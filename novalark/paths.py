"""路径管理。显式初始化、显式读取。"""

from __future__ import annotations

import sys
from pathlib import Path


class Paths:
    """工作目录下的全部路径。"""

    def __init__(self, work_dir: Path) -> None:
        self.WORK_DIR = work_dir.resolve()
        self.DATA_DIR = self.WORK_DIR / "data"
        self.LOGS_DIR = self.WORK_DIR / "logs"
        self.PLUGINS_DIR = self.WORK_DIR / "plugins"
        self.PLUGIN_CONFIGS_DIR = self.WORK_DIR / "plugin_configs"
        self.CONFIG_FILE = self.WORK_DIR / "config.yaml"
        self.CACHE_DIR = self.DATA_DIR / "cache"

    def ensure_dirs(self) -> None:
        for directory in (
            self.DATA_DIR,
            self.LOGS_DIR,
            self.PLUGINS_DIR,
            self.PLUGIN_CONFIGS_DIR,
            self.CACHE_DIR,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def register_import_root(self) -> None:
        """把工作目录加进 ``sys.path``，让 ``import plugins.foo`` 可用。"""
        root = str(self.WORK_DIR)
        if root not in sys.path:
            sys.path.insert(0, root)


_paths: Paths | None = None


def init_paths(work_dir: str | Path | None = None) -> Paths:
    """初始化路径单例。重复调用返回已有实例。"""
    global _paths
    if _paths is None:
        _paths = Paths(Path(work_dir) if work_dir is not None else Path.cwd())
        _paths.ensure_dirs()
    return _paths


def get_paths() -> Paths:
    """读取路径单例。未初始化则报错。"""
    if _paths is None:
        raise RuntimeError("paths 尚未初始化，请先调用 init_paths()")
    return _paths


def reset_paths() -> None:
    """清掉单例（测试用）。"""
    global _paths
    _paths = None
