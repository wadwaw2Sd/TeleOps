"""配置加载：config.yaml -> Settings。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: 代码所在目录（仓库根 / 打包后的 resources 目录），只读。
CODE_ROOT = Path(__file__).resolve().parent.parent


def _resolve_home() -> Path:
    """运行数据的根目录。

    默认与代码同目录（源码方式运行时的老行为）；桌面版把程序装在只读位置，
    通过环境变量 ``TELEOPS_HOME`` 把 ``config.yaml`` / ``data`` / ``plugins``
    指到用户可写的目录。
    """
    env = os.environ.get("TELEOPS_HOME", "").strip()
    if not env:
        return CODE_ROOT
    home = Path(env).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    return home


#: 运行数据根目录，配置里的相对路径都基于它
ROOT = _resolve_home()


def _abs(base: Path, p: str | os.PathLike[str]) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (base / path)


def parse_size_mb(value: Any) -> int:
    """把 2048 / "2048" / "2GB" / "500m" / "1.5 GB" 统一成 MB 整数。

    纯数字按 MB 理解（配置项名字里带 _mb）；认不出来的写法当成不限制，
    免得一个笔误把整个媒体目录清空。
    """
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(b|k|kb|m|mb|g|gb|t|tb)?", text)
    if not m:
        return 0
    num = float(m.group(1))
    unit = (m.group(2) or "mb").rstrip("b") or "m"
    factor = {"": 1.0, "b": 1.0 / (1024 * 1024), "k": 1.0 / 1024, "m": 1.0, "g": 1024.0, "t": 1024.0 * 1024}
    mb = int(num * factor.get(unit, 1.0))
    # 写了个不到 1MB 的上限也是想限制，别四舍五入成"不限"
    return max(1, mb) if num > 0 else 0


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8800
    auth_token: str = ""


@dataclass
class StorageConfig:
    data_dir: Path = ROOT / "data"
    database_url: str = "sqlite+aiosqlite:///data/teleops.db"
    media_dir: Path = ROOT / "data" / "media"
    media_retention_days: int = 3
    # 发送成功后立刻删掉本地媒体缓存（工作流可单独覆盖）
    cleanup_after_send: bool = True
    # 投递记录保留天数，0 表示永久保留
    delivery_keep_days: int = 30
    # 整个媒体目录的最大占用（MB），0 表示不限；超出时按最旧的文件先删
    media_max_mb: int = 0


@dataclass
class PluginConfig:
    dirs: list[Path] = field(default_factory=lambda: [ROOT / "plugins"])
    auto_reload: bool = True
    reload_interval: int = 5


@dataclass
class RuntimeConfig:
    send_interval: float = 3.0
    max_items_per_run: int = 20
    run_log_keep: int = 200
    max_flood_wait: int = 600


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Path | None = ROOT / "data" / "teleops.log"


@dataclass
class Settings:
    root: Path = ROOT
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    plugins: PluginConfig = field(default_factory=PluginConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def sessions_dir(self) -> Path:
        return self.storage.data_dir / "sessions"

    def ensure_dirs(self) -> None:
        for p in (self.storage.data_dir, self.storage.media_dir, self.sessions_dir):
            p.mkdir(parents=True, exist_ok=True)


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    """读取配置文件；文件不存在时回退到默认值。"""
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    s = Settings(raw=raw)

    srv = raw.get("server") or {}
    s.server = ServerConfig(
        host=srv.get("host", s.server.host),
        port=int(srv.get("port", s.server.port)),
        auth_token=os.environ.get("TELEOPS_TOKEN", srv.get("auth_token", "") or ""),
    )

    st = raw.get("storage") or {}
    data_dir = _abs(ROOT, st.get("data_dir", "data"))
    db_url = st.get("database_url", s.storage.database_url)
    if db_url.startswith("sqlite") and ":///" in db_url:
        prefix, _, rel = db_url.partition(":///")
        if not Path(rel).is_absolute():
            db_url = f"{prefix}:///{(ROOT / rel).as_posix()}"
    s.storage = StorageConfig(
        data_dir=data_dir,
        database_url=db_url,
        media_dir=_abs(ROOT, st.get("media_dir", data_dir / "media")),
        media_retention_days=int(st.get("media_retention_days", 3)),
        cleanup_after_send=bool(st.get("cleanup_after_send", True)),
        delivery_keep_days=int(st.get("delivery_keep_days", 30)),
        media_max_mb=parse_size_mb(st.get("media_max_mb", st.get("media_max_size", 0))),
    )

    pl = raw.get("plugins") or {}
    s.plugins = PluginConfig(
        dirs=[_abs(ROOT, d) for d in (pl.get("dirs") or ["plugins"])],
        auto_reload=bool(pl.get("auto_reload", True)),
        reload_interval=int(pl.get("reload_interval", 5)),
    )

    rt = raw.get("runtime") or {}
    s.runtime = RuntimeConfig(
        send_interval=float(rt.get("send_interval", 3.0)),
        max_items_per_run=int(rt.get("max_items_per_run", 20)),
        run_log_keep=int(rt.get("run_log_keep", 200)),
        max_flood_wait=int(rt.get("max_flood_wait", 600)),
    )

    lg = raw.get("logging") or {}
    log_file = lg.get("file", "data/teleops.log")
    s.logging = LoggingConfig(
        level=str(lg.get("level", "INFO")).upper(),
        file=_abs(ROOT, log_file) if log_file else None,
    )

    s.ensure_dirs()
    return s


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
