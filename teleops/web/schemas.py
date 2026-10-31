"""API 请求/响应模型。"""
from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


# ------------------------------------------------------------------ 账号
class AccountIn(BaseModel):
    name: str
    # api_id / api_hash 统一在「设置」里配，这里保留只为兼容老的调用方
    api_id: int = 0
    api_hash: str = ""
    phone: str = ""
    is_bot: bool = False
    bot_token: str = ""
    proxy: str = ""
    enabled: bool = True


class AccountPatch(BaseModel):
    name: str | None = None
    api_id: int | None = None
    api_hash: str | None = None
    phone: str | None = None
    is_bot: bool | None = None
    bot_token: str | None = None
    proxy: str | None = None
    enabled: bool | None = None


class TelegramApiIn(BaseModel):
    api_id: int
    api_hash: str = ""      # 留空表示沿用原值


class CodeWatchIn(BaseModel):
    seconds: int = 180


class CodeIn(BaseModel):
    code: str


class PasswordIn(BaseModel):
    password: str


# ------------------------------------------------------------------ 频道
class ChannelIn(BaseModel):
    title: str
    peer: str
    account_id: int | None = None
    kind: str = "channel"
    note: str = ""
    enabled: bool = True


class ChannelPatch(BaseModel):
    title: str | None = None
    peer: str | None = None
    account_id: int | None = None
    kind: str | None = None
    note: str | None = None
    enabled: bool | None = None


# ------------------------------------------------------------------ 工作流
class PipelineStep(BaseModel):
    plugin: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class TargetIn(BaseModel):
    channel_id: int
    enabled: bool = True
    overrides: dict[str, Any] = Field(default_factory=dict)


class WorkflowIn(BaseModel):
    name: str
    description: str = ""
    enabled: bool = False
    source_plugin: str
    source_config: dict[str, Any] = Field(default_factory=dict)
    filters: list[PipelineStep] = Field(default_factory=list)
    formatters: list[PipelineStep] = Field(default_factory=list)
    sink_plugin: str = "tg_send"
    sink_config: dict[str, Any] = Field(default_factory=dict)
    schedule_type: str = "interval"
    schedule_value: str = "600"
    jitter: int = 0
    active_hours: str = ""
    active_days: list[int] = Field(default_factory=list)
    daily_limit: int = 0
    timezone: str = ""
    account_id: int | None = None
    max_items_per_run: int = 5
    send_interval: float = 3.0
    dedup: bool = True
    dry_run: bool = False
    options: dict[str, Any] = Field(default_factory=dict)
    targets: list[TargetIn] = Field(default_factory=list)


class WorkflowPatch(WorkflowIn):
    name: str | None = None  # type: ignore[assignment]
    source_plugin: str | None = None  # type: ignore[assignment]
    targets: list[TargetIn] | None = None  # type: ignore[assignment]
    filters: list[PipelineStep] | None = None  # type: ignore[assignment]
    formatters: list[PipelineStep] | None = None  # type: ignore[assignment]


class RunIn(BaseModel):
    dry_run: bool = False
    limit: int | None = None


# ------------------------------------------------------------------ 插件
class PluginToggle(BaseModel):
    enabled: bool


class PluginSource(BaseModel):
    code: str


class PluginTestIn(BaseModel):
    plugin: str
    config: dict[str, Any] = Field(default_factory=dict)
    account_id: int | None = None


def dt_str(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v else None
