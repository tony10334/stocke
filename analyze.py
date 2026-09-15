#!/usr/bin/env python
"""分析層入口：讀 data/<etf>/*.json，輸出前端用的 data/site-data.json。

用法：
  python analyze.py                      # 讀 etfs.json + data/，寫 data/site-data.json
  python analyze.py --out site/public/site-data.json
  python analyze.py --co-min 3           # 臨時覆蓋門檻（正式設定放 etfs.json 的 "analysis"）
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from analysis.core import DEFAULT_CONFIG, build_site_data, load_prices, load_snapshots

log = logging.getLogger("analyze")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="etfs.json")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--out", default=os.path.join("data", "site-data.json"))
    p.add_argument("--co-min", type=int, help="覆蓋 co_signal_min_etfs")
    p.add_argument("--min-change", type=float, help="覆蓋 min_per_unit_change_pct")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s %(name)s: %(message)s", stream=sys.stderr)

    with open(args.config, encoding="utf-8") as f:
        conf = json.load(f)
    etfs = conf["etfs"]
    cfg = {**DEFAULT_CONFIG, **conf.get("analysis", {})}
    if args.co_min is not None:
        cfg["co_signal_min_etfs"] = args.co_min
    if args.min_change is not None:
        cfg["min_per_unit_change_pct"] = args.min_change

    snapshots = load_snapshots(args.data_dir, [e["code"] for e in etfs])
    n = sum(len(v) for v in snapshots.values())
    if n == 0:
        log.error("no snapshots under %s", args.data_dir)
        return 2

    prices = load_prices(args.data_dir)
    if not prices:
        log.warning("no %s/meta/prices.json; close/premium will be null", args.data_dir)
    site = build_site_data(etfs, snapshots, cfg, prices=prices)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(site, f, ensure_ascii=False, indent=1)
        f.write("\n")

    log.info("%d snapshots, %d dates (%s .. %s) -> %s (%.0f KB)",
             n, len(site["dates"]), site["dates"][0], site["dates"][-1], args.out, os.path.getsize(args.out) / 1024)
    for o in site["overview"]:
        log.info("  %s %s nav %s close %s premium %s%%  non-stock %s%% (%+s)  holdings %d  changes %s",
                 o["etf"], o["date"], o["nav"], o["close"], o["premium_pct"], o["non_stock_pct"],
                 o["non_stock_pct_change"], o["holdings_count"], o["latest_changes"])
    if site["intervals"]:
        iv = site["intervals"][0]
        log.info("  latest interval %s -> %s (etfs %s, excluded %s): co_add %d, co_reduce %d, corporate_actions %d",
                 iv["from"], iv["to"], ",".join(iv["etfs"]), ",".join(iv["excluded"]) or "-",
                 len(iv["co_add"]), len(iv["co_reduce"]), len(iv["corporate_actions"]))
        for s in iv["co_add"]:
            log.info("    co_add %s %s: %s", s["code"], s["name"], ", ".join(f"{x['etf']} {x['action']} {x['per_unit_change_pct']}%" for x in s["etfs"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
