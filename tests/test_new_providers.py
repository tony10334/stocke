"""野村、國泰、摩根三家 fetcher 的解析測試，用 samples/ 裡 2026-09-22 抓的原始檔。"""

import datetime as dt
import json
import os
import unittest

from fetchers import cathay, jpm, nomura
from fetchers.common import FetchError, NoDataError
from fetchers.xlsx import read_xlsx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(ROOT, "samples")


def load_json(*parts):
    with open(os.path.join(S, *parts), encoding="utf-8") as f:
        return json.load(f)


def load_bytes(*parts):
    with open(os.path.join(S, *parts), "rb") as f:
        return f.read()


class Nomura(unittest.TestCase):
    CFG = {"code": "00980A", "name": "主動野村臺灣優選", "provider": "nomura", "provider_id": "00980A"}

    def test_parse_00980A(self):
        p = nomura.parse_assets(load_json("nomura", "00980A_GetFundAssets_2026-09-21.json"))
        self.assertEqual(p["fund_id"], "00980A")
        self.assertEqual(p["date"], dt.date(2026, 9, 21))
        self.assertEqual(p["aum"], 19_868_595_510)
        self.assertEqual(p["outstanding_units"], 789_730_000)
        self.assertEqual(p["nav"], 25.16)
        self.assertEqual(len(p["holdings"]), 50)
        tsmc = p["holdings"][0]
        self.assertEqual((tsmc["code"], tsmc["name"], tsmc["shares"], tsmc["weight"]), ("2330", "台灣積體電路製造", 753_000, 9.4))
        self.assertEqual(tsmc["amount"], round(0.094 * 19_868_595_510))
        self.assertEqual(p["stock_value"], 18_681_391_736)
        self.assertEqual(p["futures"][0]["code"], "TX")
        self.assertEqual(p["futures"][0]["contracts"], 62)
        self.assertEqual(p["futures_value"], 595_857_200)
        self.assertEqual(p["non_stock"], {"cash": 523_256_396, "margin": 427_218_698, "receivables": 42_660_335})

    def test_parse_00999A_has_cash(self):
        p = nomura.parse_assets(load_json("nomura", "00999A_GetFundAssets_2026-09-21.json"))
        self.assertEqual(len(p["holdings"]), 54)
        self.assertIn("cash", p["non_stock"])
        self.assertTrue(all(i["name"] not in ("股票", "期貨") for i in p["non_stock_items"]))

    def test_no_data(self):
        with self.assertRaises(NoDataError):
            nomura.parse_assets({"StatusCode": 5, "Message": "此搜尋條件尚無相關資料", "Entries": {"Data": {"FundAsset": None, "Table": []}}})

    def test_snapshot(self):
        s = nomura.build_snapshot(self.CFG, nomura.parse_assets(load_json("nomura", "00980A_GetFundAssets_2026-09-21.json")))
        self.assertEqual(s["date"], "2026-09-21")
        self.assertEqual(s["non_stock_total"], 19_868_595_510 - 18_681_391_736)
        self.assertEqual(s["holdings"][0]["code"], "2330")
        json.dumps(s, ensure_ascii=False)

    def test_wrong_fund(self):
        with self.assertRaises(FetchError):
            nomura.build_snapshot({**self.CFG, "provider_id": "00985A"}, nomura.parse_assets(load_json("nomura", "00980A_GetFundAssets_2026-09-21.json")))


