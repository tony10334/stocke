"""分析層測試：用合成快照驗證規則，最後用真實 data/（若存在）做煙霧測試。"""

import datetime as dt
import json
import os
import unittest

from analysis.core import (build_site_data, cash_pct, detect_corporate_actions, diff_holdings,
                           load_prices, load_snapshots, non_stock_pct, premium_pct)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ETFS = [{"code": "A", "name": "ETF A", "provider": "x"},
        {"code": "B", "name": "ETF B", "provider": "x"},
        {"code": "C", "name": "ETF C", "provider": "y"}]
NAMES = {"1": "股一", "2": "股二", "3": "股三", "4": "股四"}


def snap(etf, date, units, holdings, aum=None, cash=None):
    """holdings: {code: shares}。金額用 shares*100 湊，權重用金額比例。"""
    amount = {c: s * 100 for c, s in holdings.items()}
    stock_value = sum(amount.values())
    aum = aum or int(stock_value * 1.05)
    return {
        "etf": etf, "date": date, "posted_date": date, "nav": aum / units,
        "outstanding_units": units, "aum": aum, "stock_value": stock_value,
        "non_stock_total": aum - stock_value,
        "non_stock": {"cash": cash} if cash is not None else None,
        "futures": [],
        "holdings": [{"code": c, "name": NAMES.get(c, c), "shares": s, "amount": amount[c],
                      "weight": round(amount[c] / aum * 100, 4)} for c, s in holdings.items()],
    }


class Metrics(unittest.TestCase):
    def test_pcts(self):
        s = snap("A", "2026-09-01", 1000, {"1": 100}, aum=12500, cash=1000)
        self.assertEqual(non_stock_pct(s), 20.0)
        self.assertEqual(cash_pct(s), 8.0)
        s["non_stock"] = None
        self.assertIsNone(cash_pct(s))


class Diff(unittest.TestCase):
    def test_proportional_scaling_is_not_a_change(self):
        # 申購讓單位數 +10%，持股等比例 +10% -> 每單位不變 -> 沒有異動
        p = snap("A", "d1", 1000, {"1": 1000, "2": 500})
        c = snap("A", "d2", 1100, {"1": 1100, "2": 550})
        self.assertEqual(diff_holdings(p, c), [])

    def test_actions(self):
        p = snap("A", "d1", 1000, {"1": 1000, "2": 500, "3": 300})
        c = snap("A", "d2", 1000, {"1": 1200, "2": 400, "4": 50})
        items = {it["code"]: it for it in diff_holdings(p, c)}
        self.assertEqual(items["1"]["action"], "add")
        self.assertEqual(items["1"]["per_unit_change_pct"], 20.0)
        self.assertEqual(items["1"]["shares_change"], 200)
        self.assertEqual(items["2"]["action"], "reduce")
        self.assertEqual(items["2"]["per_unit_change_pct"], -20.0)
        self.assertEqual(items["3"]["action"], "exit")
        self.assertEqual(items["3"]["shares_curr"], 0)
        self.assertEqual(items["3"]["per_unit_change_pct"], -100.0)
        self.assertEqual(items["4"]["action"], "new")
        self.assertIsNone(items["4"]["per_unit_change_pct"])
        self.assertEqual(items["4"]["name"], "股四")
        # 排序：new, add, reduce, exit
        self.assertEqual([it["action"] for it in diff_holdings(p, c)], ["new", "add", "reduce", "exit"])

    def test_redemption_without_trading_is_not_a_change(self):
        # 贖回讓單位數 -5%，股數完全沒動 -> 每單位全體 +5.3%，但不是經理人決策
        p = snap("A", "d1", 1000, {"1": 1000, "2": 500, "3": 1})
        c = snap("A", "d2", 950, {"1": 1000, "2": 500, "3": 1})
        self.assertEqual(diff_holdings(p, c), [])
        # 同時真的有交易的那檔仍要抓到
        c2 = snap("A", "d2", 950, {"1": 1100, "2": 500, "3": 1})
        self.assertEqual([it["code"] for it in diff_holdings(p, c2)], ["1"])

    def test_threshold(self):
        p = snap("A", "d1", 1000, {"1": 100000})
        c = snap("A", "d2", 1000, {"1": 100400})   # +0.4% < 0.5%
        self.assertEqual(diff_holdings(p, c), [])
        self.assertEqual(diff_holdings(p, c, {"min_per_unit_change_pct": 0.3})[0]["action"], "add")


