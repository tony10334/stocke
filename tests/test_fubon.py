"""富邦投信 fetcher 的解析測試，用 samples/fubon/ 的原始 HTML。"""

import datetime as dt
import json
import os
import unittest

from fetchers import fubon
from fetchers.common import FetchError, NoDataError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples", "fubon")
CFG = {"code": "00405A", "name": "主動富邦台灣龍耀", "provider": "fubon", "provider_id": "00405A"}
CFG_OTHER = {"code": "00981A", "name": "x", "provider": "fubon", "provider_id": "00981A"}


def load(name):
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as f:
        return f.read()


class ParseAssetsPage(unittest.TestCase):
    def test_ddate_0915_fetched_before_1630(self):
        # 16:2x 抓的：ddate=2026/09/15 但資料日期還是 09/14
        p = fubon.parse_assets_page(load("00405A_assets_2026-09-15.html"))
        self.assertEqual(p["stk_id"], "00405A")
        self.assertIn("00405A", p["title"])
        self.assertEqual(p["date"], dt.date(2026, 9, 14))
        self.assertEqual(p["aum"], 26_567_062_676)
        self.assertEqual(p["outstanding_units"], 3_090_745_000)
        self.assertEqual(p["nav"], 8.60)
        self.assertEqual(len(p["holdings"]), 47)
        self.assertEqual(p["holdings"][0], {"code": "3443", "name": "創意", "shares": 364_000,
                                            "amount": 2_329_600_000, "weight": 8.7687})
        self.assertEqual(p["stock_value"], 25_172_915_305)
        self.assertEqual(sum(h["amount"] for h in p["holdings"]), 25_172_915_305)
        self.assertEqual(p["stock_weight_total"], 94.7502)
        self.assertEqual(p["non_stock"], {"cash": 1_298_527_898, "redemption_payable": 307_889_696,
                                          "receivables": 384_523_388})
        self.assertEqual([i["code"] for i in p["non_stock_items"]], ["CASH", "REDEMPTION_PAYABLE", "APAR"])
        self.assertEqual(p["non_stock_items"][0]["name"], "現金")   # 幣別括號已去掉
        # 合計列不能混進持股
        self.assertFalse(any("合計" in h["code"] for h in p["holdings"]))

    def test_ddate_0916_fetched_after_1630_has_same_day_data(self):
        p = fubon.parse_assets_page(load("00405A_assets_2026-09-16.html"))
        self.assertEqual(p["date"], dt.date(2026, 9, 15))
        self.assertGreater(len(p["holdings"]), 30)
        self.assertEqual(sum(h["amount"] for h in p["holdings"]), p["stock_value"])
        self.assertIn("cash", p["non_stock"])

    def test_weekend_rolls_back(self):
        p = fubon.parse_assets_page(load("00405A_assets_2026-09-12.html"))
        self.assertEqual(p["date"], dt.date(2026, 9, 11))

    def test_layout_change_is_error(self):
        with self.assertRaises(FetchError):
            fubon.parse_assets_page("<html><body>nope</body></html>")

    def test_missing_data_block_is_no_data(self):
        html = '<input id="mainContent_subMainContent_hidStkId" value="00405A"><p>沒有資料</p>'
        with self.assertRaises(NoDataError):
            fubon.parse_assets_page(html)

    def test_total_mismatch_is_error(self):
        html = load("00405A_assets_2026-09-15.html").replace("25,172,915,305", "25,172,915,306", 1)
        with self.assertRaises(FetchError):
            fubon.parse_assets_page(html)


class BuildSnapshot(unittest.TestCase):
    def setUp(self):
        self.page = fubon.parse_assets_page(load("00405A_assets_2026-09-15.html"))
        self.fetched_at = dt.datetime(2026, 9, 15, 17, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))

    def test_snapshot(self):
        s = fubon.build_snapshot(CFG, self.page, query_date=dt.date(2026, 9, 15), fetched_at=self.fetched_at)
        self.assertEqual(s["etf"], "00405A")
        self.assertEqual(s["provider"], "fubon")
        self.assertEqual(s["date"], "2026-09-14")
        self.assertEqual(s["posted_date"], "2026-09-14")
        self.assertEqual(s["non_stock_total"], 26_567_062_676 - 25_172_915_305)
        self.assertEqual(s["non_stock"]["cash"], 1_298_527_898)
        self.assertEqual(s["futures"], [])
        self.assertEqual(s["holdings"][0]["code"], "3443")     # 權重最高
        self.assertEqual(s["extra"]["query_date"], "2026-09-15")
        self.assertEqual(s["extra"]["non_stock_detail"], "ok")
        json.dumps(s, ensure_ascii=False)

    def test_wrong_etf_is_error(self):
        with self.assertRaises(FetchError):
            fubon.build_snapshot(CFG_OTHER, self.page)


class FetchDateLogic(unittest.TestCase):
    """用假的 client 驗證 fetch() 對「資料日期 vs 要求日」的判斷。"""

    class FakeClient:
        def __init__(self, html):
            self.html = html
            self.urls = []

        def get_text(self, url, headers=None, encoding="utf-8"):
            self.urls.append(url)
            return self.html

    def test_not_yet_posted(self):
        c = self.FakeClient(load("00405A_assets_2026-09-15.html"))   # 頁面資料日 09/14
        with self.assertRaises(NoDataError):
            fubon.fetch(CFG, dt.date(2026, 9, 15), c)
        self.assertIn("stkId=00405A&ddate=2026/09/15&lan=TW", c.urls[0])

    def test_exact_match(self):
        c = self.FakeClient(load("00405A_assets_2026-09-15.html"))
        s = fubon.fetch(CFG, dt.date(2026, 9, 14), c)
        self.assertEqual(s["date"], "2026-09-14")

    def test_page_newer_than_requested_is_error(self):
        c = self.FakeClient(load("00405A_assets_2026-09-15.html"))
        with self.assertRaises(FetchError):
            fubon.fetch(CFG, dt.date(2026, 9, 10), c)


if __name__ == "__main__":
    unittest.main()
