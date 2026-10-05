"""外掛管理：termread plugin install / list / remove / new。

外掛是一般的 Python 套件，在 entry point 群組 "termread.sources" 註冊 Source 子類別。
安裝在 termread 專用的資料夾（~/.local/share/termread/plugins），啟動時加到 sys.path 最後面：
- termread 升級或重裝時外掛不會被清掉
- 核心套件永遠優先，外掛帶進來的相依套件不會蓋掉 termread 用的版本
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from importlib.metadata import distributions, entry_points
from pathlib import Path

from .store import DATA

PLUGIN_HOME = DATA.parent / "plugins"
SITE = PLUGIN_HOME / "site-packages"
SPECS = PLUGIN_HOME / "plugins.json"
GROUP = "termread.sources"


def activate() -> None:
    """把外掛資料夾加進 sys.path（放最後，核心套件優先）。"""
    if SITE.is_dir() and str(SITE) not in sys.path:
        sys.path.append(str(SITE))


def _specs() -> dict[str, str]:
    try:
        return json.loads(SPECS.read_text())
    except (OSError, ValueError):
        return {}


def _save_specs(specs: dict[str, str]) -> None:
    PLUGIN_HOME.mkdir(parents=True, exist_ok=True)
    SPECS.write_text(json.dumps(specs, ensure_ascii=False, indent=1))


def _installer(target: Path, specs: list[str]) -> list[str]:
    # 有 uv 用 uv（uv tool 建的環境沒有 pip），否則用 pip
    if uv := shutil.which("uv"):
        return [uv, "pip", "install", "--python", sys.executable, "--target", str(target), *specs]
    return [sys.executable, "-m", "pip", "install", "--target", str(target), *specs]


def _dists_in(site: Path) -> dict[str, str]:
    """site-packages 裡有註冊 termread.sources 的套件：名稱 → 版本。"""
    out = {}
    for d in distributions(path=[str(site)]):
        if any(ep.group == GROUP for ep in d.entry_points):
            out[d.metadata["Name"]] = d.version
    return out


def install(spec: str) -> int:
    """安裝一個外掛。spec 可以是 PyPI 名稱、git+https://... 或本機路徑。"""
    if Path(spec).expanduser().exists():
        spec = str(Path(spec).expanduser().resolve())
    before = _dists_in(SITE) if SITE.is_dir() else {}
    SITE.mkdir(parents=True, exist_ok=True)
    # 已裝過的外掛一併重裝，讓 --target 不會留下互相衝突的舊版相依套件
    specs = _specs()
    rc = subprocess.call(_installer(SITE, [*specs.values(), spec, "--upgrade"]))
    if rc != 0:
        return rc
    new = {k: v for k, v in _dists_in(SITE).items() if k not in before or before[k] != v}
    if not new and not any(spec == s for s in specs.values()):
        print(f"警告：{spec} 沒有註冊 {GROUP} entry point，termread 不會載入它", file=sys.stderr)
        return 1
    for name in new:
        specs[name] = spec
        print(f"已安裝外掛 {name} {new[name]}")
    _save_specs(specs)
    return 0


def remove(name: str) -> int:
    specs = _specs()
    key = next((k for k in specs if k.lower() == name.lower()), None)
    if not key:
        print(f"沒有安裝外掛 {name}", file=sys.stderr)
        return 1
    del specs[key]
    # --target 沒有可靠的移除方式：清空後重裝其餘外掛，連帶清掉不再需要的相依套件
    shutil.rmtree(SITE, ignore_errors=True)
    _save_specs(specs)
    if specs:
        SITE.mkdir(parents=True)
        rc = subprocess.call(_installer(SITE, list(specs.values())))
        if rc != 0:
            return rc
    print(f"已移除外掛 {key}")
    return 0


