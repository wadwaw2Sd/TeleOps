"""引擎：把配置、插件注册表、TG 客户端、执行器、调度器组装起来。"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..db.session import Database, init_db
from ..tg.client import ClientManager
from ..tg.codewatch import CodeWatcher
from .appconfig import TelegramApi, load_telegram_api, seed_from_accounts
from .registry import PluginRegistry
from .runner import RunResult, WorkflowRunner
from .scheduler import Scheduler

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.registry = PluginRegistry(self.settings.plugins.dirs)
        self.clients = ClientManager(self.settings)
        self.code_watcher = CodeWatcher(self.clients)
        self.runner = WorkflowRunner(self.settings, self.registry, self.clients)
        self.scheduler = Scheduler(self.runner)
        self.db: Database | None = None
        self.tg_api: TelegramApi = TelegramApi()
        self.started_at: float = 0.0
        self._watch_task: asyncio.Task[None] | None = None

    # ---------------------------------------------------------------- 生命周期
    async def start(self) -> None:
        self.started_at = time.time()
        self.db = await init_db(self.settings)
        self.registry.scan(force=True)
        log.info("已加载插件：%s", self.registry.stats())
        await self._purge_orphans()
        await self.reload_api_credentials(seed=True)
        self.scheduler.start()
        await self.scheduler.sync_all()
        if self.settings.plugins.auto_reload:
            self._watch_task = asyncio.create_task(self._watch_plugins())
        await self._cleanup_media()
        self._cleanup_tmp()
        log.info("TeleOps 引擎已启动")

    async def stop(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except (asyncio.CancelledError, Exception):
                pass
        self.scheduler.shutdown()
        await self.code_watcher.stop_all()
        await self.clients.close_all()
        if self.db:
            await self.db.dispose()
        log.info("TeleOps 引擎已停止")

    async def reload_api_credentials(self, *, seed: bool = False) -> TelegramApi:
        """从数据库读全局 API 凭据并推给客户端池。

        seed=True 时，若全局值为空则从老账号里提升一份（首次升级用）。
        """
        self.tg_api = await (seed_from_accounts() if seed else load_telegram_api())
        self.clients.set_global_api(self.tg_api.api_id, self.tg_api.api_hash)
        if not self.tg_api.configured:
            log.warning("尚未配置 Telegram API，请在后台「设置」里填写 api_id / api_hash")
        return self.tg_api

    # -------------------------------------------------------------- 插件热重载
    async def _watch_plugins(self) -> None:
        interval = max(2, self.settings.plugins.reload_interval)
        while True:
            try:
                await asyncio.sleep(interval)
                res = await asyncio.to_thread(self.registry.scan)
                if res.changed:
                    for n in res.added:
                        log.info("插件已加载：%s", n)
                    for n in res.updated:
                        log.info("插件已热更新：%s", n)
                    for n in res.removed:
                        log.info("插件已卸载：%s", n)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("插件监听出错：%s", e)

    # ------------------------------------------------------------------ 便捷
    async def run_workflow(
        self, workflow_id: int, *, trigger: str = "manual", dry_run: bool | None = None,
        limit: int | None = None,
    ) -> RunResult:
        return await self.runner.run(workflow_id, trigger=trigger, dry_run=dry_run, limit=limit)

    async def _purge_orphans(self) -> None:
        """清掉指向已删除工作流的残留数据（早期版本遗留）。

        工作流 id 会被后建的工作流复用，留着孤儿记录会让新工作流显示别人的历史。
        """
        from sqlalchemy import delete, select

        from ..db.models import RunLog, SeenItem, Workflow, WorkflowState, WorkflowTarget
        from ..db.session import session_scope

        try:
            async with session_scope() as s:
                alive = set((await s.execute(select(Workflow.id))).scalars().all())
                total = 0
                for model in (RunLog, SeenItem, WorkflowState, WorkflowTarget):
                    stmt = delete(model)
                    stmt = stmt.where(model.workflow_id.notin_(alive)) if alive else stmt
                    total += (await s.execute(stmt)).rowcount or 0
            if total:
                log.info("清理孤儿数据 %d 条", total)
        except Exception as e:
            log.warning("清理孤儿数据失败：%s", e)

    def _cleanup_tmp(self) -> None:
        """清掉 data/tmp 里的陈旧中间文件（导入失败、进程被杀等情况的残留）。"""
        tmp = self.settings.storage.data_dir / "tmp"
        if not tmp.exists():
            return
        cutoff = time.time() - 3600
        removed = 0
        for p in tmp.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
        if removed:
            log.info("清理临时文件 %d 个", removed)

    async def _cleanup_media(self) -> None:
        days = self.settings.storage.media_retention_days
        if days <= 0:
            return
        cutoff = time.time() - days * 86400
        removed = 0
        for p in self.settings.storage.media_dir.rglob("*"):
            if p.is_file():
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                        removed += 1
                except OSError:
                    pass
        if removed:
            log.info("清理过期媒体文件 %d 个", removed)

    def status(self) -> dict[str, Any]:
        return {
            "uptime": int(time.time() - self.started_at) if self.started_at else 0,
            "plugins": self.registry.stats(),
            "jobs": self.scheduler.jobs(),
            "plugin_dirs": [str(d) for d in self.settings.plugins.dirs],
        }


_engine: Engine | None = None


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("引擎尚未启动")
    return _engine


def set_engine(engine: Engine) -> None:
    global _engine
    _engine = engine


def ensure_plugin_dirs(settings: Settings) -> None:
    for d in settings.plugins.dirs:
        for sub in ("sources", "filters", "formatters", "sinks"):
            Path(d, sub).mkdir(parents=True, exist_ok=True)
