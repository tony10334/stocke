"""國泰投信（cathaysite.com.tw）fetcher：00400A。

官網是前端渲染，資料來自 cwapi（GET，帶 FundCode 與 SearchDate=YYYY-MM-DD）：
    /api/ETF/GetETFAssets          -> {preDate, fundNav(淨資產), fundOutstandingShares, fundPerNav}
    /api/ETF/GetETFDetailStockList -> [{stockCode, stockName, volumn(股數), weights}]
    /api/ETF/GetETFDetailFutureList
    /api/ETF/GetETFOptionList      -> 選擇權（掩護性買權）
    /api/ETF/GetETFDetailBalList   -> [{item: 現金/保證金/申贖應付款/股票/選擇權, amount: "(TWD) $ 997,071,596"}]
- FundCode 是國泰內部代碼（00400A = EA），放在 etfs.json 的 provider_id。
- preDate 是實際資料日；查未來日期會退回最新一天，非營業日回「查無資料」。fetcher 以 preDate == 要求日為準。
- 持股沒有金額欄，以 權重 × 淨資產 推算；股票合計用 BalList 的「股票」。
"""

from __future__ import annotations

import datetime as dt
import logging
import re

from .common import FetchError, HttpClient, NoDataError, finalize_snapshot, new_snapshot, to_float, to_int

log = logging.getLogger(__name__)

BASE = "https://cwapi.cathaysite.com.tw/api/ETF/"
HEADERS = {"Accept": "application/json", "Origin": "https://www.cathaysite.com.tw", "Referer": "https://www.cathaysite.com.tw/"}
INFO_URL = "https://www.cathaysite.com.tw/ETF/detail/E{code}?tab=etf3"

NON_STOCK_KEYS = {
    "現金": ("cash", "CASH"),
    "保證金": ("margin", "MARGIN"),
    "申贖應付款": ("redemption_payable", "REDEMPTION_PAYABLE"),
    "應收(付)證券款": ("receivables", "APAR"),
    "應收付證券款": ("receivables", "APAR"),
    "附買回債券": ("rp", "RP"),
}
SKIP_ITEMS = {"股票", "選擇權", "期貨", "債券", "基金", "ETF"}


def _money(s) -> int | None:
    """'(TWD) $ -15,252,371' / 'NT$28,059,190,858' / '29,312,424,553' -> int"""
    if s is None:
        return None
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(s).replace("−", "-"))
    return to_int(m.group()) if m else None


def _ok(payload: dict, what: str) -> list | dict:
    if not isinstance(payload, dict) or "returnCode" not in payload:
        raise FetchError(f"{what}: unexpected payload")
    if payload.get("returnCode") == "4005" or (payload.get("result") is None and not payload.get("success")):
        raise NoDataError(f"{what}: {payload.get('returnMessage')}")
    if not payload.get("success"):
        raise FetchError(f"{what}: {payload.get('returnMessage')}")
    return payload.get("result")


def parse_assets(assets: dict, stocks: dict, bal: dict, options: dict | None = None, futures: dict | None = None) -> dict:
    a = _ok(assets, "GetETFAssets")
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", a.get("preDate") or "")
    if not m:
        raise NoDataError("GetETFAssets: no preDate")
    date = dt.date(*(int(x) for x in m.groups()))
    aum, units, nav = _money(a.get("fundNav")), _money(a.get("fundOutstandingShares")), to_float(a.get("fundPerNav"))
    if not aum or not units:
        raise FetchError(f"GetETFAssets incomplete: {a}")

    holdings = []
    for r in _ok(stocks, "GetETFDetailStockList") or []:
        w = to_float(r.get("weights"))
        holdings.append({"code": str(r.get("stockCode")).strip(), "name": (r.get("stockName") or "").strip(),
                         "shares": _money(r.get("volumn")), "amount": round(w / 100 * aum) if w is not None else None, "weight": w})
    if not holdings:
        raise FetchError("GetETFDetailStockList: no rows")

    items, non_stock, stock_value = [], {}, None
    for r in _ok(bal, "GetETFDetailBalList") or []:
        name = (r.get("item") or "").strip()
        amount = _money(r.get("amount"))
        if name == "股票":
            stock_value = amount
        elif name in SKIP_ITEMS:
            continue
        else:
            key, code = NON_STOCK_KEYS.get(name, (None, "OTHER:" + name))
            items.append({"code": code, "name": name, "amount": amount or 0})
            if key:
                non_stock[key] = (non_stock.get(key) or 0) + (amount or 0)
    if stock_value is None:
        stock_value = sum(h["amount"] or 0 for h in holdings)

    opts = []
    if options is not None:
        try:
            for r in _ok(options, "GetETFOptionList") or []:
                opts.append({"code": r.get("option_No"), "name": r.get("fT_Name"), "call_put": r.get("callPut"),
                             "strike": r.get("strike_Price"), "contracts": r.get("volumn"), "month": r.get("option_Date"),
                             "notional": r.get("nT_Mkval"), "weight": to_float(r.get("weight"))})
        except NoDataError:
            pass
    futs = []
    if futures is not None:
        try:
            for r in _ok(futures, "GetETFDetailFutureList") or []:
                w = to_float(r.get("weight"))
                futs.append({"code": r.get("future_No") or r.get("option_No") or r.get("code"), "name": r.get("fT_Name") or r.get("name"),
                             "contracts": to_int(r.get("volumn")), "amount": r.get("nT_Mkval"), "weight": w, "month": r.get("future_Date") or r.get("option_Date")})
        except NoDataError:
            pass
    return {"date": date, "aum": aum, "outstanding_units": units, "nav": nav, "stock_value": stock_value,
            "holdings": holdings, "futures": futs, "options": opts, "non_stock": non_stock, "non_stock_items": items}


def build_snapshot(etf_cfg: dict, p: dict, fetched_at: dt.datetime | None = None) -> dict:
    snap = new_snapshot(etf_cfg, date=p["date"], posted_date=p["date"], fetched_at=fetched_at)
    snap.update({k: p[k] for k in ("nav", "outstanding_units", "aum", "stock_value", "holdings", "futures", "non_stock_items")})
    snap["non_stock"] = p["non_stock"] or None
    snap["extra"] = {"options": p["options"], "non_stock_detail": "ok" if p["non_stock"] else "not_available", "amount_basis": "weight_x_aum"}
    return finalize_snapshot(snap)


def _get(client: HttpClient, endpoint: str, fund_code: str, date: dt.date) -> dict:
    url = f"{BASE}{endpoint}?FundCode={fund_code}&SearchDate={date.isoformat()}&status=1"
    raw = client.get(url, headers=HEADERS)
    import json
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise FetchError(f"{endpoint}: not JSON") from e


def fetch(etf_cfg: dict, data_date: dt.date, client: HttpClient | None = None) -> dict:
    client = client or HttpClient()
    fc = etf_cfg["provider_id"]
    assets = _get(client, "GetETFAssets", fc, data_date)
    a = _ok(assets, "GetETFAssets")
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", a.get("preDate") or "")
    if m and dt.date(*(int(x) for x in m.groups())) != data_date:
        raise NoDataError(f"GetETFAssets preDate {a.get('preDate')} != {data_date} (not posted yet or holiday)")
    stocks = _get(client, "GetETFDetailStockList", fc, data_date)
    bal = _get(client, "GetETFDetailBalList", fc, data_date)
    options = _get(client, "GetETFOptionList", fc, data_date)
    futures = _get(client, "GetETFDetailFutureList", fc, data_date)
    return build_snapshot(etf_cfg, parse_assets(assets, stocks, bal, options, futures))
