"""所有 fetcher 共用的工具：HTTP client、日期轉換、快照 schema 與存檔。

單日快照 schema（schema_version 1）
----------------------------------
{
  "schema_version": 1,
  "etf": "00981A",                 # 交易代號
  "name": "主動統一台股增長",
  "provider": "uni",
  "date": "2026-09-14",            # 持股資料日（投信的 TranDate / 資料日期），檔名用這個
  "posted_date": "2026-09-15",     # 投信公告日
  "fetched_at": "2026-09-15T17:30:12+08:00",
  "source": "official",            # official | backup
  "nav": 29.30,                    # 每單位淨值
  "outstanding_units": 9466709000, # 流通在外單位數
  "aum": 277374899002,             # 基金淨資產（元）
  "stock_value": 268842870785,     # 股票部位市值合計（元）
  "non_stock_total": 8532028217,   # aum - stock_value；現金水位主算式用這個
  "non_stock": {                   # 投信揭露的非股票明細（缺時為 null）；各家項目不同，只對得上的 key 才填
    "cash": ...,                   #   現金（統一 CASH / 富邦 現金）
    "margin": ...,                 #   期貨保證金（統一 GDM）
    "rp": ...,                     #   附買回債券（統一 RP）
    "receivables": ...,            #   應收付證券款（統一 APAR / 富邦 應收(付)證券款）
    "redemption_payable": ...      #   應付受益權單位買回款（富邦；是負債，金額仍為正數）
  },
  "non_stock_items": [             # 投信原始項目（名稱照抄），供對照
    {"code": "CASH", "name": "現金", "amount": ...}
  ],
  "futures": [                     # 期貨部位（名目本金），沒有就是空陣列
    {"code": "TX", "name": "台指期貨", "contracts": 679, "amount": ..., "weight": 2.24, "month": "2026/09"}
  ],
  "holdings": [                    # 依權重由大到小
    {"code": "2330", "name": "台積電", "shares": 11864000, "amount": ..., "weight": 10.18}
  ],
  "extra": { ... }                 # 各投信額外欄位（受益人數、預收申購金等），分析層不依賴
}
"""

from __future__ import annotations

import datetime as dt
import http.cookiejar
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
TZ_TAIPEI = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 stocketf/0.1"
)


class FetchError(Exception):
    """抓取或解析失敗（網路錯誤、格式改版、資料自相矛盾）。"""


class NoDataError(FetchError):
    """來源正常回應，但該日期還沒有資料（非營業日或投信尚未公告）。"""


# ---------------------------------------------------------------- HTTP

class HttpClient:
    """帶 cookie jar 的極簡 HTTP client（只用標準函式庫）。"""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def _request(self, url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
        h = {"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
        with self.opener.open(req, timeout=self.timeout) as resp:
            return resp.read()

    def get(self, url: str, headers: dict | None = None) -> bytes:
        return self._request(url, None, headers)

    def get_text(self, url: str, headers: dict | None = None, encoding: str = "utf-8") -> str:
        return self.get(url, headers).decode(encoding, errors="replace")

    def post_json(self, url: str, payload: dict, headers: dict | None = None):
        """POST JSON 並解析回傳 JSON。

        遇到 307（統一投信 WAF 先設 cookie 再導回同一 URL）時，cookie 已進 jar，
        直接重送一次。
        """
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }
        if headers:
            h.update(headers)
        for attempt in (1, 2):
            try:
                raw = self._request(url, body, h)
                break
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and attempt == 1:
                    log.debug("POST %s got %s, retrying with cookies", url, e.code)
                    continue
                raise FetchError(f"POST {url} -> HTTP {e.code}") from e
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as e:
            raise FetchError(f"POST {url}: response is not JSON ({raw[:80]!r})") from e


# ---------------------------------------------------------------- dates

def now_taipei() -> dt.datetime:
    return dt.datetime.now(TZ_TAIPEI).replace(microsecond=0)


def today_taipei() -> dt.date:
    return now_taipei().date()


def to_roc(d: dt.date) -> str:
    """西元 date -> 民國年字串 '115/09/15'。"""
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def from_roc(s: str) -> dt.date:
    """'115/09/15' -> date(2026, 9, 15)。"""
    m = re.fullmatch(r"\s*(\d{2,3})/(\d{1,2})/(\d{1,2})\s*", s)
    if not m:
        raise ValueError(f"not a ROC date: {s!r}")
    y, mo, d = (int(x) for x in m.groups())
    return dt.date(y + 1911, mo, d)


_DOTNET_DATE = re.compile(r"/Date\((-?\d+)\)/")


def parse_dotnet_date(s: str | None) -> dt.date | None:
    """'/Date(1789315200000)/' -> date（台北時區）。

    1789315200000 = 2026-09-13T16:00Z = 台北 2026-09-14 00:00，所以要用台北時區取日期。
    無效值（年 0001 的負數、空字串）回傳 None。
    """
    if not s:
        return None
    m = _DOTNET_DATE.search(s)
    if not m:
        return None
    ms = int(m.group(1))
    if ms < 0:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=TZ_TAIPEI).date()