class Cathay(unittest.TestCase):
    CFG = {"code": "00400A", "name": "主動國泰動能高息", "provider": "cathay", "provider_id": "EA"}

    def load(self):
        d = "2026-09-21"
        return (load_json("cathay", f"00400A_GetETFAssets_{d}.json"), load_json("cathay", f"00400A_GetETFDetailStockList_{d}.json"),
                load_json("cathay", f"00400A_GetETFDetailBalList_{d}.json"), load_json("cathay", f"00400A_GetETFOptionList_{d}.json"),
                load_json("cathay", f"00400A_GetETFDetailFutureList_{d}.json"))

    def test_parse(self):
        p = cathay.parse_assets(*self.load())
        self.assertEqual(p["date"], dt.date(2026, 9, 21))
        self.assertEqual(p["aum"], 29_312_424_553)
        self.assertEqual(p["outstanding_units"], 1_914_140_000)
        self.assertEqual(p["nav"], 15.31)
        self.assertEqual(len(p["holdings"]), 47)
        self.assertEqual(p["holdings"][0], {"code": "2330", "name": "台積電", "shares": 1_034_000, "amount": round(0.0875 * 29_312_424_553), "weight": 8.75})
        self.assertEqual(p["stock_value"], 28_059_190_858)
        self.assertEqual(p["non_stock"], {"cash": 997_071_596, "margin": 502_667_714, "redemption_payable": -15_252_371})
        self.assertEqual(len(p["options"]), 2)
        self.assertEqual(p["options"][0]["contracts"], -71)
        self.assertEqual(p["futures"], [])

    def test_money(self):
        self.assertEqual(cathay._money("(TWD) $ -15,252,371"), -15_252_371)
        self.assertEqual(cathay._money("NT$28,059,190,858"), 28_059_190_858)
        self.assertIsNone(cathay._money(None))

    def test_no_data(self):
        with self.assertRaises(NoDataError):
            cathay._ok({"result": None, "returnCode": "4005", "success": False, "returnMessage": "查無資料"}, "x")

    def test_snapshot(self):
        s = cathay.build_snapshot(self.CFG, cathay.parse_assets(*self.load()))
        self.assertEqual(s["etf"], "00400A")
        self.assertEqual(s["non_stock_total"], 29_312_424_553 - 28_059_190_858)
        self.assertEqual(len(s["extra"]["options"]), 2)
        json.dumps(s, ensure_ascii=False)


class Jpm(unittest.TestCase):
    CFG = {"code": "00401A", "name": "主動摩根台灣鑫收", "provider": "jpm", "provider_id": "TW00000401A1"}

    def test_xlsx_reader(self):
        sheets = read_xlsx(load_bytes("jpm", "00401A_m12_pcf_2026-09-22.xlsx"))
        self.assertIn("現金申購買回清單公告", sheets)
        self.assertTrue(any(n.startswith("基金資產 - 股票") for n in sheets))

    def test_parse_m12(self):
        p = jpm.parse_m12(load_bytes("jpm", "00401A_m12_pcf_2026-09-22.xlsx"))
        self.assertEqual(p["date"], dt.date(2026, 9, 21))
        self.assertEqual(p["posted_date"], dt.date(2026, 9, 22))
        self.assertEqual(p["aum"], 3_751_847_570)
        self.assertEqual(p["outstanding_units"], 261_895_000)
        self.assertEqual(p["nav"], 14.33)
        self.assertEqual(p["holdings"][0], {"code": "2330", "name": "台灣積體電路製造", "shares": 300_960, "amount": 746_380_800, "weight": 19.89})
        self.assertGreater(len(p["holdings"]), 40)
        self.assertEqual(p["futures"][0]["code"], "FTV6")
        self.assertEqual(p["futures"][0]["contracts"], 27)
        self.assertEqual(len(p["options"]), 3)
        self.assertEqual(p["options"][2]["contracts"], -320)
        self.assertEqual(p["non_stock"], {"cash": 296_414_430})

    def test_not_xlsx_is_no_data(self):
        with self.assertRaises(NoDataError):
            jpm.parse_m12(b"<html>nope</html>")

    def test_candidates(self):
        self.assertEqual(jpm.candidate_post_dates(dt.date(2026, 9, 18))[:2], [dt.date(2026, 9, 21), dt.date(2026, 9, 22)])

    def test_snapshot(self):
        s = jpm.build_snapshot(self.CFG, jpm.parse_m12(load_bytes("jpm", "00401A_m12_pcf_2026-09-22.xlsx")))
        self.assertEqual(s["date"], "2026-09-21")
        self.assertEqual(s["posted_date"], "2026-09-22")
        self.assertEqual(s["non_stock"]["cash"], 296_414_430)
        json.dumps(s, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
