"""流经管线的统一数据模型。

所有插件都读写 Item，这样源/过滤/格式化/输出可以任意组合。
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

MediaKind = Literal["photo", "video", "audio", "voice", "document", "animation", "sticker", "webpage"]


@dataclass
class Media:
    """一份媒体。三种来源方式，优先级：path > url > tg_ref。

    * path   —— 已下载到本地的文件（"下载模式"）
    * url    —— 直链，交给 Telegram 服务端拉取
    * tg_ref —— 指向某条 TG 消息的媒体，发送时直接复用 file reference（"复制模式"，不落盘、最快）
    """

    kind: MediaKind = "document"
    path: str | None = None
    url: str | None = None
    tg_ref: dict[str, Any] | None = None  # {"account_id": 1, "chat": "@x", "msg_id": 123}
    filename: str | None = None
    mime: str | None = None
    size: int | None = None
    spoiler: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.path or self.url or self.tg_ref)


@dataclass
class Item:
    """一条待发布的内容。"""

    # 源内唯一标识，用于去重（例如 "tg:@channel:12345"）
    uid: str = ""
    title: str = ""
    text: str = ""                     # 正文（纯文本或 HTML，取决于 parse_mode）
    parse_mode: str | None = "html"    # html / md / None
    url: str = ""                      # 原文链接
    author: str = ""
    source_name: str = ""              # 来源展示名，格式化插件常用
    published_at: dt.datetime | None = None
    media: list[Media] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    buttons: list[list[dict[str, str]]] = field(default_factory=list)  # [[{"text":..,"url":..}]]
    raw: dict[str, Any] = field(default_factory=dict)   # 插件私有原始数据
    meta: dict[str, Any] = field(default_factory=dict)  # 管线中传递的附加信息
    # 发送控制（格式化插件可修改）
    silent: bool = False
    no_webpage: bool = False
    schedule_at: dt.datetime | None = None
    dropped: bool = False              # 被过滤器标记丢弃
    drop_reason: str = ""

    def fingerprint(self) -> str:
        """去重指纹：有 uid 用 uid，否则对正文+媒体取哈希。"""
        if self.uid:
            return self.uid[:250]
        base = "\n".join(
            [self.text.strip(), self.url] + [m.url or m.path or "" for m in self.media]
        )
        return "h:" + hashlib.sha1(base.encode("utf-8", "ignore")).hexdigest()

    def drop(self, reason: str = "") -> "Item":
        self.dropped = True
        self.drop_reason = reason
        return self

    @property
    def has_media(self) -> bool:
        return any(not m.is_empty() for m in self.media)

    def preview(self, limit: int = 160) -> str:
        t = (self.text or self.title or "").replace("\n", " ").strip()
        if len(t) > limit:
            t = t[: limit - 1] + "…"
        if not t and self.has_media:
            t = f"[{len(self.media)} 个媒体]"
        return t

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "title": self.title,
            "text": self.text,
            "parse_mode": self.parse_mode,
            "url": self.url,
            "author": self.author,
            "source_name": self.source_name,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "media": [
                {"kind": m.kind, "path": m.path, "url": m.url, "tg_ref": m.tg_ref,
                 "filename": m.filename, "spoiler": m.spoiler}
                for m in self.media
            ],
            "tags": self.tags,
            "buttons": self.buttons,
            "silent": self.silent,
            "no_webpage": self.no_webpage,
            "dropped": self.dropped,
            "drop_reason": self.drop_reason,
            "meta": {k: v for k, v in self.meta.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))},
        }
