"""統一投信 fetcher 的解析測試，全部用 samples/uni/ 裡 2026-09-15 抓的原始檔。

執行：python -m unittest discover -s tests -v
"""

import copy
import datetime as dt
import json
import os
import unittest

from fetchers import uni
from fetchers.common import (FetchError, NoDataError, from_roc, parse_any_date, parse_dotnet_date,
                             parse_iso_date, to_roc)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples", "uni")

CFG_00981A = {"code": "00981A", "name": "主動統一台股增長", "provider": "uni", "provider_id": "49YTW"}
CFG_00403A = {"code": "00403A", "name": "主動統一升級50", "provider": "uni", "provider_id": "63YTW"}


def load_json(name):
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as f:
        return json.load(f)


def load_text(name):
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as f:
        return f.read()


class DateHelpers(unittest.TestCase):
    def test_roc_roundtrip(self):
        self.assertEqual(to_roc(dt.date(2026, 9, 15)), "115/09/15")
        self.assertEqual(from_roc("115/09/15"), dt.date(2026, 9, 15))
        self.assertEqual(from_roc(" 115/9/5 "), dt.date(2026, 9, 5))

    def test_dotnet_date(self):
        self.assertEqual(parse_dotnet_date("/Date(1789315200000)/"), dt.date(2026, 9, 14))
        self.assertEqual(parse_dotnet_date("/Date(1789401600000)/"), dt.date(2026, 9, 15))
        self.assertIsNone(parse_dotnet_date("/Date(-62135596800000)/"))
        self.assertIsNone(parse_dotnet_date(""))
        self.assertIsNone(parse_dotnet_date(None))

    def test_iso_date(self):
        self.assertEqual(parse_iso_date("2026-09-14T00:00:00"), dt.date(2026, 9, 14))
        self.assertEqual(parse_iso_date("2026-09-15T16:20:06.5392232+08:00"), dt.date(2026, 9, 15))
        self.assertIsNone(parse_iso_date("0001-01-01T00:00:00"))

    def test_any_date_accepts_both_serializations(self):
        # 同一個 GetPCF：urllib 拿到 ISO，瀏覽器 XHR 拿到 /Date(ms)/
        self.assertEqual(parse_any_date("2026-09-14T00:00:00"), dt.date(2026, 9, 14))
        self.assertEqual(parse_any_date("/Date(1789315200000)/"), dt.date(2026, 9, 14))
        self.assertIsNone(parse_any_date(None))


class PostDateCandidates(unittest.TestCase):
    def test_friday_data_posts_on_monday(self):
        c = uni.candidate_post_dates(dt.date(2026, 9, 11))   # 週五
        self.assertEqual(c[:3], [dt.date(2026, 9, 14), dt.date(2026, 9, 15), dt.date(2026, 9, 16)])
        self.assertTrue(all(d.weekday() < 5 for d in c))

    def test_weekday(self):
        c = uni.candidate_post_dates(dt.date(2026, 9, 14))   # 週一
        self.assertEqual(c[0], dt.date(2026, 9, 15))
        self.assertEqual(len(c), 5)                          # 7 天內有 5 個平日


class ParsePcf(unittest.TestCase):
    def test_00981A(self):
        p = uni.parse_pcf(load_json("00981A_getpcf_2026-09-15.json"))
        self.assertEqual(p["stock_no"], "00981A")
        self.assertEqual(p["fund_code"], "49YTW")
        self.assertEqual(p["date"], dt.date(2026, 9, 14))
        self.assertEqual(p["posted_date"], dt.date(2026, 9, 15))
        self.assertEqual(p["nav"], 29.3)
        self.assertEqual(p["outstanding_units"], 9_466_709_000)
        self.assertEqual(p["aum"], 277_374_899_002)
        self.assertEqual(p["stock_value"], 268_842_870_785)
        self.assertEqual(p["futures_value"], 6_216_516_600)
        self.assertEqual(len(p["holdings"]), 50)
        tsmc = next(h for h in p["holdings"] if h["code"] == "2330")
        self.assertEqual(tsmc, {"code": "2330", "name": "台積電", "shares": 11_864_000,
                                "amount": 28_236_320_000, "weight": 10.18})
        self.assertEqual(sum(h["amount"] for h in p["holdings"]), p["stock_value"])
        self.assertEqual(p["futures"], [{"code": "TX", "name": "台指期貨", "contracts": 679,
                                         "amount": 6_216_516_600, "weight": 2.24, "month": "2026/09",
                                         "position": "B"}])
        self.assertEqual(p["extra"]["holders"], 1_013_235)
        self.assertEqual(p["extra"]["holders_as_of_roc"], "115/08/31")
        self.assertEqual(p["extra"]["units_change"], 3_000_000)

    def test_00403A_no_futures(self):
        p = uni.parse_pcf(load_json("00403A_getpcf_2026-09-15.json"))
        self.assertEqual(p["stock_no"], "00403A")
        self.assertEqual(p["aum"], 151_852_059_818)
        self.assertEqual(p["nav"], 10.14)
        self.assertEqual(len(p["holdings"]), 50)
        self.assertEqual(p["futures"], [])          # GD 群組 Details 為 null
        self.assertEqual(p["futures_value"], 0)

    def test_zero_payload_is_no_data(self):
        payload = copy.deepcopy(load_json("00981A_getpcf_2026-09-15.json"))
        for row in payload["pcf"]:
            row["Amount"] = 0
        payload["asset"] = []
        with self.assertRaises(NoDataError):
            uni.parse_pcf(payload)

    def test_garbage_payload(self):
        with self.assertRaises(FetchError):
            uni.parse_pcf({"error": "x"})