class CorporateAction(unittest.TestCase):
    def test_consistent_ratio_across_two_etfs(self):
        diffs = {
            "A": diff_holdings(snap("A", "d1", 1000, {"1": 100, "2": 100}), snap("A", "d2", 1000, {"1": 1000, "2": 120})),
            "B": diff_holdings(snap("B", "d1", 1000, {"1": 300, "2": 100}), snap("B", "d2", 1000, {"1": 3000, "2": 120})),
        }
        ca = detect_corporate_actions(diffs)
        self.assertEqual(list(ca), ["1"])           # 股一 兩檔都 x10；股二 x1.2 一致但倍數太小，是普通加碼
        self.assertEqual(ca["1"], {"ratio": 10.0, "etfs": ["A", "B"]})

    def test_ratio_at_threshold_is_flagged(self):
        diffs = {
            "A": diff_holdings(snap("A", "d1", 1000, {"1": 100}), snap("A", "d2", 1000, {"1": 150})),
            "B": diff_holdings(snap("B", "d1", 1000, {"1": 200}), snap("B", "d2", 1000, {"1": 300})),
        }
        self.assertEqual(detect_corporate_actions(diffs)["1"]["ratio"], 1.5)

    def test_single_etf_is_not_flagged(self):
        diffs = {"A": diff_holdings(snap("A", "d1", 1000, {"1": 100}), snap("A", "d2", 1000, {"1": 1000}))}
        self.assertEqual(detect_corporate_actions(diffs), {})

    def test_inconsistent_ratios_not_flagged(self):
        diffs = {
            "A": diff_holdings(snap("A", "d1", 1000, {"1": 100}), snap("A", "d2", 1000, {"1": 200})),
            "B": diff_holdings(snap("B", "d1", 1000, {"1": 100}), snap("B", "d2", 1000, {"1": 300})),
        }
        self.assertEqual(detect_corporate_actions(diffs), {})


