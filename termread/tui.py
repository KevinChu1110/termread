"""全螢幕閱讀介面（Textual）。

按鍵：↑↓ 捲動｜←→ / PgUp PgDn / 空白 翻頁（翻到底自動換章）｜[ ] 上/下一章｜Home/End 章首/章尾
      Enter 目錄・書籤・書架｜b 加書籤｜Tab/Shift+Tab 切段評｜c 開關段評欄｜j/k 捲段評｜m 更多段評
      t 繁簡切換｜p 購買本章｜r 重新載入｜F11 或 z 全螢幕｜o 開網址｜q 離開
"""

from __future__ import annotations

import time
from functools import lru_cache
from urllib.parse import urlsplit

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from .models import Chapter, Para, Review, ShelfBook, TocEntry
from .sources import Source, close_all, plugin_sources, source_for
from .store import Mark, Store

# ---------- 繁簡轉換 ----------

TRAD = {"on": True}  # 預設轉成繁體，按 t 切回原文


@lru_cache(maxsize=1)
def _cc():
    import opencc

    return opencc.OpenCC("s2twp")  # 簡體 → 台灣繁體（含用語，如「信息」→「資訊」）


@lru_cache(maxsize=20000)
def _conv(s: str) -> str:
    return _cc().convert(s)


def zh(s: str) -> str:
    return _conv(s) if TRAD["on"] and s else s


def _path(url: str) -> str:
    # 只比路徑：同一章的手機版／桌面版網址只差主機名，不算換章
    return urlsplit(url).path.rstrip("/")


# ---------- 元件 ----------

class ParaView(Static):
    def __init__(self, para: Para, count: int = 0) -> None:
        super().__init__(classes=para.kind)
        self.para, self.count = para, count
        self.redraw()

    def redraw(self) -> None:
        t = Text(zh(self.para.text))
        if self.count:
            t.append("  ")
            t.append(f" {self.count} ", style="reverse")
        self.update(t)


def review_text(r: Review, reply: bool = False) -> Text:
    t = Text()
    t.append(("↳ " if reply else "") + zh(r.user), style="bold")
    meta = "  ".join(x for x in (f"♥ {r.likes}" if r.likes else "", r.time, zh(r.place)) if x)
    t.append(f"  {meta}\n", style="dim")
    t.append(zh(r.content))
    return t


# ---------- 目錄 / 書籤 / 書架 / 最近 / 連結 ----------