def list_plugins() -> int:
    from .sources import PLUGIN_DIR

    activate()
    rows = []
    for ep in entry_points(group=GROUP):
        dist = ep.dist
        rows.append((dist.metadata["Name"] if dist else ep.name, dist.version if dist else "", ep.value))
    for f in sorted(PLUGIN_DIR.glob("*.py")) if PLUGIN_DIR.is_dir() else []:
        rows.append((f.stem, "本機檔案", str(f)))
    if not rows:
        print("還沒有安裝外掛。用 termread plugin install <套件> 安裝，或 termread plugin new <名稱> 建立新外掛")
        return 0
    from rich.cells import cell_len

    def pad(text: str, width: int) -> str:  # 中文佔兩格，用顯示寬度對齊
        return text + " " * (width - cell_len(text))

    w0 = max(cell_len(r[0]) for r in rows)
    w1 = max(cell_len(r[1]) for r in rows)
    for name, ver, where in rows:
        print(f"{pad(name, w0)}  {pad(ver, w1)}  {where}")
    return 0


TEMPLATE_PYPROJECT = """[project]
name = "{dist}"
version = "0.1.0"
description = "termread plugin for {site}"
requires-python = ">=3.10"
# 不要把 termread 本身列進來：外掛跑在已安裝的 termread 裡，列了會從 PyPI 再裝一份
dependencies = []

[project.entry-points."termread.sources"]
{name} = "{module}:{cls}"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

TEMPLATE_SOURCE = '''"""termread 外掛：{site}"""

from __future__ import annotations

from termread import net
from termread.models import Chapter, Para, TocEntry
from termread.sources import Source


class {cls}(Source):
    name = "{name}"

    @staticmethod
    def matches(url: str) -> bool:
        return "{site}" in url

    def chapter(self, url: str) -> Chapter:
        final_url, html = net.fetch(url)
        # TODO：解析 html，取出章名、段落、上一章／下一章
        return Chapter(
            url=final_url,
            title="章名",
            paras=[Para("第一段"), Para("第二段")],
            book="書名",
            book_key="{name}:書的 ID",  # 同一本書共用，用來記進度
            prev_url=None,
            next_url=None,
        )

    def toc(self, ch: Chapter) -> list[TocEntry]:
        return []
'''


def new(name: str, site: str | None) -> int:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        print("名稱只能用英文、數字", file=sys.stderr)
        return 1
    dist = f"termread-{slug.replace('_', '-')}"
    module = f"termread_{slug}"
    cls = "".join(p.capitalize() for p in slug.split("_")) + "Source"
    root = Path(dist)
    if root.exists():
        print(f"{root} 已經存在", file=sys.stderr)
        return 1
    site = site or f"{slug}.example.com"
    (root / module).mkdir(parents=True)
    fmt = dict(dist=dist, name=slug, module=module, cls=cls, site=site)
    (root / "pyproject.toml").write_text(TEMPLATE_PYPROJECT.format(**fmt))
    (root / module / "__init__.py").write_text(TEMPLATE_SOURCE.format(**fmt))
    (root / "README.md").write_text(f"# {dist}\n\n[termread](https://github.com/KevinChu1110/termread) 外掛：{site}\n\n```bash\ntermread plugin install {dist}\n```\n")
    print(f"已建立 {root}/。改好 {module}/__init__.py 之後：\n  termread plugin install ./{dist}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="termread plugin", description="管理 termread 外掛")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install", help="安裝外掛（PyPI 名稱、git+https://… 或本機路徑）")
    p.add_argument("spec")
    p = sub.add_parser("remove", aliases=["uninstall"], help="移除外掛")
    p.add_argument("name")
    sub.add_parser("list", aliases=["ls"], help="列出已安裝的外掛")
    p = sub.add_parser("new", help="建立新外掛的專案骨架")
    p.add_argument("name", help="外掛名稱，例如 mysite")
    p.add_argument("--site", help="網站網域，例如 novel.example.com")
    a = ap.parse_args(argv)
    if a.cmd == "install":
        return install(a.spec)
    if a.cmd in ("remove", "uninstall"):
        return remove(a.name)
    if a.cmd in ("list", "ls"):
        return list_plugins()
    return new(a.name, a.site)
