"""富邦投信（websys.fsit.com.tw）fetcher。

來源：ETF 投資網「基金資產」頁（詳見 samples/README.md 第 2 節）
    GET /FubonETF/Trade/Assets.aspx?stkId=00405A&ddate=2026/09/15&lan=TW

- 純 GET、伺服器端渲染，不需 ViewState。
- ddate 的語意是「不晚於該日的最新資料」：週末/假日自動退到前一營業日；
  營業日 16:30 後同一天的持股就會出現。頁面上的「資料日期」才是真正的資料日，
  fetcher 一律以它為準，對不上 data_date 就當作尚未公告（NoDataError）。
- 摘要：基金淨資產(新台幣)、基金在外流通單位數(單位)、基金每單位淨值(新台幣)。
- 持股表：股票代碼 | 股票名稱 | 股數 | 金額 | 權重(%)，最後一列「股票合計」。
- 非股票表：項目 | 金額，目前有 現金 (TWD)、應付受益權單位買回款 (TWD)、應收(付)證券款 (TWD)。
- 申購買回清單頁（Pcf.aspx）對主動式 ETF 只有摘要、沒有持股，這裡不用。

解析函式 parse_assets_page / build_snapshot 是純函式，可用 samples/fubon/ 的檔案測試。
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from html.parser import HTMLParser

from .common import (
    FetchError,
    HttpClient,
    NoDataError,
    finalize_snapshot,
    new_snapshot,
    to_float,
    to_int,
)

log = logging.getLogger(__name__)

BASE = "https://websys.fsit.com.tw"
ASSETS_URL = BASE + "/FubonETF/Trade/Assets.aspx?stkId={stk_id}&ddate={ddate}&lan=TW"

# 非股票表「項目」-> (快照 non_stock key, 標準化代碼)。名稱先去掉幣別括號再比對。
NON_STOCK_KEYS = {
    "現金": ("cash", "CASH"),
    "應付受益權單位買回款": ("redemption_payable", "REDEMPTION_PAYABLE"),
    "應收(付)證券款": ("receivables", "APAR"),
    "應收付證券款": ("receivables", "APAR"),
}

HOLDINGS_HEADER = ["股票代碼", "股票名稱", "股數", "金額", "權重(%)"]
NON_STOCK_HEADER = ["項目", "金額"]


# ---------------------------------------------------------------- HTML helpers

class _TableCollector(HTMLParser):
    """把頁面裡每個 <table> 收成 list[list[str]]（不處理巢狀表格）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _tables(page_html: str) -> list[list[list[str]]]:
    p = _TableCollector()
    p.feed(page_html)
    p.close()
    return p.tables


def _clean(s: str) -> str:
    return " ".join(s.split())


_DATE_RE = re.compile(r"資料日期\s*[:：]\s*(\d{4})/(\d{1,2})/(\d{1,2})")
_STKID_RE = re.compile(r'id="mainContent_subMainContent_hidStkId"[^>]*value="([^"]*)"')
_SUMMARY_RE = re.compile(r"<li>\s*<p>\s*([^<]+?)\s*</p>\s*<p>\s*([^<]*?)\s*</p>\s*</li>", re.S)
_TITLE_RE = re.compile(r'<h6 class="top[^"]*">\s*([^<]+?)\s*</h6>')


# ---------------------------------------------------------------- parsing (pure)