class SiteData(unittest.TestCase):
    def setUp(self):
        # A、B 三天都有；C 缺 d2（要被 d1->d2 與 d2->d3 兩個區間排除）
        self.snaps = {
            "A": {"d1": snap("A", "d1", 1000, {"1": 1000, "2": 500, "3": 100}, cash=50),
                  "d2": snap("A", "d2", 1000, {"1": 1200, "2": 500, "3": 100}, cash=60),
                  "d3": snap("A", "d3", 1000, {"1": 1200, "2": 400, "3": 1000}, cash=70)},
            "B": {"d1": snap("B", "d1", 2000, {"1": 1000, "2": 500, "3": 200}),
                  "d2": snap("B", "d2", 2000, {"1": 1300, "2": 500, "3": 200}),
                  "d3": snap("B", "d3", 2000, {"1": 1300, "2": 500, "3": 2000})},
            "C": {"d1": snap("C", "d1", 500, {"1": 100, "4": 100}),
                  "d3": snap("C", "d3", 500, {"1": 200, "4": 100})},
        }
        self.site = build_site_data(ETFS, self.snaps, generated_at=dt.datetime(2026, 9, 15, 18, 0))

    def test_dates_and_coverage(self):
        self.assertEqual(self.site["dates"], ["d1", "d2", "d3"])
        self.assertEqual(self.site["coverage"]["C"]["missing"], ["d2"])
        self.assertEqual(self.site["coverage"]["A"]["count"], 3)

    def test_intervals_exclude_missing_etf(self):
        iv = {(i["from"], i["to"]): i for i in self.site["intervals"]}
        self.assertEqual(self.site["intervals"][0]["to"], "d3")          # 最新在前
        self.assertEqual(iv[("d1", "d2")]["etfs"], ["A", "B"])
        self.assertEqual(iv[("d1", "d2")]["excluded"], ["C"])
        self.assertEqual(iv[("d2", "d3")]["excluded"], ["C"])

    def test_co_add_signal(self):
        iv = next(i for i in self.site["intervals"] if i["to"] == "d2")
        self.assertEqual([s["code"] for s in iv["co_add"]], ["1"])       # A、B 同日加碼股一
        self.assertEqual(iv["co_add"][0]["count"], 2)
        self.assertEqual([x["etf"] for x in iv["co_add"][0]["etfs"]], ["A", "B"])
        self.assertEqual(iv["co_reduce"], [])

    def test_corporate_action_in_interval_not_counted_as_add(self):
        iv = next(i for i in self.site["intervals"] if i["to"] == "d3")
        self.assertEqual([c["code"] for c in iv["corporate_actions"]], ["3"])   # A、B 股三都 x10
        self.assertEqual(iv["corporate_actions"][0]["ratio"], 10.0)
        self.assertEqual(iv["co_add"], [])
        # 該筆在 ETF 自己的異動清單裡也被改標
        a_d3 = self.site["etf_detail"]["A"]["changes"][0]
        self.assertEqual(a_d3["date"], "d3")
        acts = {it["code"]: it["action"] for it in a_d3["items"]}
        self.assertEqual(acts["3"], "corporate_action")
        self.assertEqual(acts["2"], "reduce")

    def test_missing_day_etf_still_has_own_changes(self):
        c_changes = self.site["etf_detail"]["C"]["changes"]
        self.assertEqual(len(c_changes), 1)
        self.assertEqual((c_changes[0]["prev_date"], c_changes[0]["date"]), ("d1", "d3"))
        self.assertEqual(c_changes[0]["items"][0]["code"], "1")
        self.assertEqual(c_changes[0]["items"][0]["action"], "add")

    def test_overview(self):
        ov = {o["etf"]: o for o in self.site["overview"]}
        a = ov["A"]
        self.assertEqual(a["date"], "d3")
        self.assertEqual(a["prev_date"], "d2")
        self.assertEqual(a["holdings_count"], 3)
        self.assertIsNone(a["close"])
        self.assertEqual(a["top_holdings"][0]["code"], "1")             # d3: 1200 股最大
        self.assertEqual([h["code"] for h in a["top_holdings"]], ["1", "3", "2"])
        self.assertIsNotNone(a["non_stock_pct"])
        self.assertEqual(a["latest_changes"]["corporate_action"], 1)
        self.assertEqual(a["latest_changes"]["reduce"], 1)
        self.assertEqual(ov["C"]["prev_date"], "d1")

    def test_series(self):
        s = self.site["series"]["A"]
        self.assertEqual([x["date"] for x in s], ["d1", "d2", "d3"])
        self.assertIsNotNone(s[0]["cash_pct"])
        self.assertIsNone(self.site["series"]["B"][0]["cash_pct"])      # B 沒揭露現金

    def test_stock_index(self):
        st = self.site["stocks"]
        self.assertEqual(st["1"]["etf_count"], 3)
        self.assertEqual(list(st)[0], "1")                                # 持有檔數最多排最前
        held = {x["etf"]: x for x in st["1"]["held_by"]}
        self.assertEqual(held["C"]["last_change"]["action"], "add")
        self.assertEqual(held["A"]["last_change"], {"date": "d2", "action": "add",
                                                    "per_unit_change_pct": 20.0, "shares_change": 200})
        self.assertIsNotNone(held["A"]["per_unit"])
        self.assertEqual(st["4"]["etf_count"], 1)

    def test_exited_stock_is_searchable(self):
        snaps = {"A": {"d1": snap("A", "d1", 1000, {"1": 100, "2": 100}),
                       "d2": snap("A", "d2", 1000, {"1": 100})}}
        site = build_site_data(ETFS[:1], snaps)
        self.assertIn("2", site["stocks"])
        self.assertEqual(site["stocks"]["2"]["held_by"], [])
        self.assertEqual(site["stocks"]["2"]["exited_by"][0]["etf"], "A")

    def test_json_serializable(self):
        json.dumps(self.site, ensure_ascii=False)

    def test_without_prices_everything_is_null(self):
        ov = {o["etf"]: o for o in self.site["overview"]}
        self.assertIsNone(ov["A"]["close"])
        self.assertIsNone(ov["A"]["premium_pct"])
        self.assertIsNone(self.site["series"]["A"][0]["close"])
        self.assertEqual(self.site["prices_coverage"]["A"], {"count": 0, "last": None})


