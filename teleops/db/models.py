"""数据库模型。

实体关系：
    Account  1 ── n  Channel
    Workflow n ── n  Channel   （通过 WorkflowTarget，一个频道可挂载多个工作流）
    Workflow 1 ── n  RunLog / SeenItem / Delivery / WorkflowState
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Account(Base):
    """一个 Telegram 账号（用户账号或 Bot）。"""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    api_id: Mapped[int] = mapped_column(Integer)
    api_hash: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(32), default="")
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    bot_token: Mapped[str] = mapped_column(String(128), default="")
    proxy: Mapped[str] = mapped_column(String(255), default="")  # 形如 socks5://host:port
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="logged_out")
    me: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    channels: Mapped[list["Channel"]] = relationship(back_populates="account")

    @property
    def session_name(self) -> str:
        return f"acc_{self.id}"


class Channel(Base):
    """被运营的频道/群组。既可作为输出端，也可在插件里作为信息源。"""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    peer: Mapped[str] = mapped_column(String(128))  # @username / -100xxxx / t.me/xxx
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="channel")  # channel/group/user
    note: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    resolved: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # 缓存 id/username/title
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    account: Mapped[Account | None] = relationship(back_populates="channels")
    targets: Mapped[list["WorkflowTarget"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class Workflow(Base):
    """工作流 = 信息源 + 过滤链 + 格式化链 + 输出端 + 调度。"""

    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    source_plugin: Mapped[str] = mapped_column(String(64))
    source_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # [{"plugin": "keyword", "config": {...}, "enabled": true}, ...]
    filters: Mapped[list[Any]] = mapped_column(JSON, default=list)
    formatters: Mapped[list[Any]] = mapped_column(JSON, default=list)
    sink_plugin: Mapped[str] = mapped_column(String(64), default="tg_send")
    sink_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # 调度：interval(秒，支持 "300-900" 区间随机) / cron(表达式) / manual
    schedule_type: Mapped[str] = mapped_column(String(16), default="interval")
    schedule_value: Mapped[str] = mapped_column(String(128), default="600")
    jitter: Mapped[int] = mapped_column(Integer, default=0)  # 额外随机抖动秒数

    # —— 什么时候允许搬运 ——
    # "09:00-23:00"；留空表示全天；支持跨夜写法 "22:00-06:00"
    active_hours: Mapped[str] = mapped_column(String(32), default="")
    # 允许运行的星期，1=周一 … 7=周日；空表示每天
    active_days: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 每天最多发布条数，0 表示不限
    daily_limit: Mapped[int] = mapped_column(Integer, default=0)
    timezone: Mapped[str] = mapped_column(String(48), default="")  # 空=服务器本地时区

    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    max_items_per_run: Mapped[int] = mapped_column(Integer, default=5)
    send_interval: Mapped[float] = mapped_column(Float, default=3.0)
    dedup: Mapped[bool] = mapped_column(Boolean, default=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    # 本地媒体缓存的清理策略；空=跟随全局 storage.cleanup_after_send
    #   on_success 全部目标都发成功后删 / always 发完就删 / never 保留（按全局天数清理）
    media_cleanup: Mapped[str] = mapped_column(String(16), default="")
    # 本工作流媒体目录的最大占用（MB），0 表示不限；超了先删最旧的
    media_quota_mb: Mapped[int] = mapped_column(Integer, default=0)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    last_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="never")
    last_note: Mapped[str] = mapped_column(String(255), default="")  # 例如"不在活跃时段"
    next_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    total_sent: Mapped[int] = mapped_column(Integer, default=0)
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    targets: Mapped[list["WorkflowTarget"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", order_by="WorkflowTarget.position"
    )


class WorkflowTarget(Base):
    """工作流 -> 频道 的挂载关系（一个频道可被多个工作流挂载）。"""

    __tablename__ = "workflow_targets"
    __table_args__ = (UniqueConstraint("workflow_id", "channel_id", name="uq_wf_channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    workflow: Mapped[Workflow] = relationship(back_populates="targets")
    channel: Mapped[Channel] = relationship(back_populates="targets")


class RunLog(Base):
    """一次工作流运行的结果摘要。"""

    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/ok/partial/error
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule/manual/test
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    kept: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    cleaned: Mapped[int] = mapped_column(Integer, default=0)  # 本轮删掉的本地缓存文件数
    error: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[list[Any]] = mapped_column(JSON, default=list)


class SeenItem(Base):
    """去重表：记录某工作流已经处理过的条目指纹。"""

    __tablename__ = "seen_items"
    __table_args__ = (UniqueConstraint("workflow_id", "key", name="uq_seen"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Delivery(Base):
    """投递记录：某条内容发到过哪个目标，以及本地缓存清掉了几个文件。

    和 SeenItem 的分工：SeenItem 只记"这条处理过"（工作流级），Delivery 精确到
    每个目标。多目标里只有部分成功时，靠它能认出哪个目标已经收到过，补发时
    不会重复打扰已发成功的频道。
    """

    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("workflow_id", "item_key", "channel_id", name="uq_delivery"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    item_key: Mapped[str] = mapped_column(String(255), index=True)  # Item.fingerprint()
    uid: Mapped[str] = mapped_column(String(255), default="")       # 源内标识，给人看
    preview: Mapped[str] = mapped_column(String(255), default="")   # 正文摘要，方便回溯
    # 频道被删掉后记录仍要留着，所以置空而不是级联删除
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    target_title: Mapped[str] = mapped_column(String(128), default="")
    target_peer: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")   # ok / failed
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_count: Mapped[int] = mapped_column(Integer, default=0)
    cached_files: Mapped[int] = mapped_column(Integer, default=0)   # 删掉的本地文件数
    cleaned: Mapped[bool] = mapped_column(Boolean, default=False)   # 本地缓存已清理
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class WorkflowState(Base):
    """工作流的游标/自定义持久化状态（插件通过 ctx.state 读写）。"""

    __tablename__ = "workflow_state"
    __table_args__ = (UniqueConstraint("workflow_id", "key", name="uq_state"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PluginRecord(Base):
    """插件启用状态与默认配置（插件本体在文件系统中，这里只存元数据）。"""

    __tablename__ = "plugins"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    default_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
