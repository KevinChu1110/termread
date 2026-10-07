"""書籤與閱讀進度，存在 ~/.local/share/termread/data.json。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "termread" / "data.json"


@dataclass
class Mark:
    url: str
    book: str
    chapter: str
    para: int  # 畫面上第一個可見段落的索引
    pct: float  # 進度 0–100（有總章數時是全書，否則是章內）
    time: float
    book_key: str = ""

    @property
    def date(self) -> str:
        t = time.localtime(self.time)
        return f"{t.tm_year}年{t.tm_mon:02d}月{t.tm_mday:02d}日"


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


class Store:
    def __init__(self, path: Path = DATA):
        self.path = path
        raw = _read(path)
        self.progress: dict[str, Mark] = {k: Mark(**v) for k, v in raw.get("progress", {}).items()}
        self.bookmarks: list[Mark] = [Mark(**v) for v in raw.get("bookmarks", [])]
        self.settings: dict = raw.get("settings", {})
        # 只記這個視窗自己動過什麼，存檔時才不會把別的視窗的改動蓋掉
        self._added: list[Mark] = []
        self._removed: list[Mark] = []
        self._touched_settings: set[str] = set()

    def save(self) -> None:
        # 可能同時開著好幾個 termread，各自存檔；先把檔案裡別的視窗寫的東西合進來，
        # 不然記憶體裡那份舊快照會把人家的新進度整個蓋掉
        disk = _read(self.path)
        for k, v in disk.get("progress", {}).items():
            m = Mark(**v)
            if k not in self.progress or m.time > self.progress[k].time:
                self.progress[k] = m
        if "bookmarks" in disk:  # 檔案還在：以檔案為準，套上這個視窗的增刪
            marks = [m for v in disk["bookmarks"] if (m := Mark(**v)) not in self._removed]
            self.bookmarks = [m for m in self._added if m not in marks] + marks
            self.bookmarks.sort(key=lambda m: m.time, reverse=True)
        self._added, self._removed = [], []
        for k, v in disk.get("settings", {}).items():
            if k not in self._touched_settings:
                self.settings[k] = v

        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "progress": {k: asdict(v) for k, v in self.progress.items()},
            "bookmarks": [asdict(m) for m in self.bookmarks],
            "settings": self.settings,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        tmp.replace(self.path)

    def recent(self) -> list[Mark]:
        return sorted(self.progress.values(), key=lambda m: m.time, reverse=True)

    def set_progress(self, m: Mark) -> None:
        self.progress[m.book_key or m.url] = m
        self.save()

    def set_setting(self, key: str, value) -> None:
        self.settings[key] = value
        self._touched_settings.add(key)
        self.save()

    def resume(self, book_key: str, site_time: float | None = None) -> Mark | None:
        """這本書本機記的進度；給了網站進度的時間就只在本機較新時回傳。"""
        m = self.progress.get(book_key) if book_key else None
        if m and (site_time is None or m.time >= site_time):
            return m
        return None

    def add_bookmark(self, m: Mark) -> None:
        self.bookmarks.insert(0, m)
        self._added.append(m)
        self.save()

    def remove_bookmark(self, m: Mark) -> None:
        self.bookmarks.remove(m)
        self._removed.append(m)
        self.save()
