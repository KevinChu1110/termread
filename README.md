# termread

在終端機裡讀網頁和網路小說，只留正文文字。

- 自動擷取正文（[trafilatura](https://github.com/adbar/trafilatura)），去掉導覽列、側欄、廣告
- 全螢幕閱讀介面，沿用終端機自己的配色
- 連載小說：自動找「上一章／下一章／目錄」，翻到章尾自動進下一章，背景預先抓下一章
- 書籤（含進度 % 與日期）、自動記住閱讀進度，下次接著讀
- 簡繁轉換（OpenCC，台灣用語）
- 外掛機制：替特定網站加上專屬的章節、目錄、段評、書架

## 安裝

```bash
uv tool install git+https://github.com/KevinChu1110/termread
# 或
pipx install git+https://github.com/KevinChu1110/termread
```

需要 Python 3.10+。

## 使用

```bash
termread https://example.com/novel/chapter-1   # 開啟閱讀介面
termread                                       # 開書籤／最近閱讀
termread -P https://example.com/article        # 不開介面，排版後用 less 顯示
termread --plain URL | grep 關鍵字             # 純文字輸出，方便接 pipe
termread --markdown URL > article.md           # 輸出擷取到的 markdown
```

### 按鍵

| 按鍵 | 功能 |
|---|---|
| ↑ ↓ | 捲動 |
| ← → / PgUp PgDn / 空白 | 翻頁；翻到章尾再按自動進下一章，章首再按回上一章 |
| `[` `]` | 直接上一章／下一章 |
| Home / End | 章首／章尾 |
| Enter | 目錄、書籤、書架、最近閱讀（← → 切換分頁） |
| b | 加書籤 |
| Tab / Shift+Tab | 切換有評論的段落（需外掛支援段評） |
| c / j k / m | 開關評論欄／捲動評論／載入更多 |
| t | 簡繁切換 |
| p / r | 在瀏覽器開啟購買頁／重新載入本章 |
| z 或 F11 | 隱藏狀態列 |
| o | 開新網址 |
| l | 本頁連結清單 |
| q | 離開（自動存進度） |

資料存在 `~/.local/share/termread/data.json`。

## 需要登入的網站

把瀏覽器開發者工具複製的 Cookie 字串存到 `~/.config/termread/cookies/<網域>.txt`
（例如 `example.com.txt`），抓該網域時會自動帶上。

## 外掛

替特定網站加上專屬的章節解析、目錄、段落評論、書架。

```bash
termread plugin install termread-mysite                     # 從 PyPI
termread plugin install git+https://github.com/someone/termread-mysite
termread plugin install ./termread-mysite                   # 本機資料夾
termread plugin list
termread plugin remove termread-mysite
```

外掛裝在 `~/.local/share/termread/plugins/`，與 termread 本身分開：
升級或重裝 termread 不會清掉外掛，外掛的相依套件也不會蓋掉 termread 用的版本。

### 寫一個外掛

```bash
termread plugin new mysite --site novel.example.com   # 產生 termread-mysite/ 專案骨架
# 編輯 termread-mysite/termread_mysite/__init__.py
termread plugin install ./termread-mysite
```

外掛是一般的 Python 套件，在 entry point 群組 `termread.sources` 註冊一個 `Source` 子類別：

```toml
# pyproject.toml
[project.entry-points."termread.sources"]
mysite = "termread_mysite:MySiteSource"
```

```python
from termread import net
from termread.models import Chapter, Para, TocEntry
from termread.sources import Source


class MySiteSource(Source):
    name = "mysite"

    @staticmethod
    def matches(url: str) -> bool:
        return "novel.example.com" in url

    def chapter(self, url: str) -> Chapter:
        data = net.fetch_json(url.replace("/read/", "/api/read/"))
        return Chapter(
            url=url,
            title=data["title"],
            paras=[Para(t, pid=i) for i, t in enumerate(data["paragraphs"], 1)],
            book=data["book"],
            book_key=f"mysite:{data['book_id']}",  # 同一本書共用，用來記進度
            prev_url=data.get("prev"),
            next_url=data.get("next"),
            seq=data["index"],
            total=data["total"],
        )
```

可覆寫的方法：

| 方法 | 用途 |
|---|---|
| `matches(url)` | 必要。這個網址歸不歸這個外掛 |
| `chapter(url)` | 必要。回傳一章的內容 |
| `toc(ch)` | 目錄；`TocEntry(url=None)` 是分卷標題 |
| `review_counts(ch)` / `reviews(ch, pid, page)` | 段落評論（設 `has_reviews = True`） |
| `shelf()` | 網站書架／閱讀紀錄，顯示在「書架」分頁 |
| `open_in_browser(url)` | `p` 鍵開購買頁的方式 |
| `close()` | 結束時釋放資源 |

- 不要把 `termread` 列進外掛的 `dependencies`，外掛跑在已安裝的 termread 裡
- 要沿用 Chrome 的登入狀態：外掛相依 `browser-cookie3`，並呼叫 `net.use_chrome_cookies("example.com")`
- 套件名稱建議用 `termread-<網站>`，發佈到 PyPI 或 GitHub 後，別人就能直接 `termread plugin install`
- 只想自己用、不打包的話，也可以把單一 `.py` 放到 `~/.config/termread/sources/`，
  用 `from termread.sources import register` 的 `@register` 標記類別

請只在網站服務條款允許的範圍內使用，並尊重內容的著作權。

## License

MIT
