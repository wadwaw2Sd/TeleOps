"""调度器：把启用的工作流注册成 APScheduler 任务。"""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from ..db.models import Workflow
from ..db.session import session_scope
from .runner import WorkflowRunner

log = logging.getLogger(__name__)

JOB_PREFIX = "wf:"


def job_id(workflow_id: int) -> str:
    return f"{JOB_PREFIX}{workflow_id}"


def build_trigger(wf: Workflow):
    """根据工作流配置生成 APScheduler trigger；manual 返回 None。"""
    stype = (wf.schedule_type or "interval").lower()
    value = (wf.schedule_value or "").strip()
    extra_jitter = int(wf.jitter or 0)

    if stype == "manual":
        return None
    if stype == "cron":
        if not value:
            raise ValueError("cron 表达式为空")
        return CronTrigger.from_crontab(value, timezone=None)

    # interval：支持 "600"、"10m"、"2h"、"1d"，以及区间随机 "5m-15m"
    base, span = parse_interval_spec(value)
    if base - span < 10:
        raise ValueError("间隔不能小于 10 秒")
    jitter = (span + extra_jitter) or None
    return IntervalTrigger(seconds=base, jitter=jitter)


def parse_interval_spec(value: str) -> tuple[int, int]:
    """解析间隔配置，返回 (基准秒数, 抖动半径)。

    "600"      -> (600, 0)      每 10 分钟
    "10m"      -> (600, 0)
    "5m-15m"   -> (600, 300)    5～15 分钟之间随机
    """
    v = (value or "600").strip().lower()
    parts = [p for p in re.split(r"\s*[-–~至]\s*", v) if p]
    if len(parts) == 2:
        lo, hi = parse_interval(parts[0]), parse_interval(parts[1])
        if lo > hi:
            lo, hi = hi, lo
        return (lo + hi) // 2, (hi - lo) // 2
    return parse_interval(v), 0


def parse_interval(value: str) -> int:
    """把 "600" / "10m" / "2h" / "1d" 换算成秒。"""
    v = (value or "600").strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if v and v[-1] in units:
        try:
            return int(float(v[:-1]) * units[v[-1]])
        except ValueError as e:
            raise ValueError(f"无法解析间隔：{value}") from e
    try:
        return int(float(v))
    except ValueError as e:
        raise ValueError(f"无法解析间隔：{value}") from e


class Scheduler:
    def __init__(self, runner: WorkflowRunner) -> None:
        self.runner = runner
        self.sched = AsyncIOScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}
        )

    def start(self) -> None:
        if not self.sched.running:
            self.sched.start()
            log.info("调度器已启动")

    def shutdown(self) -> None:
        if self.sched.running:
            self.sched.shutdown(wait=False)
            log.info("调度器已停止")

    # --------------------------------------------------------------- 任务同步
    async def sync_all(self) -> dict[str, Any]:
        """按数据库当前状态重建所有任务。"""
        async with session_scope() as s:
            workflows = (await s.execute(select(Workflow))).scalars().all()
            s.expunge_all()

        wanted: set[str] = set()
        errors: list[str] = []
        for wf in workflows:
            if not wf.enabled:
                continue
            try:
                if self.add_or_update(wf):
                    wanted.add(job_id(wf.id))
            except Exception as e:
                errors.append(f"{wf.name}: {e}")
                log.error("为工作流 %s 建立调度失败：%s", wf.name, e)

        for job in self.sched.get_jobs():
            if job.id.startswith(JOB_PREFIX) and job.id not in wanted:
                self.remove(int(job.id[len(JOB_PREFIX):]))

        await self._persist_next_run()
        return {"jobs": len(wanted), "errors": errors}

    def add_or_update(self, wf: Workflow) -> bool:
        trigger = build_trigger(wf)
        if trigger is None:
            self.remove(wf.id)
            return False
        self.sched.add_job(
            self._fire,
            trigger=trigger,
            id=job_id(wf.id),
            args=[wf.id],
            name=wf.name,
            replace_existing=True,
        )
        log.info("已调度工作流「%s」（%s %s）", wf.name, wf.schedule_type, wf.schedule_value)
        return True

    def remove(self, workflow_id: int) -> None:
        try:
            self.sched.remove_job(job_id(workflow_id))
        except JobLookupError:
            pass

    async def refresh(self, workflow_id: int) -> None:
        async with session_scope() as s:
            wf = await s.get(Workflow, workflow_id)
            if wf is None:
                self.remove(workflow_id)
                return
            s.expunge(wf)
        if wf.enabled:
            try:
                self.add_or_update(wf)
            except Exception as e:
                log.error("刷新工作流 %s 调度失败：%s", wf.name, e)
                self.remove(workflow_id)
        else:
            self.remove(workflow_id)
        await self._persist_next_run()

    # ------------------------------------------------------------------ 执行
    async def _fire(self, workflow_id: int) -> None:
        await self.runner.run(workflow_id, trigger="schedule")
        await self._persist_next_run(workflow_id)

    # ------------------------------------------------------------------ 查询
    def next_run(self, workflow_id: int) -> dt.datetime | None:
        job = self.sched.get_job(job_id(workflow_id))
        return getattr(job, "next_run_time", None) if job else None

    def jobs(self) -> list[dict[str, Any]]:
        out = []
        for job in self.sched.get_jobs():
            if not job.id.startswith(JOB_PREFIX):
                continue
            out.append(
                {
                    "workflow_id": int(job.id[len(JOB_PREFIX):]),
                    "name": job.name,
                    "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    "trigger": str(job.trigger),
                }
            )
        return sorted(out, key=lambda j: j["next_run"] or "9999")

    async def _persist_next_run(self, workflow_id: int | None = None) -> None:
        """把下次运行时间写回数据库，方便前端直接展示。"""
        try:
            async with session_scope() as s:
                ids = [workflow_id] if workflow_id else [
                    j["workflow_id"] for j in self.jobs()
                ]
                for wid in ids:
                    wf = await s.get(Workflow, wid)
                    if wf is None:
                        continue
                    nxt = self.next_run(wid)
                    wf.next_run_at = nxt.replace(tzinfo=None) if nxt else None
        except Exception as e:
            log.debug("写入 next_run 失败：%s", e)
