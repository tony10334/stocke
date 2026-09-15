"""每日行情（收盤價、成交量）：證交所 STOCK_DAY 月報表。

    GET https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=20260901&stockNo=00981A

- 一次回一整個月（含當天，收盤後約 14:30 就有），欄位：
  日期(民國) | 成交股數 | 成交金額 | 開盤價 | 最高價 | 最低價 | 收盤價 | 漲跌價差 | 成交筆數 | 註記
- 沒資料時 stat != "OK"（例如查未來月份）。
- 證交所對頻繁請求會擋，兩次請求之間 sleep 一下。
- 三檔都是上市 ETF；若未來加上櫃 ETF 要另接櫃買中心。

行情存成一個檔 data/meta/prices.json：{"00981A": {"2026-09-15": {...}}, ...}
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time

from .common import FetchError, HttpClient, NoDataError, from_roc, to_float, to_int

log = logging.getLogger(__name__)

STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={ym}01&stockNo={stock_no}"
REQUEST_GAP_SECONDS = 1.5
PRICES_FILENAME = os.path.join("meta", "prices.json")

FIELD_MAP = {
    "日期": "date",
    "成交股數": "volume",
    "成交金額": "value",
    "開盤價": "open",
    "最高價": "high",
    "最低價": "low",
    "收盤價": "close",
    "漲跌價差": "change",
    "成交筆數": "transactions",
}
INT_FIELDS = {"volume", "value", "transactions"}
FLOAT_FIELDS = {"open", "high", "low", "close", "change"}


# ---------------------------------------------------------------- parsing (pure)

def _num(field: str, raw: str):
    s = (raw or "").strip()
    if s in ("", "--", "X", "－"):
        return None
    if field in INT_FIELDS:
        return to_int(s)
    if field == "change":
        # 漲跌價差可能是 "+0.45" / "-0.45" / "X0.00"(除息) / "0.00"
        s = s.replace("+", "")
        if s.startswith("X"):
            s = s[1:]
    return to_float(s)


def parse_stock_day(payload: dict) -> list[dict]:
    """把 STOCK_DAY 的回傳整理成 [{date: '2026-09-15', close: 28.86, ...}]，依日期排序。"""
    if not isinstance(payload, dict) or "stat" not in payload:
        raise FetchError("STOCK_DAY: unexpected payload")
    if payload["stat"] != "OK":
        raise NoDataError(f"STOCK_DAY: {payload['stat']}")
    fields = payload.get("fields") or []
    idx = {FIELD_MAP[f]: i for i, f in enumerate(fields) if f in FIELD_MAP}
    missing = {"date", "close"} - set(idx)
    if missing:
        raise FetchError(f"STOCK_DAY: fields changed, missing {missing}: {fields}")
    rows = []
    for raw in payload.get("data") or []:
        try:
            date = from_roc(raw[idx["date"]])
        except ValueError as e:
            raise FetchError(f"STOCK_DAY: bad date {raw[idx['date']]!r}") from e
        row = {"date": date.isoformat()}
        for key, i in idx.items():
            if key == "date":
                continue
            row[key] = _num(key, raw[i]) if i < len(raw) else None
        rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows


def months_covering(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ---------------------------------------------------------------- storage

def prices_path(data_dir: str) -> str:
    return os.path.join(data_dir, PRICES_FILENAME)


def load_prices(data_dir: str) -> dict[str, dict[str, dict]]:
    path = prices_path(data_dir)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_prices(data_dir: str, prices: dict) -> str:
    path = prices_path(data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ordered = {code: dict(sorted(days.items())) for code, days in sorted(prices.items())}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return path


def merge_rows(prices: dict, stock_no: str, rows: list[dict]) -> int:
    """把月資料併進 prices；回傳新增或變動的天數。"""
    days = prices.setdefault(stock_no, {})
    changed = 0
    for r in rows:
        rec = {k: v for k, v in r.items() if k != "date"}
        if days.get(r["date"]) != rec:
            days[r["date"]] = rec
            changed += 1
    return changed


# ---------------------------------------------------------------- network

def fetch_month(client: HttpClient, stock_no: str, year: int, month: int) -> list[dict]:
    url = STOCK_DAY_URL.format(ym=f"{year:04d}{month:02d}", stock_no=stock_no)
    raw = client.get(url, headers={"Accept": "application/json"})
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise FetchError(f"STOCK_DAY {stock_no} {year}-{month:02d}: not JSON") from e
    return parse_stock_day(payload)


def update_prices(client: HttpClient, stock_nos: list[str], start: dt.date, end: dt.date,
                  data_dir: str, sleep=time.sleep) -> dict:
    """抓 start..end 涵蓋的月份，併進 data/meta/prices.json。回傳 {stock_no: {'changed': n, 'has_end': bool}}。"""
    prices = load_prices(data_dir)
    summary = {}
    first = True
    for stock_no in stock_nos:
        changed = 0
        for y, m in months_covering(start, end):
            if not first:
                sleep(REQUEST_GAP_SECONDS)
            first = False
            try:
                rows = fetch_month(client, stock_no, y, m)
            except NoDataError as e:
                log.warning("%s %d-%02d: no price data (%s)", stock_no, y, m, e)
                continue
            changed += merge_rows(prices, stock_no, rows)
        has_end = end.isoformat() in prices.get(stock_no, {})
        summary[stock_no] = {"changed": changed, "has_end": has_end}
    save_prices(data_dir, prices)
    return summary
