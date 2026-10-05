"""依網址挑選資料來源。

內建只有 GenericSource（任何網頁）。網站專用的來源用外掛加入：
  - 外掛套件：在 entry point 群組 "termread.sources" 註冊類別，用 termread plugin install 安裝
  - 單檔外掛：放在 ~/.config/termread/sources/*.py，用 @register 標記
詳見 README。
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
import webbrowser
from importlib.metadata import entry_points

from ..models import Chapter, ReviewPage, ShelfBook, TocEntry
from ..net import CONFIG_DIR

log = logging.getLogger(__name__)
PLUGIN_DIR = CONFIG_DIR / "sources"


class Source:
    name = "web"
    has_reviews = False

    @staticmethod
    def matches(url: str) -> bool:
        raise NotImplementedError

    def chapter(self, url: str) -> Chapter:
        raise NotImplementedError

    def toc(self, ch: Chapter) -> list[TocEntry]:
        return []

    def review_counts(self, ch: Chapter) -> dict[int, int]:
        """段落編號 → 評論數；0 代表整章。"""
        return {}

    def reviews(self, ch: Chapter, pid: int, page: int) -> ReviewPage:
        return ReviewPage([], 0, True)

    def shelf(self) -> list[ShelfBook]:
        """網站上的書架／閱讀紀錄，會顯示在「書架」分頁。"""
        return []

    def open_in_browser(self, url: str) -> None:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            webbrowser.open(url)

    def close(self) -> None:
        """程式結束時呼叫，用來釋放瀏覽器等資源。"""


_registry: list[type[Source]] = []
_instances: dict[type[Source], Source] = {}
_loaded = False


def register(cls: type[Source]) -> type[Source]:
    """外掛用的裝飾器：註冊一個 Source 子類別（先註冊的先比對網址）。"""
    if cls not in _registry:
        _registry.append(cls)
    return cls


def _load_plugins() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for f in sorted(PLUGIN_DIR.glob("*.py")) if PLUGIN_DIR.is_dir() else []:
        try:
            spec = importlib.util.spec_from_file_location(f"termread_plugin_{f.stem}", f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001 — 單一外掛壞掉不影響其他功能
            log.exception("載入外掛失敗：%s", f)
    from ..plugins import activate

    activate()  # 用 termread plugin install 裝的外掛
    for ep in entry_points(group="termread.sources"):
        try:
            register(ep.load())
        except Exception:  # noqa: BLE001
            log.exception("載入外掛失敗：%s", ep.name)


def _instance(cls: type[Source]) -> Source:
    if cls not in _instances:
        _instances[cls] = cls()
    return _instances[cls]


def source_for(url: str) -> Source:
    from .generic import GenericSource

    _load_plugins()
    for cls in _registry:
        if cls.matches(url):
            return _instance(cls)
    return _instance(GenericSource)


def plugin_sources() -> list[Source]:
    _load_plugins()
    return [_instance(cls) for cls in _registry]


def close_all() -> None:
    for src in _instances.values():
        try:
            src.close()
        except Exception:  # noqa: BLE001
            pass
