"""termread — 在 terminal 裡讀網頁，只留正文文字。"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import trafilatura

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LINK_RE = re.compile(r"(?<!!)\[((?:\\.|[^\]\\])*)\]\((?:<([^>]+)>|([^)\s]+))(?:\s+\"[^\"]*\")?\)")
HTML_TAG_RE = re.compile(r"</?(?:sup|sub|span|small|br|u|s|mark)\b[^>]*>")
WIKI_EDIT_RE = re.compile(r"\\\[\s*\[edit\]\((?:<[^>]*>|[^)]*)\)\s*\\\]", re.S)
ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>])")
IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
CODE_RE = re.compile(r"`([^`]+)`")
TOKEN_RE = re.compile(r"\x1b\[[0-9;]*m|\s+|[ᄀ-￿]|[^\sᄀ-￿\x1b]+")


class Style:
    on = True

    @classmethod
    def s(cls, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if cls.on else text


@dataclass
class Page:
    url: str
    title: str
    body: str  # 已排版好的文字
    links: list[tuple[str, str]] = field(default_factory=list)  # (文字, 絕對網址)


# ---------- 抓取與擷取 ----------

def fetch(url: str, timeout: int = 20) -> tuple[str, bytes]:
    if not re.match(r"^[a-z]+://", url):
        url = "https://" + url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.geturl(), resp.read()


def extract(html: bytes, url: str) -> tuple[str, str]:
    md = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        include_images=False,
        include_comments=False,
        favor_recall=True,
    )
    if not md:
        # 正文判斷失敗時退回全頁純文字
        _, text, _ = trafilatura.baseline(html)
        md = text or ""
    meta = trafilatura.extract_metadata(html, default_url=url)
    title = (meta.title if meta and meta.title else "") or url
    return title, md


# ---------- 排版 ----------

def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def vis_width(s: str) -> int:
    return sum(char_width(c) for c in ANSI_RE.sub("", s))


def wrap(text: str, width: int, first: str = "", rest: str = "") -> list[str]:
    """CJK 與 ANSI 感知的折行。first/rest 是首行與後續行的前綴。"""
    lines: list[str] = []
    cur, cur_w = first, vis_width(first)
    rest_w = vis_width(rest)
    fresh = True  # 這行還沒有內容

    def newline():
        nonlocal cur, cur_w, fresh
        lines.append(cur.rstrip())
        cur, cur_w, fresh = rest, rest_w, True

    for tok in TOKEN_RE.findall(text):
        if tok.startswith("\x1b"):
            cur += tok
            continue
        if tok.isspace():
            if not fresh:
                cur += " "
                cur_w += 1
            continue
        w = vis_width(tok)
        if cur_w + w > width and not fresh:
            newline()
        # 單一 token 比整行還寬（超長網址等）就硬切
        while cur_w + w > width and len(tok) > 1:
            room = width - cur_w
            i, acc = 0, 0
            while i < len(tok) and acc + char_width(tok[i]) <= room:
                acc += char_width(tok[i])
                i += 1
            cur += tok[:i]
            tok = tok[i:]
            w = vis_width(tok)
            newline()
        cur += tok
        cur_w += w
        fresh = False
    if not fresh or not lines:
        lines.append(cur.rstrip())
    return lines


def inline(text: str, base: str, links: list[tuple[str, str]]) -> str:
    text = HTML_TAG_RE.sub("", IMG_RE.sub("", text))

    def link_sub(m: re.Match) -> str:
        label = ESCAPE_RE.sub(r"\1", m.group(1)).strip()
        href = urljoin(base, m.group(2) or m.group(3))
        # 頁內錨點（註腳、目錄）和 js/mailto 不編號
        u, b = urlparse(href), urlparse(base)
        same_page = bool(u.fragment) and u.netloc == b.netloc and u.path.rstrip("/") in ("", b.path.rstrip("/"))
        if href.startswith(("javascript:", "mailto:")) or not label or same_page:
            return label
        links.append((label, href))
        return label + Style.s("36", f"[{len(links)}]")

    text = LINK_RE.sub(link_sub, text)
    text = BOLD_RE.sub(lambda m: Style.s("1", m.group(1)), text)
    text = ITALIC_RE.sub(lambda m: Style.s("3", m.group(1)), text)
    text = CODE_RE.sub(lambda m: Style.s("33", m.group(1)), text)
    return ESCAPE_RE.sub(r"\1", text)


def render(md: str, base: str, width: int) -> tuple[str, list[tuple[str, str]]]:
    links: list[tuple[str, str]] = []
    out: list[str] = []
    in_code = False
    md = WIKI_EDIT_RE.sub("", md)

    def blank():
        if out and out[-1] != "":
            out.append("")

    for raw in md.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            in_code = not in_code
            blank()
            continue
        if in_code:
            out.append(Style.s("2", "  " + line))
            continue
        s = line.strip()
        if not s:
            blank()
            continue
        if m := re.match(r"^(#{1,6})\s+(.*)", s):
            level = len(m.group(1))
            blank()
            text = inline(m.group(2), base, links)
            color = "1;35" if level <= 2 else "1"
            out += [Style.s(color, ln) for ln in wrap(ANSI_RE.sub("", text) if level <= 2 else text, width)]
            if level <= 2:
                out.append(Style.s("2", "─" * min(width, vis_width(out[-1]))))
            out.append("")
        elif s.startswith("|"):
            if re.fullmatch(r"\|[\s\-:|]+\|?", s):
                continue
            cells = [inline(c.strip(), base, links) for c in s.strip("|").split("|")]
            out.append("  " + Style.s("2", " │ ").join(cells))
        elif m := re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)", line):
            depth = len(m.group(1).expandtabs(4)) // 2
            bullet = "•" if not m.group(2)[0].isdigit() else m.group(2)
            pad = "  " * depth
            first = f"{pad}  {bullet} "
            out += wrap(inline(m.group(3), base, links), width, first, " " * vis_width(first))
        elif s.startswith(">"):
            bar = Style.s("2", "  │ ")
            out += wrap(Style.s("3", inline(s.lstrip("> "), base, links)), width, bar, bar)
        else:
            out += wrap(inline(s, base, links), width)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out), links


def build_page(url: str, width: int) -> Page:
    final_url, html = fetch(url)
    title, md = extract(html, final_url)
    body, links = render(md, final_url, width)
    return Page(final_url, title, body, links)


def compose(page: Page, width: int, show_links: bool) -> str:
    parts = [
        *[Style.s("1;4", ln) for ln in wrap(page.title, width)],
        Style.s("2", page.url),
        "",
        page.body or Style.s("2", "（擷取不到正文）"),
    ]
    if show_links and page.links:
        parts += ["", Style.s("2", "─" * width), Style.s("1", "連結")]
        for i, (label, href) in enumerate(page.links, 1):
            parts += wrap(f"{label} {Style.s('2', href)}", width, Style.s("36", f"[{i}] "), "     ")
    return "\n".join(parts) + "\n"


# ---------- 輸出 ----------

def show(text: str, use_pager: bool) -> None:
    if use_pager and shutil.which("less"):
        env = dict(os.environ, LESSCHARSET="utf-8")
        subprocess.run(["less", "-RFX"], input=text.encode(), env=env)
    else:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except BrokenPipeError:  # 接 head 之類提早關閉的 pipe
            sys.stderr.close()


def main() -> None:
    if sys.argv[1:2] == ["plugin"]:
        from .plugins import main as plugin_main

        sys.exit(plugin_main(sys.argv[2:]))
    ap = argparse.ArgumentParser(prog="termread", description="在 terminal 裡讀網頁與小說，只擷取正文文字",
                                 epilog="外掛：termread plugin {install,list,remove,new} …")
    ap.add_argument("url", nargs="?", help="不給網址就開書籤／最近閱讀")
    ap.add_argument("-w", "--width", type=int, help="輸出模式的排版寬度（預設依終端機，上限 100）")
    ap.add_argument("-L", "--no-links", action="store_true", help="輸出模式：不在文末列出連結")
    ap.add_argument("-P", "--print", dest="print_mode", action="store_true", help="不開閱讀介面，排版後用 less 顯示")
    ap.add_argument("--no-pager", action="store_true", help="輸出模式：不用 less，直接印出")
    ap.add_argument("--plain", action="store_true", help="輸出模式：不加顏色（輸出到 pipe 時自動啟用）")
    ap.add_argument("--markdown", action="store_true", help="直接輸出擷取到的 markdown")
    args = ap.parse_args()

    tty = sys.stdout.isatty()
    if tty and not (args.print_mode or args.no_pager or args.plain or args.markdown):
        from .tui import run

        run(args.url)
        return
    if not args.url:
        ap.error("輸出模式需要網址")

    Style.on = tty and not args.plain and "NO_COLOR" not in os.environ
    width = args.width or min(shutil.get_terminal_size((80, 24)).columns - 2, 100)
    try:
        if args.markdown:
            final_url, html = fetch(args.url)
            sys.stdout.write(extract(html, final_url)[1] + "\n")
            return
        page = build_page(args.url, width)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"termread: {e}")
    show(compose(page, width, not args.no_links), use_pager=tty and not args.no_pager)


if __name__ == "__main__":
    main()
