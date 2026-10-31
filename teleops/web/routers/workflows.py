"""工作流管理：增删改查、挂载频道、手动运行、试运行、运行记录。"""
from __future__ import annotations

from typing import Any

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from ...core.scheduler import parse_interval_spec
from ...core.timing import describe_days, describe_window, parse_window
from ...db.models import Channel, RunLog, SeenItem, Workflow, WorkflowState, WorkflowTarget
from ..deps import DbDep, EngineDep, ok
from ..schemas import RunIn, WorkflowIn, WorkflowPatch

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

_SCALARS = (
    "name", "description", "enabled", "source_plugin", "source_config",
    "sink_plugin", "sink_config", "schedule_type", "schedule_value", "jitter",
    "active_hours", "active_days", "daily_limit", "timezone",
    "account_id", "max_items_per_run", "send_interval", "dedup", "dry_run", "options",
)


def _dump(wf: Workflow, channels: dict[int, Channel], engine=None) -> dict[str, Any]:
    next_run = engine.scheduler.next_run(wf.id) if engine else None
    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "enabled": wf.enabled,
        "source_plugin": wf.source_plugin,
        "source_config": wf.source_config or {},
        "filters": wf.filters or [],
        "formatters": wf.formatters or [],
        "sink_plugin": wf.sink_plugin,
        "sink_config": wf.sink_config or {},
        "schedule_type": wf.schedule_type,
        "schedule_value": wf.schedule_value,
        "jitter": wf.jitter,
        "active_hours": wf.active_hours or "",
        "active_days": wf.active_days or [],
        "daily_limit": wf.daily_limit or 0,
        "timezone": wf.timezone or "",
        "window_label": describe_window(wf.active_hours or ""),
        "days_label": describe_days(wf.active_days or []),
        "account_id": wf.account_id,
        "max_items_per_run": wf.max_items_per_run,
        "send_interval": wf.send_interval,
        "dedup": wf.dedup,
        "dry_run": wf.dry_run,
        "options": wf.options or {},
        "last_run_at": wf.last_run_at.isoformat() if wf.last_run_at else None,
        "last_status": wf.last_status,
        "last_note": wf.last_note or "",
        "next_run_at": next_run.isoformat() if next_run else (
            wf.next_run_at.isoformat() if wf.next_run_at else None
        ),
        "total_sent": wf.total_sent,
        "total_runs": wf.total_runs or 0,
        "running": engine.runner.is_running(wf.id) if engine else False,
        "targets": [
            {
                "channel_id": t.channel_id,
                "enabled": t.enabled,
                "overrides": t.overrides or {},
                "title": channels[t.channel_id].title if t.channel_id in channels else "(已删除)",
                "peer": channels[t.channel_id].peer if t.channel_id in channels else "",
            }
            for t in sorted(wf.targets, key=lambda x: x.position)
        ],
    }


async def _channel_map(db) -> dict[int, Channel]:
    rows = (await db.execute(select(Channel))).scalars().all()
    return {c.id: c for c in rows}


@router.get("")
async def list_workflows(db: DbDep, engine: EngineDep):
    rows = (
        await db.execute(select(Workflow).options(selectinload(Workflow.targets)).order_by(Workflow.id))
    ).scalars().all()
    chans = await _channel_map(db)
    return ok([_dump(wf, chans, engine) for wf in rows])


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: int, db: DbDep, engine: EngineDep):
    wf = await _need(db, workflow_id)
    return ok(_dump(wf, await _channel_map(db), engine))


@router.post("")
async def create_workflow(payload: WorkflowIn, db: DbDep, engine: EngineDep):
    _validate(engine, payload)
    wf = Workflow(
        **{k: getattr(payload, k) for k in _SCALARS},
        filters=[s.model_dump() for s in payload.filters],
        formatters=[s.model_dump() for s in payload.formatters],
    )
    db.add(wf)
    await db.flush()
    await _set_targets(db, wf, [t.model_dump() for t in payload.targets])
    await db.flush()
    await db.refresh(wf, ["targets"])
    await db.commit()  # 调度器在另一个连接里读库，必须先提交
    await engine.scheduler.refresh(wf.id)
    return ok(_dump(wf, await _channel_map(db), engine))


@router.patch("/{workflow_id}")
async def update_workflow(workflow_id: int, payload: WorkflowPatch, db: DbDep, engine: EngineDep):
    wf = await _need(db, workflow_id)
    data = payload.model_dump(exclude_unset=True)

    for k in _SCALARS:
        if k in data and data[k] is not None:
            setattr(wf, k, data[k])
    if data.get("filters") is not None:
        wf.filters = [s if isinstance(s, dict) else s.model_dump() for s in data["filters"]]
    if data.get("formatters") is not None:
        wf.formatters = [s if isinstance(s, dict) else s.model_dump() for s in data["formatters"]]
    if data.get("targets") is not None:
        await _set_targets(db, wf, data["targets"])

    # 合并后再校验，这样只改调度不带插件字段的请求也能查出问题
    _validate(engine, wf)

    await db.flush()
    await db.refresh(wf, ["targets"])
    await db.commit()
    await engine.scheduler.refresh(wf.id)
    return ok(_dump(wf, await _channel_map(db), engine))


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: int, db: DbDep, engine: EngineDep):
    wf = await _need(db, workflow_id)
    engine.scheduler.remove(workflow_id)
    # 显式清干净附属数据：id 会被后建的工作流复用，留下孤儿记录会串档
    for model in (RunLog, SeenItem, WorkflowState, WorkflowTarget):
        await db.execute(delete(model).where(model.workflow_id == workflow_id))
    await db.delete(wf)
    return ok()