class TocScreen(ModalScreen):
    BINDINGS = [
        Binding("escape,q", "dismiss(None)", "關閉"),
        Binding("left", "tab(-1)", priority=True),
        Binding("right", "tab(1)", priority=True),
        Binding("d,delete", "delete_mark", "刪書籤"),
    ]
    DEFAULT_CSS = """
    TocScreen { background: ansi_default; }
    TocScreen > TabbedContent { width: 100%; height: 1fr; }
    TocScreen OptionList, TocScreen OptionList:focus { height: 1fr; border: none; padding: 0; background: ansi_default; scrollbar-size-vertical: 0; }
    TocScreen #toc-hint { dock: bottom; height: 1; text-style: dim; }
    """

    def __init__(self, toc: list[TocEntry], current: str | None, marks: list[Mark], recent: list[Mark],
                 shelf: list[ShelfBook], links: list[tuple[str, str]], start: str = "toc") -> None:
        super().__init__()
        self.toc, self.current, self.marks, self.recent = toc, current, marks, recent
        self.shelf, self.links, self.start = shelf, links, start

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=self.start):
            if self.toc:
                with TabPane("目錄", id="toc"):
                    yield OptionList(*[
                        Option(Text(zh(e.title), style="bold reverse") if e.url is None else
                               Text.assemble(zh(e.title), ("  VIP" if e.vip else "", "dim")),
                               id=f"t{i}", disabled=e.url is None)
                        for i, e in enumerate(self.toc)
                    ], id="toc-list")
            with TabPane("書籤", id="marks"):
                yield OptionList(*self._mark_options(), id="mark-list")
            if self.shelf:
                with TabPane("書架", id="shelf"):
                    yield OptionList(*[Option(self._shelf_text(b), id=f"s{i}") for i, b in enumerate(self.shelf)],
                                     id="shelf-list")
            if self.recent:
                with TabPane("最近", id="recent"):
                    yield OptionList(*[Option(self._mark_text(m), id=f"r{i}") for i, m in enumerate(self.recent)],
                                     id="recent-list")
            if self.links:
                with TabPane("連結", id="links"):
                    yield OptionList(*[Option(Text.assemble((f"[{i}] ", "cyan"), zh(label), (f"  {href}", "dim")), id=f"l{i}")
                                       for i, (label, href) in enumerate(self.links, 1)], id="link-list")
        yield Static(Text("← → 切換分頁　↑ ↓ 選擇　Enter 開啟　d 刪書籤　Esc 關閉"), id="toc-hint")

    @staticmethod
    def _mark_text(m: Mark) -> Text:
        return Text.assemble((zh(m.book) + "  ", "bold"), zh(m.chapter), "\n", (f"{m.pct:.0f}%", "red"), (f" · {m.date}", "dim"))

    def _shelf_text(self, b: ShelfBook) -> Text:
        t = Text.assemble((zh(b.book), "bold"), (f"  {zh(b.author)}", "dim"))
        if local := self.app.store.resume(b.book_key, b.read_time):
            t.append_text(Text.assemble("\n本機讀到：", zh(local.chapter), (f"  {local.pct:.0f}% · {local.date}", "dim")))
        else:
            t.append_text(Text.assemble("\n讀到：", zh(b.read_chapter), (f"  {zh(b.when)}", "dim")))
        if b.updated and b.latest:
            t.append(f"\n最新：{zh(b.latest)}", style="red")
        return t

    def _mark_options(self) -> list[Option]:
        if not self.marks:
            return [Option(Text("還沒有書籤，閱讀時按 b 加入", style="dim"), disabled=True)]
        return [Option(self._mark_text(m), id=f"m{i}") for i, m in enumerate(self.marks)]

    def on_mount(self) -> None:
        if self.toc and self.current:
            idx = next((i for i, e in enumerate(self.toc) if e.url == self.current), None)
            if idx is not None:
                lst = self.query_one("#toc-list", OptionList)
                lst.highlighted = idx
                lst.scroll_to_highlight(top=False)
        self._focus_active()

    def _focus_active(self) -> None:
        tabs = self.query_one(TabbedContent)
        if tabs.active_pane:
            tabs.active_pane.query_one(OptionList).focus()

    def action_tab(self, step: int) -> None:
        tabs = self.query_one(TabbedContent)
        ids = [p.id for p in tabs.query(TabPane)]
        tabs.active = ids[(ids.index(tabs.active) + step) % len(ids)]
        self._focus_active()

    def action_delete_mark(self) -> None:
        if self.query_one(TabbedContent).active != "marks":
            return
        lst = self.query_one("#mark-list", OptionList)
        if lst.highlighted is None or not self.marks:
            return
        self.app.store.remove_bookmark(self.marks.pop(lst.highlighted))
        lst.clear_options()
        lst.add_options(self._mark_options())

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        kind, i = ev.option_id[0], int(ev.option_id[1:])
        if kind == "t":
            self.dismiss(self.toc[i].url)
        elif kind == "m":
            self.dismiss(self.marks[i])
        elif kind == "r":
            self.dismiss(self.recent[i])
        elif kind == "s":
            self.dismiss(self.shelf[i])
        elif kind == "l":
            self.dismiss(self.links[i - 1][1])


class UrlScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss(None)")]
    DEFAULT_CSS = """
    UrlScreen { background: ansi_default; align: left bottom; }
    UrlScreen > Input { width: 100%; border: none; background: ansi_default; }
    """

    def compose(self) -> ComposeResult:
        yield Input(placeholder="輸入網址，Enter 開啟、Esc 取消")

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        self.dismiss(ev.value.strip() or None)


# ---------- 主閱讀畫面 ----------

END = -1  # 載入後停在章尾（從下一章往回翻時）


