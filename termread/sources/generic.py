"""一般網站：trafilatura 抓正文，從頁面連結猜上一章 / 下一章 / 目錄。"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import trafilatura
from trafilatura.utils import load_html

from .. import net
from ..cli import WIKI_EDIT_RE, Style, extract, inline
from ..models import Chapter, Para, TocEntry
from . import Source

ARROWS = r"[\s>»→›<«←‹]*"
# 英文不用 re.I：避免把「NeXT」這類專有名詞當成下一頁
NEXT_RE = re.compile(rf"^{ARROWS}(下一[章页頁节節篇回话話]|下[章页頁]|(Next|next|NEXT)( [Cc]hapter| [Pp]age| CHAPTER| PAGE)?|[Oo]lder [Pp]osts?){ARROWS}$")
PREV_RE = re.compile(rf"^{ARROWS}(上一[章页頁节節篇回话話]|上[章页頁]|(Prev|prev|PREV|Previous|previous|PREVIOUS)( [Cc]hapter| [Pp]age| CHAPTER| PAGE)?|[Nn]ewer [Pp]osts?){ARROWS}$")
TOC_RE = re.compile(r"^\s*(返回)?(章[节節])?(目[录錄]|列表)\s*$|^\s*(table of )?contents\s*$|^\s*(全部|所有)章[节節]\s*$", re.I)
CHAPTER_RE = re.compile(r"^\s*(第\s*[\d零〇一二三四五六七八九十百千万萬两兩]+\s*[章节節回卷話话集]|chapter\s*\d+|\d+[.、．]\s*\S)", re.I)


def _anchors(tree, base: str):
    for a in tree.iter("a"):
        href = a.get("href")
        if href and not href.startswith(("javascript:", "#", "mailto:")):
            yield " ".join(a.text_content().split()), urljoin(base, href), (a.get("rel") or "").lower()


def _find(tree, base: str, pattern: re.Pattern, rel: str) -> str | None:
    for link in tree.iter("link"):
        if (link.get("rel") or "").lower() == rel and link.get("href"):
            return urljoin(base, link.get("href"))
    for text, href, r in _anchors(tree, base):
        if r == rel or pattern.match(text):
            return href
    return None


def md_to_paras(md: str, base: str, links: list[tuple[str, str]]) -> list[Para]:
    paras: list[Para] = []
    in_code, code_buf = False, []
    for raw in WIKI_EDIT_RE.sub("", md).splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            if in_code and code_buf:
                paras.append(Para("\n".join(code_buf), kind="code"))
            in_code, code_buf = not in_code, []
            continue
        if in_code:
            code_buf.append(line)
            continue
        s = line.strip()
        if not s:
            continue
        if m := re.match(r"^(#{1,6})\s+(.*)", s):
            paras.append(Para(inline(m.group(2), base, links), kind=f"h{min(len(m.group(1)), 3)}"))
        elif s.startswith("|"):
            if not re.fullmatch(r"\|[\s\-:|]+\|?", s):
                paras.append(Para(" │ ".join(inline(c.strip(), base, links) for c in s.strip("|").split("|")), kind="table"))
        elif m := re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)", line):
            bullet = m.group(2) if m.group(2)[0].isdigit() else "•"
            indent = "  " * (len(m.group(1).expandtabs(4)) // 2)
            paras.append(Para(f"{indent}{bullet} {inline(m.group(3), base, links)}", kind="li"))
        elif s.startswith(">"):
            paras.append(Para(inline(s.lstrip("> "), base, links), kind="quote"))
        else:
            paras.append(Para(inline(s, base, links), kind="p"))
    return paras


class GenericSource(Source):
    @staticmethod
    def matches(url: str) -> bool:
        return True

    def chapter(self, url: str) -> Chapter:
        final_url, raw = net.fetch(url)
        title, md = extract(raw, final_url)
        tree = load_html(raw)
        links: list[tuple[str, str]] = []
        Style.on = False  # TUI 自己上色，正文不要 ANSI
        paras = md_to_paras(md, final_url, links)
        meta = trafilatura.extract_metadata(raw, default_url=final_url)
        toc_url = _find(tree, final_url, TOC_RE, "contents") if tree is not None else None
        host = urlparse(final_url).netloc
        prev_url = _find(tree, final_url, PREV_RE, "prev") if tree is not None else None
        next_url = _find(tree, final_url, NEXT_RE, "next") if tree is not None else None
        # 有上下章才當成連載，用目錄頁當作整本書的進度 key；單篇文章各自記
        serial = bool(toc_url and (prev_url or next_url))
        return Chapter(
            url=final_url,
            title=title,
            paras=paras,
            book=(meta.sitename if meta and meta.sitename else host),
            book_key=toc_url if serial else final_url,
            prev_url=prev_url,
            next_url=next_url,
            toc_url=toc_url if serial else None,
            links=links,
        )

    def toc(self, ch: Chapter) -> list[TocEntry]:
        if ch.toc_url:
            try:
                final_url, raw = net.fetch(ch.toc_url)
                tree = load_html(raw)
                seen, out = set(), []
                for text, href, _ in _anchors(tree, final_url):
                    if CHAPTER_RE.match(text) and href not in seen:
                        seen.add(href)
                        out.append(TocEntry(text, href))
                if len(out) >= 3:
                    return out
            except Exception:  # noqa: BLE001 — 目錄頁抓不到就退回頁內標題
                pass
        # 沒有目錄頁：用本頁的標題當目錄，網址用 #段落索引
        return [TocEntry(p.text, f"#{i}") for i, p in enumerate(ch.paras) if p.kind.startswith("h")]
