"""工作流执行器：信息源 → 过滤链 → 格式化链 → 输出端。"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import traceback
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from ..config import Settings
from ..db.models import Account, RunLog, Workflow, WorkflowState, utcnow
from ..db.session import session_scope
from ..tg.client import ClientManager
from .context import RunContext
from .item import Item
from .plugin import FilterPlugin, FormatterPlugin, Plugin, SinkPlugin, SourcePlugin, Target
from .registry import PluginRegistry
from .timing import DAILY_KEY, check_quota, check_window, quota_state

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    workflow_id: int
    run_id: int | None = None
    status: str = "ok"          # ok / partial / error / skipped
    fetched: int = 0
    kept: int = 0
    sent: int = 0
    failed: int = 0
    error: str = ""
    detail: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)  # dry-run 预览用

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "status": self.status,
            "fetched": self.fetched,
            "kept": self.kept,
            "sent": self.sent,
            "failed": self.failed,
            "error": self.error,
            "detail": self.detail,
            "items": self.items,
        }


class WorkflowRunner:
    def __init__(self, settings: Settings, registry: PluginRegistry, clients: ClientManager) -> None:
        self.settings = settings
        self.registry = registry
        self.clients = clients
        # 同一工作流不允许并发执行
        self._running: dict[int, asyncio.Task[Any]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def is_running(self, workflow_id: int) -> bool:
        t = self._running.get(workflow_id)
        return bool(t and not t.done())

    # ------------------------------------------------------------------ 入口
    async def run(
        self,
        workflow_id: int,
        *,
        trigger: str = "schedule",
        dry_run: bool | None = None,
        limit: int | None = None,
    ) -> RunResult:
        lock = self._locks.setdefault(workflow_id, asyncio.Lock())
        if lock.locked():
            log.info("工作流 %s 上一轮尚未结束，本轮跳过", workflow_id)
            return RunResult(workflow_id=workflow_id, status="skipped", error="上一轮仍在运行")
        async with lock:
            try:
                return await self._run_inner(workflow_id, trigger=trigger, dry_run=dry_run, limit=limit)
            except Exception as e:
                log.exception("工作流 %s 执行异常", workflow_id)
                return RunResult(
                    workflow_id=workflow_id, status="error",
                    error=f"{type(e).__name__}: {e}", detail=[{"tb": traceback.format_exc()[-2000:]}],
                )

    # ------------------------------------------------------------------ 主体
    async def _run_inner(
        self, workflow_id: int, *, trigger: str, dry_run: bool | None, limit: int | None
    ) -> RunResult:
        async with session_scope() as s:
            wf = (
                await s.execute(
                    select(Workflow)
                    .options(selectinload(Workflow.targets))
                    .where(Workflow.id == workflow_id)
                )
            ).scalar_one_or_none()
            if wf is None:
                return RunResult(workflow_id=workflow_id, status="error", error="工作流不存在")
            targets = await self._load_targets(s, wf)
            account = await s.get(Account, wf.account_id) if wf.account_id else None
            s.expunge_all()

        effective_dry = wf.dry_run if dry_run is None else dry_run

        # —— 时间闸门：定时触发时才检查，手动/试运行一律放行 ——
        quota_left: int | None = None
        if trigger == "schedule":
            gate = check_window(wf)
            if not gate.allowed:
                log.info("工作流「%s」本轮跳过：%s", wf.name, gate.reason)
                await self._note_skip(wf.id, gate.reason)
                return RunResult(workflow_id=workflow_id, status="skipped", error=gate.reason)

            qgate = check_quota(wf.daily_limit or 0, await self._load_quota(wf))
            if not qgate.allowed:
                log.info("工作流「%s」本轮跳过：%s", wf.name, qgate.reason)
                await self._note_skip(wf.id, qgate.reason)
                return RunResult(workflow_id=workflow_id, status="skipped", error=qgate.reason)
            quota_left = qgate.remaining

        result = RunResult(workflow_id=workflow_id)
        run_id = await self._open_run_log(wf.id, trigger) if trigger != "test" else None
        result.run_id = run_id

        ctx = RunContext(
            wf, self.settings, self.clients, account,
            dry_run=effective_dry, trigger=trigger, registry=self.registry,
        )
        plugins: list[Plugin] = []
        try:
            # ---- 构建插件实例 ----
            source = self._build(wf.source_plugin, wf.source_config, SourcePlugin)
            filters = [
                self._build(step["plugin"], step.get("config"), FilterPlugin)
                for step in (wf.filters or []) if step.get("enabled", True)
            ]
            formatters = [
                self._build(step["plugin"], step.get("config"), FormatterPlugin)
                for step in (wf.formatters or []) if step.get("enabled", True)
            ]
            sink = self._build(wf.sink_plugin or "tg_send", wf.sink_config, SinkPlugin)
            plugins = [source, *filters, *formatters, sink]

            for p in plugins:
                await p.setup(ctx)

            # ---- 采集 ----
            items = await source.fetch(ctx)
            result.fetched = len(items)
            ctx.log.info("采集到 %d 条", len(items))

            # ---- 去重（内置，先于自定义过滤，省掉后续开销）----
            if wf.dedup:
                items = await self._dedup(items, ctx)

            # ---- 过滤链 ----
            for f in filters:
                before = len(items)
                items = [i for i in await f.apply(items, ctx) if not i.dropped]
                ctx.log.debug("过滤器 %s：%d -> %d", f.name, before, len(items))

            # ---- 限量：单轮上限与今日剩余配额取小 ----
            cap = limit or wf.max_items_per_run or self.settings.runtime.max_items_per_run
            if quota_left is not None:
                cap = min(cap, quota_left) if cap else quota_left
            if cap and len(items) > cap:
                ctx.log.info("本轮限量 %d 条（共 %d 条待发）", cap, len(items))
                items = items[:cap]
            result.kept = len(items)

            # ---- 格式化链 ----
            formatted: list[Item] = []
            for it in items:
                try:
                    for fm in formatters:
                        it = await fm.format(it, ctx)
                        if it is None or it.dropped:
                            break
                    if it is not None and not it.dropped:
                        formatted.append(it)
                except Exception as e:
                    result.failed += 1
                    result.detail.append({"uid": it.uid if it else "", "stage": "format", "error": str(e)})
                    ctx.log.warning("格式化失败 %s：%s", getattr(it, "uid", "?"), e)
            items = formatted

            # ---- 输出 ----
            interval = wf.send_interval or self.settings.runtime.send_interval
            for idx, it in enumerate(items):
                ok_any = False
                for target in targets:
                    try:
                        if effective_dry:
                            ctx.log.info("[试运行] -> %s：%s", target.title, it.preview())
                            ok_any = True
                        else:
                            info = await sink.send(it, target, ctx)
                            ok_any = True
                            result.detail.append(
                                {"uid": it.uid, "target": target.title, "ok": True, **(info or {})}
                            )
                    except Exception as e:
                        result.failed += 1
                        result.detail.append(
                            {"uid": it.uid, "target": target.title, "ok": False, "error": f"{type(e).__name__}: {e}"}
                        )
                        ctx.log.error("发送到 %s 失败：%s", target.title, e)
                if ok_any:
                    result.sent += 1
                    ctx.sent_items.append(it)
                    if wf.dedup:
                        await ctx.mark_seen(it.fingerprint())
                if effective_dry:
                    result.items.append(it.to_dict())
                if interval and idx < len(items) - 1:
                    await asyncio.sleep(interval)

            # 提交游标：只对确实发出去的内容推进，失败的下轮重试
            if not effective_dry:
                await ctx.commit()

            if result.failed and result.sent:
                result.status = "partial"
            elif result.failed and not result.sent:
                result.status = "error"
                result.error = "全部发送失败"
            else:
                result.status = "ok"

        except Exception as e:
            result.status = "error"
            result.error = f"{type(e).__name__}: {e}"
            result.detail.append({"stage": "run", "tb": traceback.format_exc()[-2000:]})
            ctx.log.error("运行失败：%s", e)
        finally:
            for p in plugins:
                try:
                    await p.teardown(ctx)
                except Exception as e:
                    ctx.log.debug("teardown %s 出错：%s", p.name, e)
            await ctx.close()

        if result.sent and not effective_dry and (wf.daily_limit or 0) > 0:
            await self._bump_quota(wf, result.sent)

        await self._close_run_log(run_id, result)
        await self._update_workflow_stats(wf.id, result, count_sent=not effective_dry)
        return result

    # ------------------------------------------------------------- 时间闸门
    async def _load_quota(self, wf: Workflow) -> dict[str, Any]:
        async with session_scope() as s:
            row = (
                await s.execute(
                    select(WorkflowState).where(
                        WorkflowState.workflow_id == wf.id, WorkflowState.key == DAILY_KEY
                    )
                )
            ).scalar_one_or_none()
            raw = (row.value or {}).get("v") if row else None
        return quota_state(raw, wf.timezone or "")

    async def _bump_quota(self, wf: Workflow, sent: int) -> None:
        state = await self._load_quota(wf)
        state["count"] = int(state.get("count") or 0) + sent
        async with session_scope() as s:
            row = (
                await s.execute(
                    select(WorkflowState).where(
                        WorkflowState.workflow_id == wf.id, WorkflowState.key == DAILY_KEY
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                s.add(WorkflowState(workflow_id=wf.id, key=DAILY_KEY, value={"v": state}))
            else:
                row.value = {"v": state}

    async def _note_skip(self, workflow_id: int, reason: str) -> None:
        """跳过不写运行记录（否则日志会被刷屏），只更新一行状态说明。"""
        async with session_scope() as s:
            wf = await s.get(Workflow, workflow_id)
            if wf is not None:
                wf.last_note = reason[:250]

    # ------------------------------------------------------------- 辅助方法
    def _build(self, name: str, config: dict[str, Any] | None, base: type[Plugin]) -> Plugin:
        cls = self.registry.cls(name)
        if not issubclass(cls, base):
            raise TypeError(f"插件 {name} 不是 {base.plugin_type} 类型")
        errors = cls.validate_config(cls.merge_defaults(config or {}))
        if errors:
            raise ValueError(f"插件 {name} 配置有误：{'；'.join(errors)}")
        return cls(config or {})

    async def _load_targets(self, s: Any, wf: Workflow) -> list[Target]:
        from ..db.models import Channel, WorkflowTarget

        rows = (
            await s.execute(
                select(WorkflowTarget, Channel)
                .join(Channel, Channel.id == WorkflowTarget.channel_id)
                .where(WorkflowTarget.workflow_id == wf.id)
                .order_by(WorkflowTarget.position)
            )
        ).all()
        out: list[Target] = []
        for wt, ch in rows:
            if not wt.enabled or not ch.enabled:
                continue
            out.append(
                Target(
                    channel_id=ch.id,
                    title=ch.title,
                    peer=ch.peer,
                    account_id=ch.account_id or wf.account_id,
                    overrides=wt.overrides or {},
                )
            )
        return out

    async def _dedup(self, items: list[Item], ctx: RunContext) -> list[Item]:
        out: list[Item] = []
        batch_seen: set[str] = set()
        for it in items:
            fp = it.fingerprint()
            if fp in batch_seen:
                continue
            if await ctx.is_seen(fp):
                ctx.trace(it, "已发送过，跳过")
                continue
            batch_seen.add(fp)
            out.append(it)
        if len(out) != len(items):
            ctx.log.info("去重：%d -> %d", len(items), len(out))
        return out

    async def _open_run_log(self, workflow_id: int, trigger: str) -> int:
        async with session_scope() as s:
            row = RunLog(workflow_id=workflow_id, trigger=trigger, status="running")
            s.add(row)
            await s.flush()
            return row.id

    async def _close_run_log(self, run_id: int | None, result: RunResult) -> None:
        if run_id is None:
            return
        async with session_scope() as s:
            row = await s.get(RunLog, run_id)
            if row is None:
                return
            row.finished_at = utcnow()
            row.status = result.status
            row.fetched = result.fetched
            row.kept = result.kept
            row.sent = result.sent
            row.failed = result.failed
            row.error = result.error[:2000]
            row.detail = result.detail[-50:]
            # 保留条数上限
            keep = self.settings.runtime.run_log_keep
            ids = (
                await s.execute(
                    select(RunLog.id)
                    .where(RunLog.workflow_id == row.workflow_id)
                    .order_by(RunLog.id.desc())
                    .offset(keep)
                )
            ).scalars().all()
            if ids:
                await s.execute(delete(RunLog).where(RunLog.id.in_(ids)))

    async def _update_workflow_stats(
        self, workflow_id: int, result: RunResult, *, count_sent: bool = True
    ) -> None:
        async with session_scope() as s:
            wf = await s.get(Workflow, workflow_id)
            if wf is None:
                return
            wf.last_run_at = utcnow()
            wf.last_status = result.status
            wf.last_note = result.error[:250] if result.error else ""
            wf.total_runs = (wf.total_runs or 0) + 1
            if count_sent:  # 试运行没有真的发出去，不计入累计
                wf.total_sent = (wf.total_sent or 0) + result.sent


def humanize(delta: dt.timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs} 秒"
    if secs < 3600:
        return f"{secs // 60} 分钟"
    if secs < 86400:
        return f"{secs // 3600} 小时"
    return f"{secs // 86400} 天"
