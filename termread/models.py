from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Para:
    text: str
    kind: str = "p"  # p / h1 / h2 / h3 / li / quote / code / table
    pid: int | None = None  # 網站段評用的段落編號，沒有就是 None


@dataclass
class Chapter:
    url: str
    title: str
    paras: list[Para]
    book: str = ""
    book_key: str = ""  # 同一本書共用的 key，用來記進度
    prev_url: str | None = None
    next_url: str | None = None
    toc_url: str | None = None
    seq: int | None = None  # 第幾章（1 起算）
    total: int | None = None
    notice: str = ""  # 例如付費章節未購買
    buy_url: str | None = None  # 未購買時，瀏覽器購買頁
    links: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class TocEntry:
    title: str
    url: str | None  # None 表示分卷標題
    vip: bool = False


@dataclass
class Review:
    user: str
    content: str
    likes: int = 0
    time: str = ""
    place: str = ""
    replies: list["Review"] = field(default_factory=list)
    more_replies: int = 0


@dataclass
class ReviewPage:
    items: list[Review]
    total: int
    end: bool


@dataclass
class ShelfBook:
    book: str
    author: str
    read_chapter: str  # 讀到哪一章
    url: str  # 讀到的那一章
    when: str  # 例如「2小時前」
    read_time: float
    latest: str = ""
    updated: bool = False
