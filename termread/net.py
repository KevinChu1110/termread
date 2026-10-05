"""共用的 HTTP 抓取：帶 cookie jar，讓網站的 session / CSRF token 能自動延續。"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import urllib.request
from pathlib import Path

DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "termread"

jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

# 外掛可登記要沿用 Chrome 登入狀態的網域（需安裝 browser-cookie3：pip install termread[cookies]）
CHROME_COOKIE_DOMAINS: set[str] = set()
_loaded: set[str] = set()


def use_chrome_cookies(domain: str) -> None:
    CHROME_COOKIE_DOMAINS.add(domain)


def _load_chrome_cookies(host: str) -> None:
    for dom in CHROME_COOKIE_DOMAINS:
        if host.endswith(dom) and dom not in _loaded:
            _loaded.add(dom)
            try:
                import browser_cookie3

                for c in browser_cookie3.chrome(domain_name=dom):
                    jar.set_cookie(c)
            except Exception:  # noqa: BLE001 — 讀不到就當未登入
                pass


def normalize(url: str) -> str:
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if not re.match(r"^[a-z]+://", url):
        return "https://" + url
    return url


def extra_cookie(host: str) -> str | None:
    """~/.config/termread/cookies/<網域>.txt 放瀏覽器複製來的 Cookie 字串（需要登入的網站用）。"""
    d = CONFIG_DIR / "cookies"
    for name in (host, host.split(".", 1)[-1]):
        f = d / f"{name}.txt"
        if f.is_file():
            return f.read_text().strip()
    return None


def fetch(url: str, *, mobile: bool = False, referer: str | None = None, timeout: int = 20) -> tuple[str, bytes]:
    url = normalize(url)
    headers = {
        "User-Agent": MOBILE_UA if mobile else DESKTOP_UA,
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    host = urllib.request.urlparse(url).hostname or ""
    _load_chrome_cookies(host)
    if cookie := extra_cookie(host):
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with _opener.open(req, timeout=timeout) as resp:
        return resp.geturl(), resp.read()


def fetch_json(url: str, **kw) -> dict:
    return json.loads(fetch(url, **kw)[1])


def cookie_value(name: str, domain_suffix: str) -> str:
    for c in jar:
        if c.name == name and c.domain.endswith(domain_suffix):
            return c.value or ""
    return ""