def parse_any_date(s: str | None) -> dt.date | None:
    """同一個 API 依請求方式不同會回 '/Date(ms)/' 或 ISO 字串，兩種都試。"""
    if not s:
        return None
    if "/Date(" in s:
        return parse_dotnet_date(s)
    return parse_iso_date(s)


def parse_iso_date(s: str | None) -> dt.date | None:
    """'2026-09-14T00:00:00' / '2026-09-14T16:32:45.123+08:00' -> date。"""
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    if y < 1900:
        return None
    return dt.date(y, mo, d)


# ---------------------------------------------------------------- numbers

def to_int(v) -> int | None:
    """'1,234' / 1234.0 / None -> int | None。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    return int(round(float(s)))


def to_float(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").strip()
    if s in ("", "-"):
        return None
    return float(s)


# ---------------------------------------------------------------- snapshot

def new_snapshot(etf_cfg: dict, *, date: dt.date, posted_date: dt.date | None,
                 fetched_at: dt.datetime | None = None, source: str = "official") -> dict:
    """建立帶有固定欄位順序的空快照。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "etf": etf_cfg["code"],
        "name": etf_cfg.get("name"),
        "provider": etf_cfg["provider"],
        "date": date.isoformat(),
        "posted_date": posted_date.isoformat() if posted_date else None,
        "fetched_at": (fetched_at or now_taipei()).isoformat(),
        "source": source,
        "nav": None,
        "outstanding_units": None,
        "aum": None,
        "stock_value": None,
        "non_stock_total": None,
        "non_stock": None,
        "non_stock_items": [],
        "futures": [],
        "holdings": [],
        "extra": {},
    }


def finalize_snapshot(snap: dict) -> dict:
    """補算衍生欄位並做基本一致性檢查。"""
    if snap["aum"] is not None and snap["stock_value"] is not None:
        snap["non_stock_total"] = snap["aum"] - snap["stock_value"]
    snap["holdings"].sort(key=lambda h: (-(h.get("weight") or 0), h["code"]))

    # 檢查：持股市值合計不該超過 aum、代號不可重複
    codes = [h["code"] for h in snap["holdings"]]
    if len(codes) != len(set(codes)):
        dup = sorted({c for c in codes if codes.count(c) > 1})
        raise FetchError(f"{snap['etf']} {snap['date']}: duplicate holding codes {dup}")
    if snap["aum"] and snap["stock_value"] and snap["stock_value"] > snap["aum"] * 1.05:
        raise FetchError(
            f"{snap['etf']} {snap['date']}: stock_value {snap['stock_value']} > aum {snap['aum']}"
        )
    if not snap["holdings"]:
        raise FetchError(f"{snap['etf']} {snap['date']}: no holdings parsed")
    return snap


def snapshot_path(data_dir: str, etf: str, date: str) -> str:
    return os.path.join(data_dir, etf, f"{date}.json")


def write_snapshot(data_dir: str, snap: dict) -> tuple[str, str]:
    """寫入 data/<etf>/<date>.json。回傳 (path, 'created'|'updated'|'unchanged')。"""
    path = snapshot_path(data_dir, snap["etf"], snap["date"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_text = json.dumps(snap, ensure_ascii=False, indent=2) + "\n"
    status = "created"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
        # fetched_at 每次都不同，比較時忽略它
        strip = lambda t: re.sub(r'"fetched_at": "[^"]*"', '"fetched_at": ""', t)
        status = "unchanged" if strip(old) == strip(new_text) else "updated"
        if status == "unchanged":
            return path, status
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_text)
    return path, status


def load_etfs(path: str = "etfs.json") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["etfs"]
