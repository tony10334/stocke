"""野村投信（nomurafunds.com.tw）fetcher：00980A、00985A、00999A。

    POST https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets
    {"FundID": "00980A", "SearchDate": "2026-09-21"}

- SearchDate 就是資料日（YYYY-MM-DD）。非營業日 / 尚未公告 -> StatusCode 5「此搜尋條件尚無相關資料」。
- 回傳 Entries.Data.FundAsset {Aum, Units, Nav, NavDate} 與 Table[]：
  '股票'  rows [代號, 名稱, 股數, 權重%]
  '期貨'  rows [代碼, 名稱, 口數, 權重%]
  ''      rows [項目, 金額字串, 幣別, 金額(原幣)]  項目含 股票 / 期貨 / 現金 / 保證金 / 應收(付)證券款 …
- 持股沒有金額欄，以 權重 × 淨資產 推算。
- 歷史可查（至少到 2026-06）。
"""

from __future__ import annotations

import datetime as dt
import logging
import re

from .common import FetchError, HttpClient, NoDataError, finalize_snapshot, new_snapshot, to_float, to_int

log = logging.getLogger(__name__)

API_URL = "https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets"
REFERER = "https://www.nomurafunds.com.tw/ETFWEB/product-description?fundNo={code}&tab=Shareholding"

NON_STOCK_KEYS = {
    "現金": ("cash", "CASH"),
    "保證金": ("margin", "MARGIN"),
    "期貨保證金": ("margin", "MARGIN"),
    "附買回債券": ("rp", "RP"),
    "應收(付)證券款": ("receivables", "APAR"),
    "應收付證券款": ("receivables", "APAR"),
    "應付受益權單位買回款": ("redemption_payable", "REDEMPTION_PAYABLE"),
}
SKIP_ITEMS = {"股票", "期貨", "選擇權"}


def _parse_date(s: str | None) -> dt.date | None:
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s or "")
    return dt.date(*(int(x) for x in m.groups())) if m else None


def parse_assets(payload: dict) -> dict:
    if not isinstance(payload, dict) or "StatusCode" not in payload:
        raise FetchError("GetFundAssets: unexpected payload")
    if payload["StatusCode"] != 0:
        raise NoDataError(f"GetFundAssets: {payload.get('Message') or payload['StatusCode']}")
    entries = payload.get("Entries") or {}
    data = entries.get("Data") or {}
    fa = data.get("FundAsset") or {}
    date = _parse_date(fa.get("NavDate"))
    if date is None:
        raise NoDataError("GetFundAssets: no NavDate")
    aum, units, nav = to_int(fa.get("Aum")), to_int(fa.get("Units")), to_float(fa.get("Nav"))
    if not aum or not units:
        raise FetchError(f"GetFundAssets: FundAsset incomplete: {fa}")

    holdings, futures, items, non_stock = [], [], [], {}
    stock_value = futures_value = None
    for table in data.get("Table") or []:
        title = (table.get("TableTitle") or "").strip()
        rows = table.get("Rows") or []
        if title == "股票":
            for r in rows:
                if len(r) < 4:
                    continue
                w = to_float(r[3])
                holdings.append({"code": str(r[0]).strip(), "name": str(r[1]).strip(), "shares": to_int(r[2]),
                                 "amount": round(w / 100 * aum) if w is not None else None, "weight": w})
        elif title == "期貨":
            for r in rows:
                if len(r) < 4:
                    continue
                w = to_float(r[3])
                futures.append({"code": str(r[0]).strip(), "name": str(r[1]).strip(), "contracts": to_int(r[2]),
                                "amount": round(w / 100 * aum) if w is not None else None, "weight": w, "month": None})
        elif title == "":
            for r in rows:
                if len(r) < 2:
                    continue
                name = str(r[0]).strip()
                amount = to_int(r[3]) if len(r) > 3 and r[3] not in (None, "") else to_int(re.sub(r"[^\d.\-]", "", str(r[1])))
                if name == "股票":
                    stock_value = amount
                elif name == "期貨":
                    futures_value = amount
                elif name in SKIP_ITEMS:
                    continue
                else:
                    key, code = NON_STOCK_KEYS.get(name, (None, "OTHER:" + name))
                    items.append({"code": code, "name": name, "amount": amount or 0})
                    if key:
                        non_stock[key] = (non_stock.get(key) or 0) + (amount or 0)
        else:
            log.warning("GetFundAssets: unknown table %r", title)
    if not holdings:
        raise FetchError("GetFundAssets: no stock rows")
    if stock_value is None:
        stock_value = sum(h["amount"] or 0 for h in holdings)
    return {"fund_id": entries.get("FundID"), "date": date, "aum": aum, "outstanding_units": units, "nav": nav,
            "stock_value": stock_value, "futures_value": futures_value, "holdings": holdings, "futures": futures,
            "non_stock": non_stock, "non_stock_items": items}


def build_snapshot(etf_cfg: dict, p: dict, fetched_at: dt.datetime | None = None) -> dict:
    if p.get("fund_id") and p["fund_id"] != etf_cfg["provider_id"]:
        raise FetchError(f"GetFundAssets returned {p['fund_id']!r}, expected {etf_cfg['provider_id']!r}")
    snap = new_snapshot(etf_cfg, date=p["date"], posted_date=p["date"], fetched_at=fetched_at)
    snap.update({k: p[k] for k in ("nav", "outstanding_units", "aum", "stock_value", "holdings", "futures", "non_stock_items")})
    snap["non_stock"] = p["non_stock"] or None
    snap["extra"] = {"futures_notional": p["futures_value"], "non_stock_detail": "ok" if p["non_stock"] else "not_available",
                     "amount_basis": "weight_x_aum"}
    return finalize_snapshot(snap)


def fetch(etf_cfg: dict, data_date: dt.date, client: HttpClient | None = None) -> dict:
    client = client or HttpClient()
    code = etf_cfg["provider_id"]
    payload = client.post_json(API_URL, {"FundID": code, "SearchDate": data_date.isoformat()},
                               headers={"Referer": REFERER.format(code=code)})
    p = parse_assets(payload)
    if p["date"] != data_date:
        raise NoDataError(f"GetFundAssets returned data date {p['date']} for {data_date}")
    return build_snapshot(etf_cfg, p)