def parse_assets_page(page_html: str) -> dict:
    """解析 Assets.aspx 的 HTML。頁面存在但沒有資料區塊時丟 NoDataError。"""
    m = _STKID_RE.search(page_html)
    if not m:
        raise FetchError("Assets.aspx: hidStkId not found (page layout changed?)")
    stk_id = m.group(1).strip()

    m = _DATE_RE.search(page_html)
    if not m:
        raise NoDataError("Assets.aspx: no 資料日期 on page (no data for this ETF/date)")
    data_date = dt.date(*(int(x) for x in m.groups()))

    summary = {_clean(k): _clean(v) for k, v in _SUMMARY_RE.findall(page_html)}
    aum = to_int(summary.get("基金淨資產(新台幣)"))
    units = to_int(summary.get("基金在外流通單位數(單位)"))
    nav = to_float(summary.get("基金每單位淨值(新台幣)"))
    if aum is None or nav is None:
        raise FetchError(f"Assets.aspx: summary block incomplete: {summary}")

    title = _TITLE_RE.search(page_html)
    title = _clean(title.group(1)) if title else ""

    holdings: list[dict] = []
    stock_total = None
    stock_weight_total = None
    non_stock: dict[str, int] = {}
    non_stock_items: list[dict] = []
    seen_holdings = seen_non_stock = False

    for table in _tables(page_html):
        if not table:
            continue
        header = [_clean(c) for c in table[0]]
        if header == HOLDINGS_HEADER:
            seen_holdings = True
            for row in table[1:]:
                if len(row) != 5:
                    raise FetchError(f"Assets.aspx: holdings row has {len(row)} cells: {row}")
                code, name, shares, amount, weight = row
                if "合計" in code or not shares:
                    stock_total = to_int(amount)
                    stock_weight_total = to_float(weight)
                    continue
                holdings.append({
                    "code": code.strip(),
                    "name": name.strip(),
                    "shares": to_int(shares),
                    "amount": to_int(amount),
                    "weight": to_float(weight),
                })
        elif header == NON_STOCK_HEADER:
            seen_non_stock = True
            for row in table[1:]:
                if len(row) != 2:
                    continue
                raw_name, amount = row
                name = re.sub(r"\s*\((TWD|NTD|新台幣)\)\s*$", "", raw_name).strip()
                key, code = NON_STOCK_KEYS.get(name, (None, None))
                if key is None:
                    log.warning("Assets.aspx: unknown non-stock item %r", raw_name)
                    code = "OTHER:" + name
                item_amount = to_int(amount) or 0
                non_stock_items.append({"code": code, "name": name, "amount": item_amount})
                if key:
                    non_stock[key] = item_amount
        else:
            log.debug("Assets.aspx: ignoring table with header %s", header)

    if not seen_holdings:
        raise FetchError("Assets.aspx: holdings table not found (page layout changed?)")
    if not seen_non_stock:
        log.warning("Assets.aspx: non-stock table not found")

    amount_sum = sum(h["amount"] or 0 for h in holdings)
    if stock_total is not None and stock_total != amount_sum:
        raise FetchError(f"Assets.aspx: holdings sum {amount_sum} != 股票合計 {stock_total}")

    return {
        "stk_id": stk_id,
        "title": title,
        "date": data_date,
        "nav": nav,
        "outstanding_units": units,
        "aum": aum,
        "stock_value": stock_total if stock_total is not None else amount_sum,
        "stock_weight_total": stock_weight_total,
        "holdings": holdings,
        "non_stock": non_stock,
        "non_stock_items": non_stock_items,
    }


def build_snapshot(etf_cfg: dict, page: dict, query_date: dt.date | None = None,
                   fetched_at: dt.datetime | None = None) -> dict:
    if page["stk_id"] and page["stk_id"] != etf_cfg["code"]:
        raise FetchError(f"Assets.aspx returned {page['stk_id']!r}, expected {etf_cfg['code']!r}")

    # 富邦在資料日當天 16:30 後就揭露，公告日 = 資料日
    snap = new_snapshot(etf_cfg, date=page["date"], posted_date=page["date"], fetched_at=fetched_at)
    snap["nav"] = page["nav"]
    snap["outstanding_units"] = page["outstanding_units"]
    snap["aum"] = page["aum"]
    snap["stock_value"] = page["stock_value"]
    snap["holdings"] = page["holdings"]
    snap["non_stock"] = page["non_stock"] or None
    snap["non_stock_items"] = page["non_stock_items"]
    snap["extra"] = {
        "stock_weight_total": page["stock_weight_total"],
        "query_date": query_date.isoformat() if query_date else None,
        "non_stock_detail": "ok" if page["non_stock"] else "not_available",
    }
    return finalize_snapshot(snap)


# ---------------------------------------------------------------- network

def fetch_assets_page(client: HttpClient, stk_id: str, ddate: dt.date) -> str:
    url = ASSETS_URL.format(stk_id=stk_id, ddate=ddate.strftime("%Y/%m/%d"))
    return client.get_text(url)


def fetch(etf_cfg: dict, data_date: dt.date, client: HttpClient | None = None) -> dict:
    """抓取「資料日 = data_date」的快照（provider 介面）。"""
    client = client or HttpClient()
    page = parse_assets_page(fetch_assets_page(client, etf_cfg["provider_id"], data_date))
    if page["date"] < data_date:
        raise NoDataError(f"page shows data date {page['date']}, {data_date} not posted yet (or holiday)")
    if page["date"] > data_date:
        raise FetchError(f"page shows data date {page['date']} for ddate={data_date}; unexpected")
    return build_snapshot(etf_cfg, page, query_date=data_date)