class ReaderScreen(Screen):
    BINDINGS = [
        Binding("up", "scroll(-2)", show=False, priority=True),
        Binding("down", "scroll(2)", show=False, priority=True),
        Binding("right,pagedown,space", "page(1)", show=False, priority=True),
        Binding("left,pageup", "page(-1)", show=False, priority=True),
        Binding("home", "edge(0)", show=False, priority=True),
        Binding("end", "edge(1)", show=False, priority=True),
        Binding("right_square_bracket", "chapter(1)", show=False, priority=True),
        Binding("left_square_bracket", "chapter(-1)", show=False, priority=True),
        Binding("enter", "toc", show=False, priority=True),
        Binding("f11,z", "zen", show=False, priority=True),
        Binding("b", "bookmark", show=False, priority=True),
        Binding("tab", "review(1)", show=False, priority=True),
        Binding("shift+tab", "review(-1)", show=False, priority=True),
        Binding("c", "toggle_reviews", show=False, priority=True),
        Binding("j", "review_scroll(3)", show=False, priority=True),
        Binding("k", "review_scroll(-3)", show=False, priority=True),
        Binding("m", "more_reviews", show=False, priority=True),
        Binding("t", "toggle_trad", show=False, priority=True),
        Binding("p", "buy", show=False, priority=True),
        Binding("r", "reload", show=False, priority=True),
        Binding("o", "open_url", show=False, priority=True),
        Binding("l", "links", show=False, priority=True),
        Binding("q", "quit_reader", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    #top { height: 1; text-style: dim; }
    #bottom { height: 1; }
    #pos { width: auto; text-style: bold; padding-right: 2; }
    #hints { width: 1fr; text-style: dim; text-align: right; }
    ReaderScreen.-zen #top, ReaderScreen.-zen #bottom { display: none; }
    #reader { width: 1fr; scrollbar-size-vertical: 0; }
    ParaView { width: 100%; margin-bottom: 1; }
    ParaView.title { text-style: bold; color: ansi_yellow; margin-bottom: 1; }
    ParaView.h1, ParaView.h2 { text-style: bold; color: ansi_yellow; }
    ParaView.h3 { text-style: bold; }
    ParaView.quote { text-style: italic dim; padding-left: 2; }
    ParaView.code { color: ansi_cyan; padding-left: 2; }
    ParaView.li { margin-bottom: 0; }
    ParaView.notice { color: ansi_red; }
    ParaView.-sel { text-style: underline; color: ansi_bright_white; }
    #reviews { width: 42%; display: none; border-left: vkey ansi_bright_black; padding-left: 1; scrollbar-size-vertical: 0; }
    #reviews.-show { display: block; }
    #reviews > Static { margin-bottom: 1; }
    #reviews > .rv-head { color: ansi_yellow; text-style: bold; }
    #reviews > .rv-quote { text-style: dim; }
    #reviews > .rv-reply { padding-left: 2; text-style: dim; }
    #reviews > .rv-more { text-style: dim; }
    """

    def __init__(self, start: str | Mark | None) -> None:
        super().__init__()
        self.start = start
        self.ch: Chapter | None = None
        self.src: Source | None = None
        self.views: list[ParaView] = []
        self.sel: int | None = None
        self.rv_page = 0
        self.rv_end = True
        self.busy = False
        self.cache: dict[str, tuple[Chapter, dict[int, int]]] = {}

    @property
    def store(self) -> Store:
        return self.app.store

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        with Horizontal():
            yield VerticalScroll(id="reader")
            yield VerticalScroll(id="reviews")
        with Horizontal(id="bottom"):
            yield Static(id="pos")
            yield Static(id="hints")

    def on_mount(self) -> None:
        self.reader = self.query_one("#reader", VerticalScroll)
        self.rv = self.query_one("#reviews", VerticalScroll)
        self.set_interval(0.3, self.refresh_status)
        self.set_interval(15, self.save_progress)
        if isinstance(self.start, Mark):
            self.load(self.start.url, self.start.para)
        elif self.start:
            self.load(self.start)
        else:
            self.app.call_after_refresh(self.action_toc, "recent")

    # ----- 載入章節 -----

    @work(thread=True, exclusive=True, group="load")
    def load(self, url: str, para: int | None = None) -> None:
        self.busy = True
        self.app.call_from_thread(self.set_top, f"載入中 {url} …")
        try:
            if url in self.cache:
                ch, counts = self.cache[url]
            else:
                src = source_for(url)
                ch = src.chapter(url)
                counts = self._counts(src, ch)
                self.cache[url] = self.cache[ch.url] = (ch, counts)
            src = source_for(ch.url)
        except Exception as e:  # noqa: BLE001
            self.busy = False
            self.app.call_from_thread(self.app.notify, f"讀取失敗：{e}", severity="error", timeout=8)
            return
        if para is None and _path(ch.url) != _path(url) and (local := self.store.resume(ch.book_key)) \
                and _path(local.url) != _path(ch.url):
            # 給的是書頁，來源替我們選了一章（通常是網站書架記的）；本機有進度就以本機為準
            self.busy = False
            self.app.call_from_thread(self.load, local.url, local.para)
            return
        self.app.call_from_thread(self.show_chapter, ch, src, counts, para)
        # 預先抓下一章，翻到底時就不用等
        if ch.next_url and ch.next_url not in self.cache:
            try:
                nxt = src.chapter(ch.next_url)
                self.cache[ch.next_url] = (nxt, self._counts(src, nxt))
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _counts(src: Source, ch: Chapter) -> dict[int, int]:
        if not src.has_reviews:
            return {}
        try:
            return src.review_counts(ch)
        except Exception:  # noqa: BLE001 — 段評抓不到不影響閱讀
            return {}

    async def show_chapter(self, ch: Chapter, src: Source, counts: dict[int, int], para: int | None) -> None:
        if self.ch:
            self.save_progress()
        self.ch, self.src, self.sel = ch, src, None
        await self.reader.remove_children()
        title = ParaView(Para(ch.title, kind="title", pid=0 if src.has_reviews else None), counts.get(0, 0))
        self.views = [title]
        if ch.notice:
            self.views.append(ParaView(Para(ch.notice, kind="notice")))
        self.views += [ParaView(p, counts.get(p.pid, 0) if p.pid is not None else 0) for p in ch.paras]
        if not ch.paras:
            self.views.append(ParaView(Para("（擷取不到正文）", kind="notice")))
        await self.reader.mount_all(self.views)
        self.hide_reviews()
        if para == END:
            self.call_after_refresh(self._restore, None)
        elif para and (target := self.views[min(para, len(self.views) - 1)]):
            # 等排版算出段落高度再捲，否則位置都是 0
            self.call_after_refresh(self._restore, target)
        else:
            self.reader.scroll_home(animate=False)
            self.busy = False
            self.save_progress()

    def _restore(self, target: ParaView | None, tries: int = 0) -> None:
        # 從彈出視窗回來時，底下畫面要等它關掉才排版，排好前位置都是 0，稍等再試
        if self.reader.max_scroll_y <= 0 and tries < 40:
            self.set_timer(0.05, lambda: self._restore(target, tries + 1))
            return
        if target is None:
            self.reader.scroll_end(animate=False)
        else:
            self.reader.scroll_to_widget(target, top=True, animate=False)
        self.busy = False
        self.call_after_refresh(self.save_progress)

    # ----- 狀態列與進度 -----

    def set_top(self, text: str) -> None:
        self.query_one("#top", Static).update(Text(text, overflow="ellipsis", no_wrap=True))

    def chapter_pct(self) -> float:
        m = self.reader.max_scroll_y
        return 100.0 if m <= 0 else min(100.0, self.reader.scroll_y / m * 100)

    def book_pct(self) -> float:
        ch = self.ch
        if ch and ch.seq and ch.total:
            return min(100.0, ((ch.seq - 1) + self.chapter_pct() / 100) / ch.total * 100)
        return self.chapter_pct()

    def first_visible(self) -> int:
        y = self.reader.scroll_y
        for i, v in enumerate(self.views):
            r = v.virtual_region
            if r.y + r.height > y:
                return i
        return 0

    def refresh_status(self) -> None:
        ch = self.ch
        pos = self.query_one("#pos", Static)
        hints = self.query_one("#hints", Static)
        if not ch:
            self.set_top("termread")
            pos.update("")
            hints.update(Text("Enter 書架／書籤／最近閱讀　o 開網址　q 離開", no_wrap=True, overflow="ellipsis"))
            return
        if not self.busy:
            self.set_top(zh(f"{ch.book} · {ch.title}" if ch.book else ch.title))
        seq = f"{ch.seq}/{ch.total} 章 · " if ch.seq and ch.total else ""
        pos.update(f"{seq}本章 {self.chapter_pct():.0f}%" + (f" · 全書 {self.book_pct():.1f}%" if ch.total else ""))
        keys = ["←→翻頁", "[]換章", "Enter目錄", "b書籤"]
        if self.src and self.src.has_reviews:
            keys.append("Tab段評")
        if ch.buy_url:
            keys.append("p購買")
        keys += ["q離開", "t繁簡", "z全屏"]  # 太窄時從尾巴截掉，常用的放前面
        hints.update(Text("  ".join(keys), no_wrap=True, overflow="ellipsis"))

    def current_mark(self) -> Mark | None:
        if not self.ch:
            return None
        return Mark(url=self.ch.url, book=self.ch.book, chapter=self.ch.title, para=self.first_visible(),
                    pct=round(self.book_pct(), 1), time=time.time(), book_key=self.ch.book_key)

    def save_progress(self) -> None:
        if not self.busy and (m := self.current_mark()):
            self.store.set_progress(m)

    # ----- 動作 -----

    def action_scroll(self, n: int) -> None:
        self.reader.scroll_relative(y=n, animate=False)

    def action_page(self, d: int) -> None:
        if not self.ch or self.busy:
            return
        r = self.reader
        if d > 0 and r.scroll_y >= r.max_scroll_y - 0.5:
            self.action_chapter(1)  # 已在最後一頁 → 下一章
        elif d < 0 and r.scroll_y <= 0.5:
            self.action_chapter(-1, END)  # 已在第一頁 → 上一章的最後一頁
        else:
            (r.scroll_page_down if d > 0 else r.scroll_page_up)(animate=False)

    def action_edge(self, end: int) -> None:
        (self.reader.scroll_end if end else self.reader.scroll_home)(animate=False)

    def action_chapter(self, d: int, para: int | None = None) -> None:
        if not self.ch:
            return
        url = self.ch.next_url if d > 0 else self.ch.prev_url
        if url:
            self.load(url, para)
        else:
            self.app.notify("已經是最後一章" if d > 0 else "已經是第一章", timeout=2)

    def action_zen(self) -> None:
        self.toggle_class("-zen")

    def action_toggle_trad(self) -> None:
        TRAD["on"] = not TRAD["on"]
        self.store.set_setting("traditional", TRAD["on"])
        for v in self.views:
            v.redraw()
        self.app.notify("已切換為繁體" if TRAD["on"] else "已切換為原文（簡體）", timeout=2)

    def action_bookmark(self) -> None:
        if m := self.current_mark():
            self.store.add_bookmark(m)
            self.app.notify(f"已加書籤：{zh(m.chapter)}  {m.pct:.0f}%", timeout=2)

    def action_buy(self) -> None:
        if self.ch and self.ch.buy_url and self.src:
            self.src.open_in_browser(self.ch.buy_url)
            self.app.notify("已在瀏覽器開啟購買頁，買完回來按 r 重新載入", timeout=6)

    def action_reload(self) -> None:
        if self.ch:
            self.cache.pop(self.ch.url, None)
            self.load(self.ch.url, self.first_visible())

    def action_toc(self, start: str = "toc") -> None:
        self._open_toc(start)

    def action_links(self) -> None:
        if self.ch and self.ch.links:
            self._open_toc("links")

    @work(thread=True, exclusive=True, group="toc")
    def _open_toc(self, start: str) -> None:
        toc: list[TocEntry] = []
        if self.ch and self.src:
            try:
                toc = self.src.toc(self.ch)
            except Exception as e:  # noqa: BLE001
                self.app.call_from_thread(self.app.notify, f"目錄讀取失敗：{e}", severity="warning")
        shelf = []
        for src in plugin_sources():
            try:
                shelf += src.shelf()
            except Exception:  # noqa: BLE001 — 外掛的書架抓不到就略過
                pass
        shelf.sort(key=lambda b: b.read_time, reverse=True)
        recent = self.store.recent()
        avail = {"toc": bool(toc), "shelf": bool(shelf), "recent": bool(recent), "links": bool(self.ch and self.ch.links)}
        if not avail.get(start, True):
            start = next((k for k in ("recent", "shelf") if avail[k]), "marks")
        self.app.call_from_thread(self._push_toc, toc, recent, shelf, start)

    def _push_toc(self, toc, recent, shelf, start) -> None:
        # 畫面元件要在主執行緒建立
        screen = TocScreen(toc, self.ch.url if self.ch else None, list(self.store.bookmarks), recent, shelf,
                           self.ch.links if self.ch else [], start)
        self.app.push_screen(screen, self._picked)

    def _picked(self, result) -> None:
        if result is None:
            if not self.ch:
                self.action_open_url()
            return
        if isinstance(result, Mark):
            if self.ch and result.url == self.ch.url:
                self.reader.scroll_to_widget(self.views[min(result.para, len(self.views) - 1)], top=True, animate=False)
            else:
                self.load(result.url, result.para)
        elif isinstance(result, str) and result.startswith("#"):
            i = int(result[1:]) + (len(self.views) - len(self.ch.paras))  # 頁內標題，扣掉章名等前置列
            self.reader.scroll_to_widget(self.views[i], top=True, animate=False)
        elif isinstance(result, ShelfBook):
            # 網站書架的進度常比本機舊（只有在官方 App 讀才會同步），本機較新就接本機的
            if local := self.store.resume(result.book_key, result.read_time):
                self.load(local.url, local.para)
            else:
                self.load(result.url, self._para_for(result.url))
        elif isinstance(result, str):
            self.load(result, self._para_for(result))

    def _para_for(self, url: str) -> int | None:
        # 本機記過這一章的段落位置就接著讀（網站書架通常只記到章）
        mark = next((m for m in self.store.progress.values() if m.url == url), None)
        return mark.para if mark else None

    def action_open_url(self) -> None:
        self.app.push_screen(UrlScreen(), lambda url: url and self.load(url))

    def action_quit_reader(self) -> None:
        self.save_progress()
        self.app.exit()

    # ----- 段評 -----

    def hide_reviews(self) -> None:
        self.rv.remove_class("-show")
        if self.sel is not None:
            self.views[self.sel].remove_class("-sel")
        self.sel = None

    def action_toggle_reviews(self) -> None:
        if self.rv.has_class("-show"):
            self.hide_reviews()
        else:
            self.action_review(1)

    def action_review(self, d: int) -> None:
        if not (self.src and self.src.has_reviews):
            return
        idx = [i for i, v in enumerate(self.views) if v.count]
        if not idx:
            self.app.notify("這一章沒有段評", timeout=2)
            return
        if self.sel is None:
            fv = self.first_visible()
            pick = next((i for i in idx if i >= fv), idx[0]) if d > 0 else next((i for i in reversed(idx) if i <= fv + 3), idx[-1])
        else:
            pos = idx.index(self.sel) if self.sel in idx else 0
            pick = idx[(pos + d) % len(idx)]
            self.views[self.sel].remove_class("-sel")
        self.sel = pick
        v = self.views[pick]
        v.add_class("-sel")
        self.rv.add_class("-show")
        self.call_after_refresh(self.reader.scroll_to_widget, v, center=True, animate=False)
        self.rv.remove_children()
        text = zh(v.para.text)
        quote = text if len(text) <= 120 else text[:120] + "…"
        label = "整章評論" if v.para.pid == 0 else "段評"
        self.rv.mount(Static(Text(f"{label} · {v.count} 則"), classes="rv-head"),
                      Static(Text(quote), classes="rv-quote"))
        self.rv_page, self.rv_end = 0, False
        self.fetch_reviews(v.para.pid, 1)

    def action_more_reviews(self) -> None:
        if self.sel is not None and not self.rv_end:
            self.fetch_reviews(self.views[self.sel].para.pid, self.rv_page + 1)

    @work(thread=True, exclusive=True, group="reviews")
    def fetch_reviews(self, pid: int, page: int) -> None:
        ch, src = self.ch, self.src
        try:
            rp = src.reviews(ch, pid, page)
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(self.app.notify, f"段評讀取失敗：{e}", severity="warning")
            return
        self.app.call_from_thread(self._show_reviews, pid, page, rp)

    def _show_reviews(self, pid: int, page: int, rp) -> None:
        if self.sel is None or self.views[self.sel].para.pid != pid:
            return  # 已經切到別段
        for w in self.rv.query(".rv-more"):
            w.remove()
        ws: list[Static] = []
        for r in rp.items:
            ws.append(Static(review_text(r)))
            ws += [Static(review_text(x, reply=True), classes="rv-reply") for x in r.replies]
            if r.more_replies:
                ws.append(Static(Text(f"  還有 {r.more_replies} 則回覆", style="dim"), classes="rv-reply"))
        self.rv_page, self.rv_end = page, rp.end
        ws.append(Static(Text("— 沒有更多了 —" if rp.end else "按 m 載入更多"), classes="rv-more"))
        self.rv.mount_all(ws)
        if page == 1:
            self.rv.scroll_home(animate=False)

    def action_review_scroll(self, n: int) -> None:
        if self.rv.has_class("-show"):
            self.rv.scroll_relative(y=n, animate=False)
            if self.rv.scroll_y >= self.rv.max_scroll_y - 1:
                self.action_more_reviews()


class TermRead(App):
    TITLE = "termread"
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, start: str | None) -> None:
        super().__init__(ansi_color=True)  # 沿用終端機自己的配色與背景
        self.store = Store()
        TRAD["on"] = self.store.settings.get("traditional", True)
        self.start = start

    def on_mount(self) -> None:
        self.theme = "ansi-dark"
        self.push_screen(ReaderScreen(self.start))

    def on_unmount(self) -> None:
        close_all()


def run(url: str | None) -> None:
    TermRead(url).run()