class Prices(unittest.TestCase):
    def test_premium(self):
        self.assertEqual(premium_pct(28.86, 28.66), 0.7)
        self.assertEqual(premium_pct(8.32, 8.33), -0.12)
        self.assertIsNone(premium_pct(None, 10))
        self.assertIsNone(premium_pct(10, None))

    def test_site_data_with_prices(self):
        snaps = {"A": {"d1": snap("A", "d1", 1000, {"1": 1000}, aum=10000),      # nav 10.0
                       "d2": snap("A", "d2", 1000, {"1": 1000}, aum=10500)}}    # nav 10.5
        prices = {"A": {"d1": {"close": 10.2, "change": 0.1, "volume": 500},
                        "d2": {"close": 10.29, "change": 0.09, "volume": 600}}}
        site = build_site_data(ETFS[:1], snaps, prices=prices)
        o = site["overview"][0]
        self.assertEqual(o["close"], 10.29)
        self.assertEqual(o["close_change"], 0.09)
        self.assertEqual(o["close_change_pct"], 0.88)
        self.assertEqual(o["volume"], 600)
        self.assertEqual(o["premium_pct"], -2.0)          # (10.29-10.5)/10.5
        self.assertEqual(o["premium_pct_change"], -4.0)   # d1 是 +2.0
        s = site["series"]["A"]
        self.assertEqual([x["premium_pct"] for x in s], [2.0, -2.0])
        self.assertEqual(site["prices_coverage"]["A"], {"count": 2, "last": "d2"})
        self.assertEqual([p["date"] for p in site["price_series"]["A"]], ["d1", "d2"])
        self.assertEqual(site["price_series"]["A"][1]["close"], 10.29)
        self.assertEqual(site["etfs"][0]["code"], "A")
        self.assertIn("description", site["etfs"][0])
        # 缺某天行情 -> 該天 null，其他不受影響
        site2 = build_site_data(ETFS[:1], snaps, prices={"A": {"d1": prices["A"]["d1"]}})
        self.assertIsNone(site2["overview"][0]["close"])
        self.assertIsNone(site2["overview"][0]["premium_pct_change"])
        self.assertEqual(site2["series"]["A"][0]["close"], 10.2)

    def test_load_prices_missing(self):
        self.assertEqual(load_prices(os.path.join(ROOT, "definitely-not-here")), {})


class RealDataSmoke(unittest.TestCase):
    def test_real_data_if_present(self):
        data_dir = os.path.join(ROOT, "data")
        if not os.path.isdir(os.path.join(data_dir, "00981A")):
            self.skipTest("no data/ yet")
        with open(os.path.join(ROOT, "etfs.json"), encoding="utf-8") as f:
            etfs = json.load(f)["etfs"]
        snaps = load_snapshots(data_dir, [e["code"] for e in etfs])
        site = build_site_data(etfs, snaps)
        self.assertTrue(site["dates"])
        for o in site["overview"]:
            self.assertGreater(o["holdings_count"], 0)
            self.assertIsNotNone(o["non_stock_pct"])
        json.dumps(site, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
