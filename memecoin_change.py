#!/usr/bin/env python3
"""
Memecoin % change table.

Compares USD prices of a fixed basket of memecoins between two dates and
prints a Markdown table. Prices come from the CoinGecko public API
(/coins/{id}/history), with a Yahoo Finance fallback for tokens that have
a known Yahoo ticker.

Usage:
    python3 memecoin_change.py                      # default dates below
    python3 memecoin_change.py 2025-08-14 2026-08-26
    python3 memecoin_change.py --csv > out.csv

No third-party dependencies. A CoinGecko demo key (optional, raises the
rate limit) can be supplied via the COINGECKO_API_KEY env var.
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

START_DATE = "2025-08-14"
END_DATE = "2026-08-26"

# name, symbol, CoinGecko id, Yahoo Finance ticker (None if unknown)
TOKENS = [
    ("Apu Apustaja", "APU", "apu", "APU30008-USD"),
    ("HarryPotterObamaSonic10Inu (ERC-20)", "BITCOIN", "harrypotterobamasonic10in", "BITCOIN25220-USD"),
    ("Gigachad", "GIGA", "gigachad-2", "GIGA30063-USD"),
    ("SPX6900", "SPX", "spx6900", "SPX28081-USD"),
    ("Pepe", "PEPE", "pepe", "PEPE24478-USD"),
    ("Mog Coin", "MOG", "mog-coin", None),
    ("Brett", "BRETT", "based-brett", None),
]

CG_BASE = "https://api.coingecko.com/api/v3"
UA = "memecoin-change-table/1.0"
DELAY = 2.5  # seconds between CoinGecko calls (public rate limit is ~30/min)


def get_json(url, retries=4):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    key = os.environ.get("COINGECKO_API_KEY")
    if key and url.startswith(CG_BASE):
        headers["x-cg-demo-api-key"] = key
    backoff = 5
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
    return None


def cg_history_price(coin_id, date_iso):
    """USD price at 00:00 UTC on date_iso from CoinGecko."""
    d = datetime.strptime(date_iso, "%Y-%m-%d")
    url = f"{CG_BASE}/coins/{coin_id}/history?date={d:%d-%m-%Y}&localization=false"
    data = get_json(url)
    time.sleep(DELAY)
    try:
        return float(data["market_data"]["current_price"]["usd"])
    except (KeyError, TypeError):
        return None


def yahoo_close(ticker, date_iso):
    """Daily close on date_iso from Yahoo Finance chart API."""
    d = datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    p1 = int(d.timestamp()) - 86400
    p2 = int(d.timestamp()) + 2 * 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?period1={p1}&period2={p2}&interval=1d")
    data = get_json(url)
    try:
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
    except (KeyError, TypeError, IndexError):
        return None
    for t, c in zip(ts, closes):
        if datetime.fromtimestamp(t, timezone.utc).date() == d.date() and c is not None:
            return float(c)
    return None


def fetch_price(coin_id, yahoo_ticker, date_iso):
    src = "coingecko"
    price = None
    try:
        price = cg_history_price(coin_id, date_iso)
    except Exception as e:  # noqa: BLE001
        print(f"  coingecko failed for {coin_id} @ {date_iso}: {e}", file=sys.stderr)
    if price is None and yahoo_ticker:
        src = "yahoo"
        try:
            price = yahoo_close(yahoo_ticker, date_iso)
        except Exception as e:  # noqa: BLE001
            print(f"  yahoo failed for {yahoo_ticker} @ {date_iso}: {e}", file=sys.stderr)
    return price, (src if price is not None else None)


def fmt_price(p):
    if p is None:
        return "n/a"
    if p >= 1:
        return f"${p:,.4f}"
    if p >= 0.01:
        return f"${p:.5f}"
    return f"${p:.10f}".rstrip("0")


def fmt_pct(a, b):
    if a is None or b is None or a == 0:
        return "n/a"
    return f"{(b / a - 1) * 100:+.1f}%"


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    as_csv = "--csv" in argv
    start = args[0] if len(args) > 0 else START_DATE
    end = args[1] if len(args) > 1 else END_DATE

    rows = []
    for name, sym, cg_id, yt in TOKENS:
        print(f"fetching {name}...", file=sys.stderr)
        p0, s0 = fetch_price(cg_id, yt, start)
        p1, s1 = fetch_price(cg_id, yt, end)
        rows.append((name, sym, p0, p1, s0, s1))

    rows.sort(key=lambda r: (r[2] is None or r[3] is None, -(r[3] / r[2]) if r[2] and r[3] else 0))

    if as_csv:
        w = csv.writer(sys.stdout)
        w.writerow(["name", "symbol", f"price_{start}", f"price_{end}", "pct_change", "source_start", "source_end"])
        for name, sym, p0, p1, s0, s1 in rows:
            pct = "" if not (p0 and p1) else f"{(p1 / p0 - 1) * 100:.2f}"
            w.writerow([name, sym, p0 if p0 is not None else "", p1 if p1 is not None else "", pct, s0 or "", s1 or ""])
        return

    print(f"| Token | Symbol | Price {start} | Price {end} | % Change |")
    print("|---|---|---:|---:|---:|")
    for name, sym, p0, p1, _, _ in rows:
        print(f"| {name} | {sym} | {fmt_price(p0)} | {fmt_price(p1)} | {fmt_pct(p0, p1)} |")
    print()
    print("Prices are USD at 00:00 UTC (CoinGecko) or the daily close (Yahoo fallback).")


if __name__ == "__main__":
    main(sys.argv[1:])