class ParseDataAsset(unittest.TestCase):
    def test_00981A(self):
        a = uni.parse_data_asset(load_text("00981A_fundinfo_page_2026-09-15.html"))
        self.assertEqual(a["date"], dt.date(2026, 9, 14))
        self.assertEqual(a["aum"], 277_374_899_002)
        self.assertEqual(a["outstanding_units"], 9_466_709_000)
        self.assertEqual(a["nav"], 29.3)
        self.assertEqual(a["stock_value"], 268_842_870_785)
        self.assertEqual(a["holdings_count"], 50)
        self.assertEqual(a["non_stock"], {"cash": 4_823_473_398, "margin": 1_848_386_338,
                                          "rp": 1_258_097_300, "receivables": 443_464_120})
        self.assertEqual([i["code"] for i in a["non_stock_items"]], ["CASH", "GDM", "RP", "APAR"])
        self.assertEqual(a["non_stock_items"][0]["name"], "現金")

    def test_00403A(self):
        a = uni.parse_data_asset(load_text("00403A_fundinfo_page_2026-09-15.html"))
        self.assertEqual(a["date"], dt.date(2026, 9, 14))
        self.assertEqual(a["non_stock"], {"cash": 8_598_485_395, "margin": 0,
                                          "rp": 3_318_409_687, "receivables": 1_371_365_473})

    def test_missing_block(self):
        with self.assertRaises(FetchError):
            uni.parse_data_asset("<html><body>nothing</body></html>")


class BuildSnapshot(unittest.TestCase):
    def setUp(self):
        self.pcf = uni.parse_pcf(load_json("00981A_getpcf_2026-09-15.json"))
        self.asset = uni.parse_data_asset(load_text("00981A_fundinfo_page_2026-09-15.html"))
        self.fetched_at = dt.datetime(2026, 9, 15, 17, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))

    def test_full(self):
        s = uni.build_snapshot(CFG_00981A, self.pcf, self.asset, self.fetched_at)
        self.assertEqual(s["schema_version"], 1)
        self.assertEqual(s["etf"], "00981A")
        self.assertEqual(s["date"], "2026-09-14")
        self.assertEqual(s["posted_date"], "2026-09-15")
        self.assertEqual(s["fetched_at"], "2026-09-15T17:30:00+08:00")
        self.assertEqual(s["non_stock_total"], 277_374_899_002 - 268_842_870_785)
        self.assertEqual(s["non_stock"]["cash"], 4_823_473_398)
        self.assertEqual(s["extra"]["non_stock_detail"], "ok")
        self.assertEqual(s["extra"]["futures_notional"], 6_216_516_600)
        # 依權重排序，台積電第一
        self.assertEqual(s["holdings"][0]["code"], "2330")
        self.assertTrue(all(s["holdings"][i]["weight"] >= s["holdings"][i + 1]["weight"]
                            for i in range(len(s["holdings"]) - 1)))
        json.dumps(s, ensure_ascii=False)  # 必須可序列化

    def test_without_asset(self):
        s = uni.build_snapshot(CFG_00981A, self.pcf, None, self.fetched_at)
        self.assertIsNone(s["non_stock"])
        self.assertEqual(s["non_stock_items"], [])
        self.assertEqual(s["non_stock_total"], 277_374_899_002 - 268_842_870_785)
        self.assertEqual(s["extra"]["non_stock_detail"], "not_available")

    def test_asset_date_mismatch_skips_breakdown(self):
        asset = dict(self.asset, date=dt.date(2026, 9, 13))
        with self.assertLogs("fetchers.uni", level="WARNING"):
            s = uni.build_snapshot(CFG_00981A, self.pcf, asset, self.fetched_at)
        self.assertIsNone(s["non_stock"])
        self.assertEqual(s["extra"]["non_stock_detail"], "date_mismatch:2026-09-13")

    def test_asset_aum_conflict_is_error(self):
        asset = dict(self.asset, aum=1)
        with self.assertRaises(FetchError):
            uni.build_snapshot(CFG_00981A, self.pcf, asset, self.fetched_at)

    def test_wrong_etf_is_error(self):
        with self.assertRaises(FetchError):
            uni.build_snapshot(CFG_00403A, self.pcf, None, self.fetched_at)


if __name__ == "__main__":
    unittest.main()
