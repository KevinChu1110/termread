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


class Store:
    def __init__(self, path: Path = DATA):
        self.path = path
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            raw = {}
        self.progress: dict[str, Mark] = {k: Mark(**v) for k, v in raw.get("progress", {}).items()}
        self.bookmarks: list[Mark] = [Mark(**v) for v in raw.get("bookmarks", [])]
        self.settings: dict = raw.get("settings", {})

    def save(self) -> None:
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

    def add_bookmark(self, m: Mark) -> None:
        self.bookmarks.insert(0, m)
        self.save()

    def remove_bookmark(self, m: Mark) -> None:
        self.bookmarks.remove(m)
        self.save()