@router.post("/{workflow_id}/toggle")
async def toggle(workflow_id: int, db: DbDep, engine: EngineDep):
    wf = await _need(db, workflow_id)
    wf.enabled = not wf.enabled
    await db.commit()
    await engine.scheduler.refresh(workflow_id)
    return ok({"enabled": wf.enabled})


@router.post("/{workflow_id}/run")
async def run_now(workflow_id: int, payload: RunIn, db: DbDep, engine: EngineDep):
    await _need(db, workflow_id)
    await db.commit()  # 释放连接，运行可能耗时较久
    result = await engine.run_workflow(
        workflow_id,
        trigger="test" if payload.dry_run else "manual",
        dry_run=payload.dry_run,
        limit=payload.limit,
    )
    return ok(result.as_dict())


@router.get("/{workflow_id}/runs")
async def runs(workflow_id: int, db: DbDep, limit: int = 30):
    rows = (
        await db.execute(
            select(RunLog).where(RunLog.workflow_id == workflow_id)
            .order_by(RunLog.id.desc()).limit(limit)
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "status": r.status,
                "trigger": r.trigger,
                "fetched": r.fetched,
                "kept": r.kept,
                "sent": r.sent,
                "failed": r.failed,
                "error": r.error,
                "detail": r.detail or [],
            }
            for r in rows
        ]
    )


@router.post("/{workflow_id}/reset")
async def reset_state(workflow_id: int, db: DbDep, mode: str = "all"):
    """重置游标 / 去重记录。mode: all | cursor | dedup"""
    await _need(db, workflow_id)
    if mode in ("all", "cursor"):
        await db.execute(delete(WorkflowState).where(WorkflowState.workflow_id == workflow_id))
    if mode in ("all", "dedup"):
        await db.execute(delete(SeenItem).where(SeenItem.workflow_id == workflow_id))
    return ok({"mode": mode})


@router.get("/{workflow_id}/state")
async def get_state(workflow_id: int, db: DbDep):
    rows = (
        await db.execute(select(WorkflowState).where(WorkflowState.workflow_id == workflow_id))
    ).scalars().all()
    seen = (
        await db.execute(select(SeenItem.id).where(SeenItem.workflow_id == workflow_id))
    ).all()
    return ok({
        "state": {r.key: (r.value or {}).get("v") for r in rows},
        "seen_count": len(seen),
    })


# ------------------------------------------------------------------- 内部
async def _need(db, workflow_id: int) -> Workflow:
    wf = (
        await db.execute(
            select(Workflow).options(selectinload(Workflow.targets)).where(Workflow.id == workflow_id)
        )
    ).scalar_one_or_none()
    if wf is None:
        raise HTTPException(404, "工作流不存在")
    return wf


async def _set_targets(db, wf: Workflow, targets: list[dict[str, Any]]) -> None:
    await db.execute(delete(WorkflowTarget).where(WorkflowTarget.workflow_id == wf.id))
    for i, t in enumerate(targets or []):
        db.add(
            WorkflowTarget(
                workflow_id=wf.id,
                channel_id=int(t["channel_id"]),
                position=i,
                enabled=bool(t.get("enabled", True)),
                overrides=t.get("overrides") or {},
            )
        )


def _validate(engine, payload) -> None:
    """校验插件与调度配置，避免存下跑不起来的工作流。"""
    # —— 调度时间 ——
    stype = getattr(payload, "schedule_type", None) or "interval"
    value = (getattr(payload, "schedule_value", "") or "").strip()
    try:
        if stype == "cron":
            CronTrigger.from_crontab(value)
        elif stype == "interval":
            base, span = parse_interval_spec(value)
            if base - span < 10:
                raise ValueError("间隔不能小于 10 秒")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"调度配置有误：{e}")

    try:
        parse_window(getattr(payload, "active_hours", "") or "")
    except ValueError as e:
        raise HTTPException(400, str(e))

    for d in getattr(payload, "active_days", None) or []:
        if int(d) not in range(1, 8):
            raise HTTPException(400, f"运行星期取值应为 1–7，收到 {d}")

    # —— 插件与其配置 ——
    reg = engine.registry
    checks: list[tuple[str, dict[str, Any]]] = []
    if getattr(payload, "source_plugin", None):
        checks.append((payload.source_plugin, getattr(payload, "source_config", None) or {}))
    if getattr(payload, "sink_plugin", None):
        checks.append((payload.sink_plugin, getattr(payload, "sink_config", None) or {}))
    for step in (getattr(payload, "filters", None) or []) + (getattr(payload, "formatters", None) or []):
        # 创建时是 pydantic 对象，更新时是已落库的 dict，两种都要吃得下
        name = step["plugin"] if isinstance(step, dict) else step.plugin
        cfg = (step.get("config") if isinstance(step, dict) else step.config) or {}
        checks.append((name, cfg))

    for name, cfg in checks:
        lp = reg.get(name)
        if lp is None:
            raise HTTPException(400, f"插件 {name} 不存在（可能未加载或已删除）")
        errs = lp.cls.validate_config(lp.cls.merge_defaults(cfg))
        if errs:
            raise HTTPException(400, f"[{lp.meta.display_name}] " + "；".join(errs))
