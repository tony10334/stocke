"""分析層的純函式（不碰網路；只讀快照 dict）。

規則（對應規格第 4 節）：
1. 非股票水位 % = non_stock_total / aum * 100；現金 % = non_stock.cash / aum * 100（有揭露時）。
2. 每單位持股數 = shares / outstanding_units。日對日比較用它，排除申贖造成的規模效果。
   加/減碼要同時滿足：股數真的有變、且每單位變動 >= min_per_unit_change_pct。
   （只有單位數變、股數沒變，是申贖造成的全體等比例漂移，不是經理人決策。）
3. 異動只比較同一檔 ETF「自己相鄰的兩個快照日」；共同訊號只比較「全體日期序列中相鄰兩天」，
   缺其中一天的 ETF 不納入該區間（避免假訊號）。
4. 共同加碼：同一區間內 >= co_signal_min_etfs 檔對同一股票 new/add 即觸發；共同減碼同理。
5. 公司行動：同一區間內 >= 2 檔對同一股票的「股數倍數」一致（誤差 corporate_action_tolerance_pct 內）
   且倍數 >= corporate_action_min_ratio（或 <= 1/min_ratio），標記 corporate_action，不算加減碼。
6. 反向索引：股票 -> 持有它的 ETF + 最新股數/權重/每單位股數 + 最近一次異動。
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import logging
import os
from collections import defaultdict

log = logging.getLogger(__name__)

SITE_SCHEMA_VERSION = 1

DEFAULT_CONFIG = {
    "co_signal_min_etfs": 2,            # 幾檔以上同向才算共同訊號（Phase 1 只有 3 檔，先用 2）
    "min_per_unit_change_pct": 0.5,     # 每單位持股數變動超過此 % 才算加/減碼（過濾申贖與湊整雜訊）
    "corporate_action_min_ratio": 1.5,  # 股數倍數 >= 1.5 或 <= 1/1.5 才可能是公司行動
    "corporate_action_tolerance_pct": 1.0,  # 各 ETF 倍數彼此差異 <= 1% 視為一致
    "top_holdings": 3,                  # 總覽卡片顯示前幾大
    "detail_change_intervals": 30,      # 單檔明細保留最近幾個區間的異動（反向索引仍看完整歷史）
    "history_days": 30,                 # holdings_history 保留最近幾個資料日（前端比較窗口最多 10 天 + 歷史 6 個區間）
}

ACTION_ORDER = {"new": 0, "add": 1, "reduce": 2, "exit": 3, "corporate_action": 4}
ADD_ACTIONS = {"new", "add"}
REDUCE_ACTIONS = {"reduce", "exit"}


# ---------------------------------------------------------------- loading

def load_snapshots(data_dir: str, etf_codes: list[str]) -> dict[str, dict[str, dict]]:
    """回傳 {etf: {date: snapshot}}；檔名與內容不一致的檔會被跳過並記 warning。"""
    out: dict[str, dict[str, dict]] = {}
    for code in etf_codes:
        snaps: dict[str, dict] = {}
        for path in sorted(glob.glob(os.path.join(data_dir, code, "????-??-??.json"))):
            date = os.path.basename(path)[:-5]
            try:
                with open(path, encoding="utf-8") as f:
                    s = json.load(f)
            except ValueError as e:
                log.warning("skip %s: invalid JSON (%s)", path, e)
                continue
            if s.get("etf") != code or s.get("date") != date:
                log.warning("skip %s: etf/date inside file (%s/%s) don't match path", path, s.get("etf"), s.get("date"))
                continue
            if not s.get("holdings") or not s.get("outstanding_units"):
                log.warning("skip %s: no holdings / outstanding_units", path)
                continue
            snaps[date] = s
        out[code] = snaps
    return out


# ---------------------------------------------------------------- basic metrics

def pct(part, whole, digits: int = 2) -> float | None:
    if part is None or not whole:
        return None
    return round(part / whole * 100, digits)


def non_stock_pct(snap: dict) -> float | None:
    return pct(snap.get("non_stock_total"), snap.get("aum"))


def cash_pct(snap: dict) -> float | None:
    ns = snap.get("non_stock") or {}
    return pct(ns.get("cash"), snap.get("aum"))


def per_unit(shares, units) -> float | None:
    if shares is None or not units:
        return None
    return shares / units


# ---------------------------------------------------------------- diff

def diff_holdings(prev: dict, curr: dict, cfg: dict | None = None) -> list[dict]:
    """比較同一檔 ETF 的兩個快照，回傳有意義的異動（依 new/add/reduce/exit 分類）。"""
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    thr = cfg["min_per_unit_change_pct"]
    units_p, units_c = prev["outstanding_units"], curr["outstanding_units"]
    hp = {h["code"]: h for h in prev["holdings"]}
    hc = {h["code"]: h for h in curr["holdings"]}
    items = []
    for code in set(hp) | set(hc):
        p, c = hp.get(code), hc.get(code)
        sp = (p or {}).get("shares") or 0
        sc = (c or {}).get("shares") or 0
        pu_p = per_unit(sp, units_p) if p else None
        pu_c = per_unit(sc, units_c) if c else None
        if p and c:
            if not pu_p:
                continue
            # 股數沒動就不是經理人的決策：申贖讓單位數變了，每單位會全體等比例漂移，不算加減碼
            if sc == sp:
                continue
            chg = (pu_c - pu_p) / pu_p * 100
            if chg >= thr:
                action = "add"
            elif chg <= -thr:
                action = "reduce"
            else:
                continue
        elif c:
            action, chg = "new", None
        else:
            action, chg = "exit", -100.0
        items.append({
            "code": code,
            "name": (c or p).get("name"),
            "action": action,
            "shares_prev": sp,
            "shares_curr": sc,
            "shares_change": sc - sp,
            "per_unit_prev": pu_p,
            "per_unit_curr": pu_c,
            "per_unit_change_pct": round(chg, 2) if chg is not None else None,
            "weight_prev": (p or {}).get("weight"),
            "weight_curr": (c or {}).get("weight"),
            "amount_curr": (c or {}).get("amount"),
        })
    items.sort(key=lambda it: (ACTION_ORDER[it["action"]], -(it["weight_curr"] or it["weight_prev"] or 0), it["code"]))
    return items


def detect_corporate_actions(diffs_by_etf: dict[str, list[dict]], cfg: dict | None = None) -> dict[str, dict]:
    """同一區間內，多檔 ETF 對同一股票出現一致的大倍數變化 -> {code: {ratio, etfs}}。"""
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    min_ratio = cfg["corporate_action_min_ratio"]
    tol = cfg["corporate_action_tolerance_pct"]
    by_code: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for etf, items in diffs_by_etf.items():
        for it in items:
            if it["shares_prev"] and it["shares_curr"]:
                by_code[it["code"]].append((etf, it["shares_curr"] / it["shares_prev"]))
    found = {}
    for code, lst in by_code.items():
        if len(lst) < 2:
            continue
        ratios = [r for _, r in lst]
        rmin, rmax = min(ratios), max(ratios)
        if (rmax - rmin) / rmin * 100 > tol:
            continue
        mean = sum(ratios) / len(ratios)
        if mean >= min_ratio or mean <= 1 / min_ratio:
            found[code] = {"ratio": round(mean, 4), "etfs": sorted(e for e, _ in lst)}
    return found


# ---------------------------------------------------------------- site data

def premium_pct(close, nav) -> float | None:
    """折溢價 % = (市價 - 淨值) / 淨值 * 100。"""
    if close is None or not nav:
        return None
    return round((close - nav) / nav * 100, 2)


def load_prices(data_dir: str) -> dict[str, dict[str, dict]]:
    """data/meta/prices.json -> {etf: {date: {close, volume, ...}}}；沒有檔案回 {}。"""
    path = os.path.join(data_dir, "meta", "prices.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_site_data(etfs: list[dict], snapshots: dict[str, dict[str, dict]],
                    cfg: dict | None = None, generated_at: dt.datetime | None = None,
                    prices: dict[str, dict[str, dict]] | None = None) -> dict:
    """把所有快照（與行情）彙整成前端用的一份 JSON。"""
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    prices = prices or {}
    etf_by_code = {e["code"]: e for e in etfs}
    codes = [e["code"] for e in etfs if e["code"] in snapshots]
    all_dates = sorted(set().union(*[set(snapshots[c]) for c in codes]) if codes else set())

    # 1. 每檔自己相鄰快照的異動
    own_diffs: dict[tuple[str, str, str], list[dict]] = {}
    for code in codes:
        ds = sorted(snapshots[code])
        for a, b in zip(ds, ds[1:]):
            own_diffs[(code, a, b)] = diff_holdings(snapshots[code][a], snapshots[code][b], cfg)

    # 2. 全體日期序列上相鄰兩天的區間：公司行動標記 + 共同訊號
    intervals = []
    for a, b in zip(all_dates, all_dates[1:]):
        participating = {c: own_diffs[(c, a, b)] for c in codes if (c, a, b) in own_diffs}
        excluded = [c for c in codes if c not in participating]
        ca = detect_corporate_actions(participating, cfg)
        for items in participating.values():
            for it in items:
                if it["code"] in ca:
                    it["action"] = "corporate_action"
                    it["corporate_action_ratio"] = ca[it["code"]]["ratio"]
        by_code: dict[str, list[dict]] = defaultdict(list)
        names: dict[str, str] = {}
        for c, items in participating.items():
            for it in items:
                if it["action"] in ADD_ACTIONS | REDUCE_ACTIONS:
                    names[it["code"]] = it["name"]
                    by_code[it["code"]].append({
                        "etf": c, "action": it["action"],
                        "per_unit_change_pct": it["per_unit_change_pct"],
                        "shares_change": it["shares_change"],
                        "weight_curr": it["weight_curr"],
                    })
        min_n = cfg["co_signal_min_etfs"]

        def signals(actions):
            out = []
            for stock, lst in by_code.items():
                hits = [x for x in lst if x["action"] in actions]
                if len(hits) >= min_n:
                    out.append({"code": stock, "name": names[stock], "etfs": sorted(hits, key=lambda x: x["etf"]), "count": len(hits)})
            out.sort(key=lambda s: (-s["count"], s["code"]))
            return out

        intervals.append({
            "from": a, "to": b,
            "etfs": sorted(participating),
            "excluded": excluded,
            "co_add": signals(ADD_ACTIONS),
            "co_reduce": signals(REDUCE_ACTIONS),
            "corporate_actions": [{"code": k, "name": names.get(k), **v} for k, v in sorted(ca.items())],
        })
    intervals.reverse()  # 最新在前

    # 3. 每檔：序列、最新持股、異動清單、總覽、壓縮的持股歷史（前端自訂比較窗口用）
    overview, series, detail, coverage, holdings_history = [], {}, {}, {}, {}
    for code in codes:
        snaps = snapshots[code]
        ds = sorted(snaps)
        cfg_etf = etf_by_code[code]
        coverage[code] = {"dates": ds, "missing": [d for d in all_dates if d not in snaps],
                          "first": ds[0], "last": ds[-1], "count": len(ds)}
        px = prices.get(code, {})
        series[code] = [{
            "date": d,
            "posted_date": s.get("posted_date"),
            "nav": s.get("nav"),
            "close": px.get(d, {}).get("close"),
            "premium_pct": premium_pct(px.get(d, {}).get("close"), s.get("nav")),
            "volume": px.get(d, {}).get("volume"),
            "aum": s.get("aum"),
            "outstanding_units": s.get("outstanding_units"),
            "non_stock_pct": non_stock_pct(s),
            "cash_pct": cash_pct(s),
            "holdings_count": len(s["holdings"]),
            "top10_weight": round(sum(sorted((h.get("weight") or 0 for h in s["holdings"]), reverse=True)[:10]), 2),
        } for d, s in ((d, snaps[d]) for d in ds)]

        hist_dates = ds[-cfg.get("history_days", 30):]
        stocks_hist: dict[str, dict] = {}
        for i, d in enumerate(hist_dates):
            for h in snaps[d]["holdings"]:
                entry = stocks_hist.setdefault(h["code"], {"name": h.get("name"), "shares": [0] * len(hist_dates)})
                entry["shares"][i] = h.get("shares") or 0
                entry["name"] = h.get("name") or entry["name"]
        holdings_history[code] = {
            "dates": hist_dates,
            "units": [snaps[d]["outstanding_units"] for d in hist_dates],
            "stocks": stocks_hist,
        }

        latest, prev = snaps[ds[-1]], (snaps[ds[-2]] if len(ds) > 1 else None)
        px_latest = px.get(ds[-1], {})
        px_prev = px.get(ds[-2], {}) if prev else {}
        units = latest["outstanding_units"]
        holdings = [{**h, "per_unit": per_unit(h.get("shares"), units)} for h in latest["holdings"]]
        changes = [{"date": b, "prev_date": a, "items": own_diffs[(code, a, b)]}
                   for a, b in zip(ds, ds[1:])]
        changes.reverse()
        # 反向索引要看完整歷史，前端明細只列最近幾十個區間，其餘裁掉以控制檔案大小
        full_changes = changes
        changes = changes[: cfg.get("detail_change_intervals", 40)]
        detail[code] = {
            "date": ds[-1],
            "holdings": holdings,
            "non_stock": latest.get("non_stock"),
            "non_stock_items": latest.get("non_stock_items") or [],
            "futures": latest.get("futures") or [],
            "changes": changes,
            "_full_changes": full_changes,
        }

        nsp, nsp_prev = non_stock_pct(latest), (non_stock_pct(prev) if prev else None)
        top = sorted(latest["holdings"], key=lambda h: -(h.get("weight") or 0))[: cfg["top_holdings"]]
        overview.append({
            "etf": code,
            "name": cfg_etf.get("name"),
            "provider": cfg_etf.get("provider"),
            "date": ds[-1],
            "posted_date": latest.get("posted_date"),
            "prev_date": ds[-2] if prev else None,
            "nav": latest.get("nav"),
            "nav_change": round(latest["nav"] - prev["nav"], 4) if prev and latest.get("nav") is not None and prev.get("nav") is not None else None,
            "close": px_latest.get("close"),
            "close_change": px_latest.get("change"),
            "close_change_pct": pct(px_latest.get("change"), (px_latest.get("close") or 0) - (px_latest.get("change") or 0)) if px_latest.get("close") is not None and px_latest.get("change") is not None else None,
            "volume": px_latest.get("volume"),
            "premium_pct": premium_pct(px_latest.get("close"), latest.get("nav")),
            "premium_pct_change": (lambda a, b: round(a - b, 2) if a is not None and b is not None else None)(
                premium_pct(px_latest.get("close"), latest.get("nav")),
                premium_pct(px_prev.get("close"), prev.get("nav")) if prev else None),
            "aum": latest.get("aum"),
            "outstanding_units": units,
            "units_change": (units - prev["outstanding_units"]) if prev else None,
            "stock_value": latest.get("stock_value"),
            "non_stock_total": latest.get("non_stock_total"),
            "non_stock_pct": nsp,
            "non_stock_pct_change": round(nsp - nsp_prev, 2) if nsp is not None and nsp_prev is not None else None,
            "cash_pct": cash_pct(latest),
            "futures_notional_pct": pct(sum(f.get("amount") or 0 for f in latest.get("futures") or []) or None, latest.get("aum")),
            "holdings_count": len(latest["holdings"]),
            "top_holdings": [{"code": h["code"], "name": h.get("name"), "weight": h.get("weight")} for h in top],
            "latest_changes": {k: sum(1 for it in changes[0]["items"] if it["action"] == k)
                               for k in ACTION_ORDER} if changes else None,
        })

    # 4. 反向索引
    stocks: dict[str, dict] = {}
    for code in codes:
        d = detail[code]
        last_change_by_stock: dict[str, dict] = {}
        for ch in d["_full_changes"]:  # 最新在前，第一次遇到即最近一次
            for it in ch["items"]:
                last_change_by_stock.setdefault(it["code"], {
                    "date": ch["date"], "action": it["action"],
                    "per_unit_change_pct": it["per_unit_change_pct"], "shares_change": it["shares_change"],
                })
        for h in d["holdings"]:
            entry = stocks.setdefault(h["code"], {"code": h["code"], "name": h.get("name"), "held_by": []})
            entry["held_by"].append({
                "etf": code, "date": d["date"],
                "shares": h.get("shares"), "amount": h.get("amount"), "weight": h.get("weight"),
                "per_unit": h.get("per_unit"),
                "last_change": last_change_by_stock.get(h["code"]),
            })
        # 已出清的股票也要查得到最近一次異動
        for stock, lc in last_change_by_stock.items():
            if lc["action"] == "exit" and stock not in stocks:
                name = next((it["name"] for ch in d["_full_changes"] for it in ch["items"] if it["code"] == stock), None)
                stocks[stock] = {"code": stock, "name": name, "held_by": []}
            if stock in stocks and not any(x["etf"] == code for x in stocks[stock]["held_by"]) and lc["action"] == "exit":
                stocks[stock].setdefault("exited_by", []).append({"etf": code, **lc})
    for d in detail.values():
        del d["_full_changes"]
    for entry in stocks.values():
        entry["etf_count"] = len(entry["held_by"])
        entry["total_amount"] = sum(x.get("amount") or 0 for x in entry["held_by"])
        entry["held_by"].sort(key=lambda x: -(x.get("weight") or 0))

    return {
        "schema_version": SITE_SCHEMA_VERSION,
        "generated_at": (generated_at or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))).replace(microsecond=0).isoformat(),
        "config": {k: cfg[k] for k in DEFAULT_CONFIG},
        "etfs": [{k: e.get(k) for k in ("code", "name", "full_name", "provider", "issuer", "tag", "description", "color", "info_url")} for e in etfs],
        # 完整價格序列（行情比快照早很多，圖表用這個畫收盤/成交量）
        "price_series": {c: [{"date": d, **v} for d, v in sorted(prices.get(c, {}).items())] for c in codes},
        "dates": all_dates,
        "coverage": coverage,
        "prices_coverage": {c: {"count": len(prices.get(c, {})), "last": max(prices.get(c, {}), default=None)} for c in codes},
        "overview": overview,
        "series": series,
        "intervals": intervals,
        "etf_detail": detail,
        "holdings_history": holdings_history,
        "stocks": dict(sorted(stocks.items(), key=lambda kv: (-kv[1]["etf_count"], -kv[1]["total_amount"]))),
        "notes": {
            "per_unit": "每單位持股數 = 股數 / 流通在外單位數；日對日比較用它，排除申贖造成的規模效果。加/減碼要同時滿足股數有變且每單位變動超過門檻。",
            "non_stock_pct": "非股票水位 = (基金淨資產 - 股票市值) / 基金淨資產；cash_pct 是投信揭露的現金項目，統一只有最新一天有。",
            "intervals": "共同訊號只比較全體日期序列中相鄰兩天，且只納入兩天都有快照的 ETF（excluded 列出缺席者）。",
            "corporate_action": "多檔 ETF 同日對同一股票出現一致的大倍數股數變化，標記為疑似公司行動（分割/併股）待核對，不算加減碼。",
            "prices": "close 為證交所當日收盤價；premium_pct = (收盤 - 淨值) / 淨值，淨值為投信當日公告值。缺行情的日子為 null。",
        },
    }
