"""統一投信（ezmoney.com.tw）fetcher。

兩個來源（詳見 samples/README.md 第 1 節）：

A. 申購買回清單 JSON API
   POST /ETF/Transaction/GetPCF  {"fundCode": "49YTW", "date": "115/09/16", "specificDate": true}
   - date 是民國年的「公告日」；回傳的持股資料日（TranDate）是公告日的前一營業日。
   - 每個營業日 16:30 後，「當天」的持股就以「下一營業日」為公告日掛上去；
     specificDate=false 直接回最新一筆。
   - 非營業日 / 尚未公告：所有 Amount 為 0 -> NoDataError。
   - 有持股（ST）與期貨（GD），沒有現金明細。
   - 可查歷史。
   - 日期欄位：urllib 拿到 ISO 字串，瀏覽器 XHR 拿到 '/Date(ms)/'，兩種都要吃。

B. 基金頁內嵌的投資組合 JSON
   GET /ETF/Fund/Info?fundCode=49YTW  ->  <div id="DataAsset" data-content="[...]">
   - 含 CASH / GDM（期貨保證金）/ RP / APAR（應收付證券款）。
   - 只有最新一天（16:30 後更新為當天）。資料日與 A 對得上時才併入快照，否則 non_stock 留 null。

解析函式（parse_pcf / parse_data_asset / build_snapshot）都是純函式，
可以直接用 samples/uni/ 裡的檔案測試。
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import re

from .common import (
    FetchError,
    HttpClient,
    NoDataError,
    finalize_snapshot,
    new_snapshot,
    parse_any_date,
    parse_iso_date,
    to_float,
    to_int,
    to_roc,
)

log = logging.getLogger(__name__)

BASE = "https://www.ezmoney.com.tw"
PCF_PAGE_URL = BASE + "/ETF/Transaction/PCF"
PCF_API_URL = BASE + "/ETF/Transaction/GetPCF"
FUND_INFO_URL = BASE + "/ETF/Fund/Info?fundCode={fund_code}"

# DataAsset 的 AssetCode -> 快照 non_stock 的 key
NON_STOCK_KEYS = {
    "CASH": "cash",
    "GDM": "margin",
    "RP": "rp",
    "APAR": "receivables",
}

# PCF 摘要裡要保留到 extra 的項目
PCF_EXTRA_CODES = {
    "PRE_AMT": "pre_subscription_amount",   # 預收申購總價金
    "DIFF_UNIT": "units_change",            # 與前日已發行單位差異數
    "FUND_BASEUNIT": "creation_unit",       # 每申購/買回基數之單位數
    "BASEUNIT_MRK_VAL": "creation_unit_value",
    "DIFF_ACT_AMT": "creation_cash_diff",
    "ACT_AMT": "creation_cash_actual",
    "NAV_PEOPLE": "holders",                # 受益人數（上月底）
}


# ---------------------------------------------------------------- parsing (pure)

def parse_pcf(payload: dict) -> dict:
    """把 GetPCF 的回傳整理成扁平 dict。全零時丟 NoDataError。"""
    if not isinstance(payload, dict) or "pcf" not in payload:
        raise FetchError(f"GetPCF: unexpected payload keys {list(payload)[:6] if isinstance(payload, dict) else type(payload)}")

    pcf = {row["PCFCode"]: row for row in payload.get("pcf") or []}
    nav_row = pcf.get("NAV")
    if not nav_row or not nav_row.get("Amount"):
        raise NoDataError("GetPCF: NAV is 0 / missing (non-business day or not yet posted)")

    # 日期欄位：curl/urllib 拿到 ISO 字串，瀏覽器 XHR 拿到 '/Date(ms)/'，兩種都處理
    tran_date = parse_any_date(nav_row.get("TranDate"))
    post_date = parse_any_date(nav_row.get("PostDate"))
    if tran_date is None:
        raise FetchError("GetPCF: cannot parse TranDate")

    fund = payload.get("fund") or {}
    stock_no = (fund.get("sStockNo") or "").strip()

    holdings: list[dict] = []
    futures: list[dict] = []
    stock_value = None
    futures_value = None
    for group in payload.get("asset") or []:
        code = group.get("AssetCode")
        details = group.get("Details") or []
        if code == "ST":
            stock_value = to_int(group.get("Value"))
            for d in details:
                holdings.append({
                    "code": str(d["DetailCode"]).strip(),
                    "name": str(d.get("DetailName") or "").strip(),
                    "shares": to_int(d.get("Share")),
                    "amount": to_int(d.get("Amount")),
                    "weight": to_float(d.get("NavRate")),
                })
        elif code == "GD":
            futures_value = to_int(group.get("Value"))
            for d in details:
                futures.append({
                    "code": str(d["DetailCode"]).strip(),
                    "name": str(d.get("DetailName") or "").strip(),
                    "contracts": to_int(d.get("Share")),
                    "amount": to_int(d.get("Amount")),
                    "weight": to_float(d.get("NavRate")),
                    "month": (d.get("MTH") or "").strip() or None,
                    "position": (d.get("Position") or "").strip() or None,
                })
        else:
            log.warning("GetPCF: unknown asset group %r (%s)", code, group.get("AssetName"))

    extra = {}
    for code, key in PCF_EXTRA_CODES.items():
        row = pcf.get(code)
        if row is not None:
            extra[key] = to_int(row.get("Amount"))
            vd = (row.get("ValueDate") or "").strip()
            if vd:
                extra[key + "_as_of_roc"] = vd

    return {
        "stock_no": stock_no,
        "fund_code": (fund.get("sFundCode") or "").strip(),
        "date": tran_date,
        "posted_date": post_date,
        "nav": to_float(pcf["P_UNIT"]["Amount"]) if "P_UNIT" in pcf else None,
        "outstanding_units": to_int(pcf["OUT_UNIT"]["Amount"]) if "OUT_UNIT" in pcf else None,
        "aum": to_int(nav_row["Amount"]),
        "stock_value": stock_value,
        "futures_value": futures_value,
        "holdings": holdings,
        "futures": futures,
        "extra": extra,
    }


_DATA_ASSET_RE = re.compile(r'<div\s+id="DataAsset"\s+data-content="([^"]*)"', re.I)


def parse_data_asset(page_html: str) -> dict:
    """從基金頁 HTML 取出內嵌的 DataAsset JSON 並整理。"""
    m = _DATA_ASSET_RE.search(page_html)
    if not m:
        raise FetchError("Fund/Info: DataAsset block not found (page layout changed?)")
    try:
        groups = json.loads(html.unescape(m.group(1)))
    except ValueError as e:
        raise FetchError("Fund/Info: DataAsset is not valid JSON") from e

    by_code = {g.get("AssetCode"): g for g in groups}
    items = []
    non_stock = {}
    for g in groups:
        code = g.get("AssetCode")
        if code in NON_STOCK_KEYS:
            amount = to_int(g.get("Value")) or 0
            items.append({"code": code, "name": (g.get("AssetName") or "").strip(), "amount": amount})
            non_stock[NON_STOCK_KEYS[code]] = amount

    # 資料日：股票明細的 TranDate（group 層的 EditDate 對空群組會是抓取當下時間，不可靠）
    date = None
    st = by_code.get("ST") or {}
    for d in st.get("Details") or []:
        date = parse_iso_date(d.get("TranDate")) or date
        if date:
            break
    if date is None:
        date = parse_iso_date(st.get("EditDate"))

    return {
        "date": date,
        "aum": to_int((by_code.get("NAV") or {}).get("Value")),
        "outstanding_units": to_int((by_code.get("OUT_UNIT") or {}).get("Value")),
        "nav": to_float((by_code.get("P_UNIT") or {}).get("Value")),
        "stock_value": to_int(st.get("Value")),
        "non_stock": non_stock,
        "non_stock_items": items,
        "holdings_count": len(st.get("Details") or []),
    }


def build_snapshot(etf_cfg: dict, pcf: dict, asset: dict | None,
                   fetched_at: dt.datetime | None = None) -> dict:
    """把 A（必要）與 B（可選）合併成快照。"""
    if pcf["stock_no"] and pcf["stock_no"] != etf_cfg["code"]:
        raise FetchError(f"GetPCF returned {pcf['stock_no']!r}, expected {etf_cfg['code']!r}")

    snap = new_snapshot(etf_cfg, date=pcf["date"], posted_date=pcf["posted_date"], fetched_at=fetched_at)
    snap["nav"] = pcf["nav"]
    snap["outstanding_units"] = pcf["outstanding_units"]
    snap["aum"] = pcf["aum"]
    snap["stock_value"] = pcf["stock_value"]
    snap["holdings"] = pcf["holdings"]
    snap["futures"] = pcf["futures"]
    snap["extra"] = dict(pcf["extra"])
    snap["extra"]["futures_notional"] = pcf["futures_value"]

    if asset is None:
        snap["extra"]["non_stock_detail"] = "not_available"
    elif asset["date"] != pcf["date"]:
        log.warning("%s: DataAsset date %s != PCF date %s; cash breakdown skipped",
                    etf_cfg["code"], asset["date"], pcf["date"])
        snap["extra"]["non_stock_detail"] = f"date_mismatch:{asset['date']}"
    else:
        if asset["aum"] is not None and pcf["aum"] is not None and asset["aum"] != pcf["aum"]:
            raise FetchError(f"{etf_cfg['code']}: aum differs between PCF ({pcf['aum']}) and DataAsset ({asset['aum']})")
        snap["non_stock"] = asset["non_stock"] or None
        snap["non_stock_items"] = asset["non_stock_items"]
        snap["extra"]["non_stock_detail"] = "ok"

    return finalize_snapshot(snap)


# ---------------------------------------------------------------- network

def candidate_post_dates(data_date: dt.date, max_days: int = 7) -> list[dt.date]:
    """資料日 D 的 PCF 會掛在 D 之後第一個營業日的公告日；連假不確定，往後多試幾個平日。"""
    out = []
    d = data_date
    for _ in range(max_days):
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def warm_up(client: HttpClient) -> None:
    """先 GET 一次 PCF 頁面拿 WAF cookie（拿不到也沒關係，post_json 會處理 307）。"""
    if getattr(client, "_uni_warmed", False):
        return
    try:
        client.get(PCF_PAGE_URL)
    except Exception as e:
        log.debug("warm-up GET failed: %s", e)
    client._uni_warmed = True


def fetch_pcf(client: HttpClient, fund_code: str, post_date: dt.date | None) -> dict:
    """呼叫 GetPCF。post_date=None 表示取最新一筆（specificDate=false）。"""
    warm_up(client)
    payload = {
        "fundCode": fund_code,
        "date": to_roc(post_date or dt.date.today()),
        "specificDate": post_date is not None,
    }
    return client.post_json(PCF_API_URL, payload, headers={"Referer": PCF_PAGE_URL, "Origin": BASE})


def fetch_data_asset(client: HttpClient, fund_code: str) -> str:
    return client.get_text(FUND_INFO_URL.format(fund_code=fund_code))


def fetch(etf_cfg: dict, data_date: dt.date, client: HttpClient | None = None) -> dict:
    """抓取「資料日 = data_date」的快照（provider 介面）。

    流程：
    1. 先取最新一筆 PCF。資料日剛好是 data_date -> 用它（每日 17:30 的正常路徑）。
       最新資料日還早於 data_date -> 投信尚未公告 -> NoDataError。
    2. 否則是回補歷史：從 data_date 往後的平日逐一當公告日查，直到 TranDate 對上。
    3. 只有在 data_date 就是最新資料日時，基金頁的 DataAsset（現金明細）才對得上，才去抓。
    """
    client = client or HttpClient()
    fund_code = etf_cfg["provider_id"]
    code = etf_cfg["code"]

    latest = parse_pcf(fetch_pcf(client, fund_code, None))  # 全零時丟 NoDataError
    if latest["date"] == data_date:
        pcf = latest
    elif latest["date"] < data_date:
        raise NoDataError(f"latest PCF data date is {latest['date']}, {data_date} not posted yet")
    else:
        pcf = None
        for post_date in candidate_post_dates(data_date):
            try:
                cand = parse_pcf(fetch_pcf(client, fund_code, post_date))
            except NoDataError:
                continue
            if cand["date"] == data_date:
                pcf = cand
                break
            if cand["date"] > data_date:
                break  # 已經跳過去了：data_date 不是營業日
        if pcf is None:
            raise NoDataError(f"no PCF with data date {data_date} (holiday?)")

    asset = None
    if pcf["date"] == latest["date"]:
        try:
            asset = parse_data_asset(fetch_data_asset(client, fund_code))
        except FetchError as e:
            # 現金明細抓不到不算致命，先保留持股快照
            log.warning("%s: DataAsset unavailable: %s", code, e)
    else:
        log.info("%s: %s is historical; cash breakdown only exists for latest day (%s)", code, data_date, latest["date"])

    return build_snapshot(etf_cfg, pcf, asset)
