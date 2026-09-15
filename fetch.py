#!/usr/bin/env python
"""每日抓取入口。

用法：
  python fetch.py                          # 抓 etfs.json 全部，資料日 = 今天（台北）
  python fetch.py --etf 00981A             # 只抓一檔
  python fetch.py --provider uni           # 只抓某投信
  python fetch.py --date 2026-09-10        # 指定持股資料日（回補歷史）
  python fetch.py --date 2026-09-08 --date-to 2026-09-15   # 回補一段區間（逐日，跳過週末）

「資料日」是持股所屬的營業日；投信在該日 16:30 後公告，所以排程在 17:30 抓當天即可。
每檔的結果寫到 data/<etf>/<資料日>.json，並在 data/meta/fetch_log.jsonl 追加一行紀錄。
持股抓完會順便抓證交所的日收盤（涵蓋 --date..--date-to 的月份），併進 data/meta/prices.json；
--no-prices 略過、--prices-only 只抓行情。
一檔失敗不影響其他檔；只有「非預期錯誤」會讓 exit code 變成 2（「尚無資料」不算）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys

from fetchers import get_fetcher
from fetchers.common import FetchError, HttpClient, NoDataError, load_etfs, now_taipei, today_taipei, write_snapshot
from fetchers.prices import update_prices

log = logging.getLogger("fetch")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--etf", action="append", help="只抓這些代號（可重複）")
    p.add_argument("--provider", action="append", help="只抓這些投信（可重複）")
    p.add_argument("--date", type=dt.date.fromisoformat, help="持股資料日 YYYY-MM-DD，預設今天（台北）")
    p.add_argument("--date-to", type=dt.date.fromisoformat, help="與 --date 搭配，逐日抓到這天（含）")
    p.add_argument("--config", default="etfs.json")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--no-prices", action="store_true", help="不抓證交所行情")
    p.add_argument("--prices-only", action="store_true", help="只抓行情，不抓持股")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def iter_dates(start: dt.date, end: dt.date | None):
    end = end or start
    d = start
    while d <= end:
        if d.weekday() < 5:  # 週末直接跳過；國定假日交給來源回「無資料」
            yield d
        d += dt.timedelta(days=1)


def append_log(data_dir: str, record: dict):
    path = os.path.join(data_dir, "meta", "fetch_log.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def run(args) -> int:
    etfs = load_etfs(args.config)
    if args.etf:
        wanted = {c.upper() for c in args.etf}
        etfs = [e for e in etfs if e["code"].upper() in wanted]
        missing = wanted - {e["code"].upper() for e in etfs}
        if missing:
            log.error("unknown etf code(s): %s", ", ".join(sorted(missing)))
            return 2
    if args.provider:
        etfs = [e for e in etfs if e["provider"] in set(args.provider)]
    if not etfs:
        log.error("nothing to fetch")
        return 2

    start = args.date or today_taipei()
    clients: dict[str, HttpClient] = {}
    had_error = False

    for data_date in ([] if args.prices_only else iter_dates(start, args.date_to)):
        for cfg in etfs:
            code, provider = cfg["code"], cfg["provider"]
            fetcher = get_fetcher(provider)
            record = {"ts": now_taipei().isoformat(), "etf": code, "date": data_date.isoformat()}
            if fetcher is None:
                log.warning("%s: fetcher for provider %r not implemented, skipped", code, provider)
                append_log(args.data_dir, {**record, "status": "skipped", "reason": "no_fetcher"})
                continue
            client = clients.setdefault(provider, HttpClient())
            try:
                snap = fetcher(cfg, data_date, client)
                if snap["date"] != data_date.isoformat():
                    raise FetchError(f"fetcher returned data date {snap['date']}, wanted {data_date}")
                path, status = write_snapshot(args.data_dir, snap)
                log.info("%s %s: %s (%d holdings, nav %s, cash detail %s) %s",
                         code, data_date, status, len(snap["holdings"]), snap["nav"],
                         "yes" if snap["non_stock"] else "no", path)
                append_log(args.data_dir, {**record, "status": status, "posted_date": snap["posted_date"],
                                           "path": path.replace("\\", "/")})
            except NoDataError as e:
                log.warning("%s %s: no data (%s)", code, data_date, e)
                append_log(args.data_dir, {**record, "status": "no_data", "reason": str(e)})
            except FetchError as e:
                had_error = True
                log.error("%s %s: FAILED: %s", code, data_date, e)
                append_log(args.data_dir, {**record, "status": "error", "reason": str(e)})
            except Exception as e:  # 網路層或未預期的例外，也不能讓其他檔中斷
                had_error = True
                log.exception("%s %s: unexpected error", code, data_date)
                append_log(args.data_dir, {**record, "status": "error", "reason": f"{type(e).__name__}: {e}"})

    if not args.no_prices:
        end = args.date_to or start
        record = {"ts": now_taipei().isoformat(), "etf": "*", "date": end.isoformat(), "kind": "prices"}
        try:
            summary = update_prices(HttpClient(), [e["code"] for e in etfs], start, end, args.data_dir)
            for code, s in summary.items():
                log.info("prices %s: %d day(s) changed, %s close for %s", code, s["changed"],
                         "has" if s["has_end"] else "NO", end)
            append_log(args.data_dir, {**record, "status": "ok", "summary": summary})
        except Exception as e:
            had_error = True
            log.exception("prices: unexpected error")
            append_log(args.data_dir, {**record, "status": "error", "reason": f"{type(e).__name__}: {e}"})

    return 2 if had_error else 0


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
