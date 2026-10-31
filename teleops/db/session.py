"""异步数据库会话管理。"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import Settings
from .models import Base

log = logging.getLogger(__name__)


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url, echo=False, future=True)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)
        if url.startswith("sqlite"):
            # SQLite 默认不校验外键，ondelete="CASCADE" 会形同虚设，
            # 导致删掉工作流后运行记录/游标变成孤儿数据。这里显式打开。
            @event.listens_for(self.engine.sync_engine, "connect")
            def _fk_on(dbapi_conn: Any, _record: Any) -> None:
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(self._add_missing_columns)
        log.info("数据库已就绪")

    @staticmethod
    def _add_missing_columns(conn: Any) -> None:
        """轻量迁移：为已存在的表补上模型里新增的列。

        SQLite 只支持 ADD COLUMN，够用了——本项目不做删列/改类型这类破坏性变更。
        """
        from sqlalchemy import inspect, text
        from sqlalchemy.schema import CreateColumn

        insp = inspect(conn)
        existing_tables = set(insp.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = str(CreateColumn(col).compile(dialect=conn.engine.dialect)).strip()
                literal = _default_literal(col)
                if literal is not None and "DEFAULT" not in ddl.upper():
                    ddl += f" DEFAULT {literal}"
                elif literal is None and "NOT NULL" in ddl.upper():
                    # SQLite 不允许加"非空但无默认值"的列；退一步允许为空，
                    # 总比升级失败、整个库打不开要好。
                    ddl = re.sub(r"\s+NOT\s+NULL", "", ddl, flags=re.IGNORECASE)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
                log.info("数据库迁移：%s 新增列 %s", table.name, col.name)

    async def dispose(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise


def _default_literal(col: Any) -> str | None:
    """把列的 Python 侧默认值转成能写进 DDL 的 SQL 字面量；转不了返回 None。"""
    if col.default is None:
        return None
    arg = col.default.arg
    if callable(arg):
        for call in (lambda: arg(), lambda: arg(None)):  # 有的默认值签名带 context
            try:
                arg = call()
                break
            except TypeError:
                continue
            except Exception:
                return None
        else:
            return None
    if isinstance(arg, bool):
        return "1" if arg else "0"
    if isinstance(arg, (int, float)):
        return str(arg)
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    if isinstance(arg, (list, dict)):
        return "'" + json.dumps(arg, ensure_ascii=False).replace("'", "''") + "'"
    if isinstance(arg, dt.datetime):
        return "CURRENT_TIMESTAMP"
    return None


_db: Database | None = None


async def init_db(settings: Settings) -> Database:
    global _db
    _db = Database(settings.storage.database_url)
    await _db.create_all()
    return _db


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("数据库尚未初始化")
    return _db


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with get_db().session() as s:
        yield s
