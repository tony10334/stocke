"""摩根投信（am.jpmorgan.com）fetcher：00401A。

官網的「現金申購買回清單公告」Excel 一個檔就有全部：
    GET https://am.jpmorgan.com/FundsMarketingHandler/excel?type=m12_pcf&cusip=TW00000401A1&country=tw&role=twetf&locale=zh-TW&date=YYYY-MM-DD
    工作表：現金申購買回清單公告（淨資產、單位數、淨值）、基金資產 - 股票（代碼/名稱/股數/金額/權重）、
            基金資產 - 期貨、基金資產 - 選擇權、現金與約當現金
- date 是 PCF「公告日」＝ 持股資料日的下一個營業日（和統一一樣）。工作表標題括號內是資料日，fetcher 以它為準。
- provider_id 放 ISIN（cusip 參數）。
- 沒有 m12 的日期回傳的不是 xlsx（或解析不到資料日）-> 視為尚無資料。
"""

from __future__ import annotations

import datetime as dt
import logging
import re

from .common import FetchError, HttpClient, NoDataError, finalize_snapshot, new_snapshot, to_float, to_int
from .xlsx import read_xlsx

log = logging.getLogger(__name__)

EXCEL_URL = ("https://am.jpmorgan.com/FundsMarketingHandler/excel?type=m12_pcf&cusip={cusip}"
             "&country=tw&role=twetf&locale=zh-TW&date={date}")
STOCK_HEADER = ["股票代碼", "股票名稱", "股數", "金額", "權重(%)"]


def _num(s):
    if s is None:
        return None
    s = str(s).replace("%", "").replace(",", "").strip()
    return to_float(s) if s not in ("", "-") else None


def _find_sheet(sheets: dict, prefix: str):
    for name, rows in sheets.items():
        if name.startswith(prefix):
            return rows
    return None


def _title_date(rows) -> dt.date | None:
    for r in rows[:3]:
        for c in r:
            m = re.search(r"\((\d{4})-(\d{2})-(\d{2})\)", str(c or ""))
            if m:
                return dt.date(*(int(x) for x in m.groups()))
    return None


def _data_rows(rows, header_first: str):
    """回傳表頭列之後、到空列為止的資料列。"""
    out, seen = [], False
    for r in rows:
        first = (r[0] or "").strip() if r else ""
        if not seen:
            seen = first == header_first
            continue
        if not first:
            break
        out.append(r)
    return out


def parse_m12(data: bytes) -> dict:
    try:
        sheets = read_xlsx(data)
    except Exception as e:
        raise NoDataError(f"m12_pcf: not an xlsx ({e})")
    summary = _find_sheet(sheets, "現金申購買回清單")
    stock = _find_sheet(sheets, "基金資產 - 股票")
    if summary is None or stock is None:
        raise NoDataError(f"m12_pcf: sheets missing: {list(sheets)}")
    date = _title_date(stock)
    if date is None:
        raise NoDataError("m12_pcf: no data date in stock sheet title")

    aum = units = nav = None
    post_date = _title_date(summary)
    for r in summary:
        label = (r[0] or "").strip() if r else ""
        val = r[1] if len(r) > 1 else None
        if label == "基金淨資產價值(元)":
            aum = to_int(_num(val))
        elif label == "已發行受益權單位總數":
            units = to_int(_num(val))
        elif "每受益權單位淨資產價值" in label:
            nav = _num(val)
    if not aum or not units:
        raise FetchError("m12_pcf: summary sheet missing aum/units")

    holdings = []
    for r in _data_rows(stock, STOCK_HEADER[0]):
        if len(r) < 5:
            continue
        holdings.append({"code": str(r[0]).strip(), "name": (r[1] or "").strip(), "shares": to_int(_num(r[2])),
                         "amount": to_int(_num(r[3])), "weight": _num(r[4])})
    if not holdings:
        raise FetchError("m12_pcf: no stock rows")

    futures = []
    fut = _find_sheet(sheets, "基金資產 - 期貨")
    for r in _data_rows(fut or [], "商品代碼"):
        if len(r) < 4:
            continue
        w = _num(r[3])
        futures.append({"code": (r[0] or "").strip(), "name": (r[1] or "").strip(), "contracts": to_int(_num(r[2])),
                        "amount": round(w / 100 * aum) if w is not None else None, "weight": w, "month": None})
    options = []
    opt = _find_sheet(sheets, "基金資產 - 選擇權")
    for r in _data_rows(opt or [], "商品代碼"):
        if len(r) < 4:
            continue
        options.append({"code": (r[0] or "").strip(), "name": (r[1] or "").strip(), "contracts": to_int(_num(r[2])), "weight": _num(r[3])})
    cash_items = []
    cash = _find_sheet(sheets, "現金與約當現金")
    for r in _data_rows(cash or [], "名稱"):
        if len(r) < 2:
            continue
        cash_items.append({"code": "CASH", "name": (r[0] or "").strip(), "amount": to_int(_num(r[1])) or 0})
    non_stock = {"cash": sum(i["amount"] for i in cash_items)} if cash_items else {}
    return {"date": date, "posted_date": post_date, "aum": aum, "outstanding_units": units, "nav": nav,
            "stock_value": sum(h["amount"] or 0 for h in holdings), "holdings": holdings, "futures": futures,
            "options": options, "non_stock": non_stock, "non_stock_items": cash_items}


def build_snapshot(etf_cfg: dict, p: dict, fetched_at: dt.datetime | None = None) -> dict:
    snap = new_snapshot(etf_cfg, date=p["date"], posted_date=p["posted_date"] or p["date"], fetched_at=fetched_at)
    snap.update({k: p[k] for k in ("nav", "outstanding_units", "aum", "stock_value", "holdings", "futures", "non_stock_items")})
    snap["non_stock"] = p["non_stock"] or None
    snap["extra"] = {"options": p["options"], "non_stock_detail": "ok" if p["non_stock"] else "not_available"}
    return finalize_snapshot(snap)


def candidate_post_dates(data_date: dt.date, max_days: int = 7) -> list[dt.date]:
    out, d = [], data_date
    for _ in range(max_days):
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def fetch(etf_cfg: dict, data_date: dt.date, client: HttpClient | None = None) -> dict:
    client = client or HttpClient()
    cusip = etf_cfg["provider_id"]
    for post in candidate_post_dates(data_date):
        url = EXCEL_URL.format(cusip=cusip, date=post.isoformat())
        try:
            raw = client.get(url, headers={"Accept": "*/*"})
        except Exception as e:
            log.debug("%s: m12 %s failed: %s", etf_cfg["code"], post, e)
            continue
        try:
            p = parse_m12(raw)
        except NoDataError as e:
            log.debug("%s: m12 %s: %s", etf_cfg["code"], post, e)
            continue
        if p["date"] == data_date:
            return build_snapshot(etf_cfg, p)
        if p["date"] > data_date:
            break
    raise NoDataError(f"no m12_pcf with data date {data_date} (not posted yet or holiday)")
