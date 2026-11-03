"""运行上下文：插件在执行期间能拿到的一切资源。"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from sqlalchemy import delete, select

from ..config import Settings
from ..db.models import Account, Delivery, SeenItem, Workflow, WorkflowState, utcnow
from ..db.session import session_scope
from ..tg.client import ClientManager
from .item import Item

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class StateStore:
    """工作流级别的键值存储，插件用来记游标（例如上次爬到哪条）。"""

    def __init__(self, workflow_id: int) -> None:
        self.workflow_id = workflow_id
        # workflow_id <= 0 表示临时/测试上下文，只在内存里保存，不落库
        self.persistent = workflow_id > 0
        self._cache: dict[str, Any] = {}

    async def get(self, key: str, default: Any = None) -> Any:
        if key in self._cache:
            return self._cache[key]
        if not self.persistent:
            return default
        async with session_scope() as s:
            row = (
                await s.execute(
                    select(WorkflowState).where(
                        WorkflowState.workflow_id == self.workflow_id, WorkflowState.key == key
                    )
                )
            ).scalar_one_or_none()
        val = row.value.get("v", default) if row else default
        self._cache[key] = val
        return val

    async def set(self, key: str, value: Any) -> None:
        self._cache[key] = value
        if not self.persistent:
            return
        async with session_scope() as s:
            row = (
                await s.execute(
                    select(WorkflowState).where(
                        WorkflowState.workflow_id == self.workflow_id, WorkflowState.key == key
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                s.add(WorkflowState(workflow_id=self.workflow_id, key=key, value={"v": value}))
            else:
                row.value = {"v": value}

    async def delete(self, key: str) -> None:
        self._cache.pop(key, None)
        if not self.persistent:
            return
        async with session_scope() as s:
            await s.execute(
                delete(WorkflowState).where(
                    WorkflowState.workflow_id == self.workflow_id, WorkflowState.key == key
                )
            )

    async def clear(self) -> None:
        self._cache.clear()
        if not self.persistent:
            return
        async with session_scope() as s:
            await s.execute(delete(WorkflowState).where(WorkflowState.workflow_id == self.workflow_id))


class RunContext:
    """一次工作流运行的上下文。"""

    def __init__(
        self,
        workflow: Workflow,
        settings: Settings,
        clients: ClientManager,
        account: Account | None,
        *,
        dry_run: bool = False,
        trigger: str = "schedule",
        registry: Any = None,
    ) -> None:
        self.workflow = workflow
        self.workflow_id = workflow.id
        self.settings = settings
        self.clients = clients
        self.account = account
        self.dry_run = dry_run
        self.trigger = trigger
        self.registry = registry
        self.state = StateStore(workflow.id)
        self.log = logging.getLogger(f"wf.{workflow.id}.{workflow.name}")
        self.traces: list[dict[str, str]] = []
        # 成功投递出去的条目；提交钩子据此推进游标
        self.sent_items: list[Item] = []
        self.on_commit: list[Callable[["RunContext"], Any]] = []
        self._http: httpx.AsyncClient | None = None
        self._temp_files: list[Path] = []
        # 本轮开始的时刻：裁剪配额时据此保护本轮刚下载、还没发出去的文件
        self.started_at = time.time()

    def add_commit_hook(self, fn: Callable[["RunContext"], Any]) -> None:
        """注册"发送成功后"回调。插件用它来安全地推进游标——发送失败的内容
        不会被跳过，下一轮还会重新处理。"""
        self.on_commit.append(fn)

    async def commit(self) -> None:
        for hook in self.on_commit:
            try:
                await hook(self)
            except Exception as e:
                self.log.warning("提交钩子出错：%s", e)

    # ------------------------------------------------------------------ HTTP
    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": DEFAULT_UA},
            )
        return self._http

    async def http_get(self, url: str, **kw: Any) -> str:
        r = await self.http.get(url, **kw)
        r.raise_for_status()
        return r.text

    async def http_json(self, url: str, **kw: Any) -> Any:
        r = await self.http.get(url, **kw)
        r.raise_for_status()
        return r.json()

    # -------------------------------------------------------------- Telegram
    async def client(self):
        """当前工作流绑定账号的 Telethon 客户端。"""
        if self.account is None:
            raise RuntimeError("该工作流未绑定 Telegram 账号")
        return await self.clients.get(self.account)

    async def client_for(self, account_id: int | None):
        if account_id is None or (self.account and account_id == self.account.id):
            return await self.client()
        async with session_scope() as s:
            acc = await s.get(Account, account_id)
        if acc is None:
            raise RuntimeError(f"账号 {account_id} 不存在")
        return await self.clients.get(acc)

    # ------------------------------------------------------------------ 存储
    def media_dir(self) -> Path:
        d = self.settings.storage.media_dir / f"wf_{self.workflow_id}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def register_temp(self, path: str | Path) -> None:
        """登记临时文件，运行结束后自动清理。"""
        self._temp_files.append(Path(path))

    def owns_path(self, path: str | Path) -> bool:
        """这个文件是不是我们自己下载到媒体目录里的缓存。

        清理只敢动媒体目录内的文件——插件可能把用户自备的素材（水印、片头）
        塞进 Item.media，删错了就找不回来了。
        """
        try:
            target = Path(path).resolve()
            root = self.settings.storage.media_dir.resolve()
        except OSError:
            return False
        return target != root and target.is_relative_to(root)

    # -------------------------------------------------------------- 占用配额
    def media_limits(self) -> tuple[int, int]:
        """(本工作流上限, 全局上限)，单位字节；0 表示不限。"""
        wf_mb = int(getattr(self.workflow, "media_quota_mb", 0) or 0)
        global_mb = int(self.settings.storage.media_max_mb or 0)
        return wf_mb * 1024 * 1024, global_mb * 1024 * 1024

    def has_media_limit(self) -> bool:
        return any(self.media_limits())

    async def media_usage(self, scope: str = "workflow") -> int:
        """媒体目录当前占用字节数。scope: workflow | global"""
        root = self.media_dir() if scope == "workflow" else self.settings.storage.media_dir
        return await asyncio.to_thread(dir_size, root)

    async def media_budget_left(self) -> int | None:
        """还能再下载多少字节；None 表示没设上限。"""
        wf_limit, g_limit = self.media_limits()
        if not (wf_limit or g_limit):
            return None
        room: list[int] = []
        if wf_limit:
            room.append(wf_limit - await self.media_usage("workflow"))
        if g_limit:
            room.append(g_limit - await self.media_usage("global"))
        return min(room)

    async def enforce_media_quota(self, *, protect_current_run: bool = True) -> tuple[int, int]:
        """把媒体目录压回配额内：最旧的先删，返回 (文件数, 字节数)。

        本轮刚下载、还没发出去的文件默认不碰——真删了这一轮就白跑了。
        """
        wf_limit, g_limit = self.media_limits()
        if not (wf_limit or g_limit):
            return 0, 0
        keep_after = self.started_at if protect_current_run else None
        files = freed = 0
        for root, limit in (
            (self.media_dir(), wf_limit),
            (self.settings.storage.media_dir, g_limit),
        ):
            if not limit:
                continue
            n, b = await asyncio.to_thread(trim_dir, root, limit, keep_after)
            files += n
            freed += b
        if files:
            self.log.warning(
                "媒体缓存超出上限，已删除最旧的 %d 个文件（释放 %s）", files, human_size(freed)
            )
        return files, freed

    async def cleanup_item_media(self, item: Item) -> int:
        """删掉该条内容在媒体目录里的本地缓存，返回删掉的文件数。

        只有"确实发出去了"才该调用（见 runner 的清理策略），否则下一轮重试时
        文件已经没了。
        """
        removed = 0
        for m in item.media:
            if not m.path:
                continue
            p = Path(m.path)
            if not self.owns_path(p):
                self.log.debug("媒体 %s 不在媒体目录内，跳过清理", p)
                continue
            try:
                if p.exists():
                    p.unlink()
                    removed += 1
            except OSError as e:
                self.log.warning("删除缓存 %s 失败：%s", p, e)
                continue
            m.extra["cache_removed"] = True
        if removed:
            self.log.info("已清理本地缓存 %d 个文件（%s）", removed, item.uid or item.preview(40))
        return removed

    # ------------------------------------------------------------------ 去重
    async def is_seen(self, key: str) -> bool:
        if self.workflow_id <= 0:
            return False
        async with session_scope() as s:
            row = (
                await s.execute(
                    select(SeenItem.id).where(
                        SeenItem.workflow_id == self.workflow_id, SeenItem.key == key
                    )
                )
            ).first()
        return row is not None

    async def mark_seen(self, key: str) -> None:
        if self.dry_run or self.workflow_id <= 0:
            return
        try:
            async with session_scope() as s:
                s.add(SeenItem(workflow_id=self.workflow_id, key=key))
        except Exception:  # 并发下唯一约束冲突可忽略
            pass

    # ------------------------------------------------------------------ 投递
    async def delivered_channels(self, item_key: str) -> set[int]:
        """这条内容已经成功发到过哪些频道（channel_id 集合）。"""
        if self.workflow_id <= 0 or not item_key:
            return set()
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(Delivery.channel_id).where(
                        Delivery.workflow_id == self.workflow_id,
                        Delivery.item_key == item_key,
                        Delivery.status == "ok",
                    )
                )
            ).scalars().all()
        return {int(c) for c in rows if c is not None}

    async def record_delivery(
        self,
        item: Item,
        target: Any,
        *,
        status: str = "ok",
        message_id: int | None = None,
        error: str = "",
    ) -> None:
        """记一条"谁发到了哪"。同一条内容同一个目标只保留一行，重试时累加次数。"""
        if self.dry_run or self.workflow_id <= 0:
            return
        key = item.fingerprint()
        try:
            async with session_scope() as s:
                row = (
                    await s.execute(
                        select(Delivery).where(
                            Delivery.workflow_id == self.workflow_id,
                            Delivery.item_key == key,
                            Delivery.channel_id == getattr(target, "channel_id", None),
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = Delivery(
                        workflow_id=self.workflow_id,
                        item_key=key,
                        channel_id=getattr(target, "channel_id", None),
                    )
                    s.add(row)
                else:
                    row.attempts = (row.attempts or 0) + 1
                row.uid = (item.uid or "")[:250]
                row.preview = item.preview(120)
                row.target_title = (getattr(target, "title", "") or "")[:120]
                row.target_peer = (getattr(target, "peer", "") or "")[:120]
                row.status = status
                row.message_id = int(message_id) if message_id else None
                row.media_count = len(item.media)
                row.error = (error or "")[:1000]
                row.sent_at = utcnow()
        except Exception as e:  # 记录失败不该影响搬运本身
            self.log.warning("写投递记录失败：%s", e)

    async def mark_cleaned(self, item_key: str, files: int) -> None:
        """把"缓存已清"落到该条内容的投递记录上。"""
        if self.dry_run or self.workflow_id <= 0 or not item_key:
            return
        try:
            async with session_scope() as s:
                rows = (
                    await s.execute(
                        select(Delivery).where(
                            Delivery.workflow_id == self.workflow_id,
                            Delivery.item_key == item_key,
                        )
                    )
                ).scalars().all()
                for row in rows:
                    row.cleaned = True
                    row.cached_files = files
        except Exception as e:
            self.log.warning("更新清理状态失败：%s", e)

    # ------------------------------------------------------------------ 追踪
    def trace(self, item: Item | None, message: str) -> None:
        self.traces.append({"uid": item.uid if item else "", "msg": message})
        self.log.debug("%s %s", item.uid if item else "-", message)

    # ------------------------------------------------------------------ 清理
    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        for p in self._temp_files:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        self._temp_files.clear()


def dir_size(root: Path) -> int:
    """目录占用字节数；目录不存在当作 0。"""
    total = 0
    if not root.exists():
        return 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def trim_dir(root: Path, limit: int, keep_after: float | None = None) -> tuple[int, int]:
    """把目录压到 limit 字节以内，最旧的文件先删。

    keep_after 之后修改过的文件（也就是本轮刚下载的）不动，哪怕压不下来——
    宁可暂时超一点，也不能把正要发的素材删掉。
    """
    if limit <= 0 or not root.exists():
        return 0, 0
    entries: list[tuple[float, int, Path]] = []
    total = 0
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
            st = p.stat()
        except OSError:
            continue
        total += st.st_size
        if keep_after is not None and st.st_mtime >= keep_after:
            continue  # 本轮的文件，不列为候选
        entries.append((st.st_mtime, st.st_size, p))
    if total <= limit:
        return 0, 0
    entries.sort(key=lambda e: e[0])  # 旧的排前面
    files = freed = 0
    for _mtime, size, p in entries:
        if total <= limit:
            break
        try:
            p.unlink()
        except OSError:
            continue
        total -= size
        freed += size
        files += 1
    return files, freed


def human_size(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < step or unit == "GB":
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} GB"


ProgressHook = Callable[[str, dict[str, Any]], None]
