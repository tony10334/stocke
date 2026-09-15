"""證交所行情 fetcher 測試，用 samples/twse/ 的原始回傳。"""

import datetime as dt
import json
import os
import tempfile
import unittest

from fetchers import prices
from fetchers.common import FetchError, NoDataError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples", "twse")


def load(name):
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as f:
        return json.load(f)


class ParseStockDay(unittest.TestCase):
    def test_month(self):
        rows = prices.parse_stock_day(load("00981A_STOCK_DAY_202609.json"))
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[0]["date"], "2026-09-01")
        last = rows[-1]
        self.assertEqual(last["date"], "2026-09-15")
        self.assertEqual(last["close"], 28.86)
        self.assertEqual(last["open"], 29.30)
        self.assertEqual(last["change"], -0.46)
        self.assertEqual(last["volume"], 164_063_144)
        self.assertEqual(last["transactions"], 72_645)
        self.assertEqual([r["date"] for r in rows], sorted(r["date"] for r in rows))

    def test_future_month_is_no_data(self):
        with self.assertRaises(NoDataError):
            prices.parse_stock_day(load("00981A_STOCK_DAY_202612_future.json"))

    def test_fields_changed_is_error(self):
        p = load("00981A_STOCK_DAY_202609.json")
        p["fields"] = ["日期", "x"]
        with self.assertRaises(FetchError):
            prices.parse_stock_day(p)

    def test_no_trade_placeholders(self):
        p = load("00981A_STOCK_DAY_202609.json")
        p["data"] = [["115/09/16", "0", "0", "--", "--", "--", "--", "X0.00", "0", ""]]
        r = prices.parse_stock_day(p)[0]
        self.assertIsNone(r["close"])
        self.assertEqual(r["change"], 0.0)
        self.assertEqual(r["volume"], 0)


class Helpers(unittest.TestCase):
    def test_months_covering(self):
        self.assertEqual(prices.months_covering(dt.date(2026, 9, 8), dt.date(2026, 9, 15)), [(2026, 9)])
        self.assertEqual(prices.months_covering(dt.date(2025, 11, 20), dt.date(2026, 1, 3)),
                         [(2025, 11), (2025, 12), (2026, 1)])

    def test_merge_and_roundtrip(self):
        rows = prices.parse_stock_day(load("00981A_STOCK_DAY_202609.json"))
        store = {}
        self.assertEqual(prices.merge_rows(store, "00981A", rows), 11)
        self.assertEqual(prices.merge_rows(store, "00981A", rows), 0)       # 重複併入不算變動
        rows[-1]["close"] = 99.0
        self.assertEqual(prices.merge_rows(store, "00981A", rows), 1)
        with tempfile.TemporaryDirectory() as d:
            prices.save_prices(d, store)
            self.assertEqual(prices.load_prices(d)["00981A"]["2026-09-15"]["close"], 99.0)
            self.assertEqual(prices.load_prices(os.path.join(d, "nope")), {})


class UpdatePrices(unittest.TestCase):
    class FakeClient:
        def __init__(self):
            self.urls = []

        def get(self, url, headers=None):
            self.urls.append(url)
            name = "00981A_STOCK_DAY_202609.json" if "date=202609" in url else "00981A_STOCK_DAY_202612_future.json"
            with open(os.path.join(SAMPLES, name), "rb") as f:
                return f.read()

    def test_update(self):
        c = self.FakeClient()
        sleeps = []
        with tempfile.TemporaryDirectory() as d:
            s = prices.update_prices(c, ["00981A", "00403A"], dt.date(2026, 9, 1), dt.date(2026, 10, 15), d,
                                     sleep=sleeps.append)
            self.assertEqual(s["00981A"], {"changed": 11, "has_end": False})   # 10 月沒資料
            self.assertEqual(len(c.urls), 4)                                    # 2 檔 x 2 個月
            self.assertEqual(len(sleeps), 3)                                    # 第一次不等
            self.assertIn("stockNo=00403A", c.urls[2])
            self.assertEqual(prices.load_prices(d)["00981A"]["2026-09-15"]["close"], 28.86)


if __name__ == "__main__":
    unittest.main()
