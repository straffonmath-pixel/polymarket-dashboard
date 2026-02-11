#!/usr/bin/env python3
"""
Polymarket Sharp Wallet Discovery Tool

Crawls the Polymarket leaderboard, analyzes each trader's closed-position
track record, scores them on a composite "sharpness" metric, and outputs
a CSV ready for bulk import into the Alpha Tracker dashboard.

Usage:
    python3 discover.py

Output:
    sharp_wallets.csv         -- bulk import file for the dashboard
    sharp_wallets_report.md   -- human-readable analysis
"""

import urllib.request
import urllib.error
import json
import math
import time
import sys
import os
from datetime import datetime, timezone

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

API_BASE = "https://data-api.polymarket.com"
USER_AGENT = "PolymarketSharpDiscovery/1.0"

REQUEST_DELAY = 0.25          # seconds between API calls
MAX_RETRIES = 3
BACKOFF_BASE = 2.0

LEADERBOARD_LIMIT = 50
CLOSED_POS_LIMIT = 50
CLOSED_POS_MAX_PAGES = 4      # 4 × 50 = 200 positions max
ACTIVITY_LIMIT = 100

MIN_WIN_RATE = 0.55
MIN_POSITIONS = 20
MAX_INACTIVE_DAYS = 7
TOP_N = 100

CATEGORIES = ["OVERALL", "SPORTS", "POLITICS", "CRYPTO"]
TIME_PERIODS = ["DAY", "WEEK", "MONTH", "ALL"]

CATEGORY_KEYWORDS = {
    "Sports": [
        "nba", "nfl", "mlb", "nhl", "epl", "premier league", "champions league",
        "la liga", "serie a", "bundesliga", "ligue 1", "mls",
        "spread", "over/under", "o/u", "moneyline",
        "touchdown", "goal", "playoff", "super bowl", "world cup", "finals",
        "match", "game ", " win ", " wins ", "score", "points",
        "lakers", "celtics", "warriors", "chiefs", "cowboys", "arsenal",
        "chelsea", "knicks", "nets", "yankees", "dodgers", "rockets",
        "mvp", "ufc", "tennis", "boxing", "formula 1", "f1",
        " fc ", " fc:", "city vs", "united vs", "forest fc",
    ],
    "Politics": [
        "president", "election", "trump", "biden", "harris", "desantis",
        "senate", "congress", "governor", "democrat", "republican",
        "vote", "primary", "inauguration", "impeach", "cabinet",
        "executive order", "legislation", "bill ", "confirmation",
        "supreme court", "scotus", "speaker", "majority", "minority leader",
        "shutdown", "debt ceiling",
    ],
    "Crypto": [
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto",
        "token", "defi", "nft", "price above", "price below",
        "market cap", "halving", "memecoin", "doge", "xrp",
        "binance", "coinbase",
    ],
    "Macro": [
        "fed ", "federal reserve", "inflation", "gdp", "interest rate",
        "recession", "unemployment", "cpi", "treasury", "bond", "yield",
        "tariff", "trade war", "sanctions", "oil price",
    ],
}

# ──────────────────────────────────────────────
# HTTP / Rate Limiting
# ──────────────────────────────────────────────

_cache = {}
_request_count = 0


def api_get(path):
    """GET request to API_BASE + path with retries and backoff. Returns parsed JSON."""
    global _request_count

    if path in _cache:
        return _cache[path]

    url = API_BASE + path
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            _cache[path] = data
            _request_count += 1
            time.sleep(REQUEST_DELAY)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = BACKOFF_BASE ** (attempt + 1)
                print(f"  ⚠ {e.code} on {path[:60]}... retrying in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
            else:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            wait = BACKOFF_BASE ** (attempt + 1)
            print(f"  ⚠ Network error: {e} — retrying in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)

    raise Exception(f"Failed after {MAX_RETRIES} retries: {url}")


def api_get_safe(path, default=None):
    """Wrapper that returns default on any failure."""
    try:
        return api_get(path)
    except Exception as e:
        print(f"  ⚠ Skipping {path[:60]}...: {e}", file=sys.stderr)
        return default


# ──────────────────────────────────────────────
# Data Fetching
# ──────────────────────────────────────────────

def fetch_leaderboard_page(category, period, limit=50, offset=0):
    path = f"/v1/leaderboard?category={category}&timePeriod={period}&orderBy=PNL&limit={limit}&offset={offset}"
    data = api_get_safe(path, default=[])
    return data if isinstance(data, list) else []


def fetch_all_candidates():
    """Fetch leaderboards across categories × periods, deduplicate by address."""
    candidates = {}
    combos = [(c, p) for c in CATEGORIES for p in TIME_PERIODS]
    total = len(combos)

    for i, (cat, period) in enumerate(combos, 1):
        print(f"  [Leaderboard] Fetching {cat}/{period}... ({i}/{total})")
        entries = fetch_leaderboard_page(cat, period, limit=LEADERBOARD_LIMIT)

        for entry in entries:
            addr = (entry.get("proxyWallet") or "").lower()
            if not addr or len(addr) != 42:
                continue
            if addr not in candidates:
                candidates[addr] = {
                    "address": entry.get("proxyWallet", addr),
                    "userName": entry.get("userName") or "",
                    "best_pnl": float(entry.get("pnl") or 0),
                    "best_vol": float(entry.get("vol") or 0),
                    "source_categories": set(),
                }
            c = candidates[addr]
            c["best_pnl"] = max(c["best_pnl"], float(entry.get("pnl") or 0))
            c["best_vol"] = max(c["best_vol"], float(entry.get("vol") or 0))
            c["source_categories"].add(cat)

    print(f"  [Leaderboard] {len(candidates)} unique candidates from {total * LEADERBOARD_LIMIT} raw entries\n")
    return candidates


def fetch_closed_positions(address, max_positions=200):
    """Paginate through closed positions for a wallet."""
    all_positions = []
    pages = max_positions // CLOSED_POS_LIMIT

    for page in range(pages):
        offset = page * CLOSED_POS_LIMIT
        path = f"/closed-positions?user={address}&limit={CLOSED_POS_LIMIT}&offset={offset}&sortBy=TIMESTAMP&sortDirection=DESC"
        data = api_get_safe(path, default=[])
        if not isinstance(data, list):
            break
        all_positions.extend(data)
        if len(data) < CLOSED_POS_LIMIT:
            break  # no more pages

    return all_positions


def fetch_recent_activity(address, lookback_days=30):
    """Fetch recent trade activity for a wallet."""
    now = int(time.time())
    start = now - (lookback_days * 86400)
    path = f"/activity?user={address}&type=TRADE&limit={ACTIVITY_LIMIT}&start={start}&sortBy=TIMESTAMP&sortDirection=DESC"
    data = api_get_safe(path, default=[])
    return data if isinstance(data, list) else []


# ──────────────────────────────────────────────
# Analysis
# ──────────────────────────────────────────────

def analyze_closed_positions(positions):
    """Calculate win rate and PnL stats from closed positions."""
    if not positions:
        return {
            "total_positions": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "total_realized_pnl": 0.0,
            "avg_position_size": 0.0,
        }

    wins = 0
    losses = 0
    total_pnl = 0.0
    total_bought = 0.0

    for pos in positions:
        pnl = float(pos.get("realizedPnl") or 0)
        bought = float(pos.get("totalBought") or 0)
        total_pnl += pnl
        total_bought += bought
        if pnl > 0:
            wins += 1
        else:
            losses += 1

    total = wins + losses
    return {
        "total_positions": total,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / total if total > 0 else 0.0,
        "total_realized_pnl": total_pnl,
        "avg_position_size": total_bought / total if total > 0 else 0.0,
    }


def analyze_activity(trades):
    """Analyze recent trading activity."""
    if not trades:
        return {
            "last_trade_ts": 0,
            "trade_count_30d": 0,
            "total_volume_30d": 0.0,
            "days_since_last_trade": 999,
            "titles": [],
        }

    now = time.time()
    timestamps = []
    volume = 0.0
    titles = set()

    for t in trades:
        ts = t.get("timestamp") or 0
        if isinstance(ts, str):
            try:
                ts = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            except (ValueError, TypeError):
                ts = 0
        timestamps.append(ts)
        volume += float(t.get("usdcSize") or 0)
        title = t.get("title") or ""
        if title:
            titles.add(title)

    last_ts = max(timestamps) if timestamps else 0
    days_since = (now - last_ts) / 86400 if last_ts > 0 else 999

    return {
        "last_trade_ts": last_ts,
        "trade_count_30d": len(trades),
        "total_volume_30d": volume,
        "days_since_last_trade": days_since,
        "titles": list(titles),
    }


def classify_categories(titles):
    """Classify trade titles into categories using keyword matching."""
    if not titles:
        return ["Leaderboard"]

    category_scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        matches = 0
        for title in titles:
            title_lower = title.lower()
            if any(kw in title_lower for kw in keywords):
                matches += 1
        if matches > 0:
            category_scores[cat] = matches / len(titles)

    tags = []
    for cat, score in sorted(category_scores.items(), key=lambda x: -x[1]):
        if score >= 0.20:
            tags.append(cat)

    # If nothing hit 20%, take the best match if any
    if not tags and category_scores:
        best = max(category_scores, key=category_scores.get)
        tags.append(best)

    tags.append("Leaderboard")
    return tags


# ──────────────────────────────────────────────
# Scoring
# ──────────────────────────────────────────────

def compute_sharpness_score(win_rate, total_positions, days_since_last_trade, categories, avg_position_size):
    """Composite sharpness score. Higher = sharper."""

    # Win rate component: (WR - 0.50) * 40, capped at [0, 20]
    wr_component = max(0, min(20, (win_rate - 0.50) * 40))

    # Evidence component: log10(positions) * 15
    evidence = math.log10(max(total_positions, 1)) * 15

    # Recency: 1.0 if <=1 day, linear decay to 0 at 30 days
    if days_since_last_trade <= 1:
        recency = 1.0
    elif days_since_last_trade >= 30:
        recency = 0.0
    else:
        recency = 1.0 - (days_since_last_trade - 1) / 29.0
    recency_component = recency * 15

    # Category match: count non-Leaderboard tags
    real_cats = [c for c in categories if c != "Leaderboard"]
    if len(real_cats) >= 2:
        cat_score = 1.0
    elif len(real_cats) == 1:
        cat_score = 0.5
    else:
        cat_score = 0.0
    cat_component = cat_score * 20

    # Average size: log scale, $1 → 0, $10k → 1.0
    if avg_position_size > 0:
        size_score = min(1.0, math.log10(max(avg_position_size, 1)) / 4)
    else:
        size_score = 0.0
    size_component = size_score * 10

    return wr_component + evidence + recency_component + cat_component + size_component


# ──────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────

def format_addr(addr):
    """Shorten address for display."""
    return f"{addr[:8]}...{addr[-4:]}" if len(addr) >= 12 else addr


def run_discovery():
    """Main discovery pipeline."""
    start_time = time.time()

    # Phase 1: Fetch leaderboard candidates
    print("=" * 55)
    print("  Polymarket Sharp Wallet Discovery")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 55)
    print()

    candidates = fetch_all_candidates()
    if not candidates:
        print("No candidates found. Check your network connection.")
        return [], {}

    # Phase 2: Analyze each candidate
    results = []
    total = len(candidates)
    passed = 0
    skipped_positions = 0
    skipped_winrate = 0
    skipped_inactive = 0

    for i, (addr, info) in enumerate(candidates.items(), 1):
        display_name = info["userName"] or format_addr(addr)
        sys.stdout.write(f"  [Analysis] [{i}/{total}] {format_addr(addr)} {display_name} — ")
        sys.stdout.flush()

        # Fetch data
        positions = fetch_closed_positions(info["address"])
        activity = fetch_recent_activity(info["address"])

        # Analyze
        pos_stats = analyze_closed_positions(positions)
        act_stats = analyze_activity(activity)

        # Quick filter checks
        if pos_stats["total_positions"] < MIN_POSITIONS:
            print(f"{pos_stats['total_positions']} pos (skip: <{MIN_POSITIONS})")
            skipped_positions += 1
            continue

        if pos_stats["win_rate"] < MIN_WIN_RATE:
            print(f"{pos_stats['total_positions']} pos, {pos_stats['win_rate']:.1%} WR (skip: <{MIN_WIN_RATE:.0%})")
            skipped_winrate += 1
            continue

        if act_stats["days_since_last_trade"] > MAX_INACTIVE_DAYS:
            print(f"{pos_stats['total_positions']} pos, {pos_stats['win_rate']:.1%} WR (skip: inactive {act_stats['days_since_last_trade']:.0f}d)")
            skipped_inactive += 1
            continue

        # Classify and score
        all_titles = act_stats["titles"]
        # Also pull titles from closed positions for better classification
        for p in positions:
            t = p.get("title")
            if t:
                all_titles.append(t)
        all_titles = list(set(all_titles))

        categories = classify_categories(all_titles)
        score = compute_sharpness_score(
            pos_stats["win_rate"],
            pos_stats["total_positions"],
            act_stats["days_since_last_trade"],
            categories,
            pos_stats["avg_position_size"],
        )

        result = {
            "address": info["address"],
            "userName": info["userName"],
            "tags": categories,
            "score": score,
            "win_rate": pos_stats["win_rate"],
            "total_positions": pos_stats["total_positions"],
            "wins": pos_stats["wins"],
            "losses": pos_stats["losses"],
            "total_pnl": pos_stats["total_realized_pnl"],
            "avg_position_size": pos_stats["avg_position_size"],
            "days_since_last_trade": act_stats["days_since_last_trade"],
            "trade_count_30d": act_stats["trade_count_30d"],
            "total_volume_30d": act_stats["total_volume_30d"],
            "source_categories": info["source_categories"],
        }
        results.append(result)
        passed += 1

        tag_str = "|".join(categories)
        print(f"{pos_stats['total_positions']} pos, {pos_stats['win_rate']:.1%} WR, score={score:.1f} ✓ [{tag_str}]")

    # Sort and cap
    results.sort(key=lambda r: r["score"], reverse=True)
    results = results[:TOP_N]

    elapsed = time.time() - start_time
    stats = {
        "total_candidates": total,
        "passed_filters": passed,
        "exported": len(results),
        "skipped_positions": skipped_positions,
        "skipped_winrate": skipped_winrate,
        "skipped_inactive": skipped_inactive,
        "elapsed_seconds": elapsed,
        "api_requests": _request_count,
    }

    print()
    print(f"  [Filter] {total} candidates → {passed} passed filters → top {len(results)}")
    print(f"  [Filter] Skipped: {skipped_positions} too few positions, {skipped_winrate} low WR, {skipped_inactive} inactive")
    print()

    return results, stats


# ──────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────

def generate_nickname(result):
    """Generate an informative nickname for a wallet."""
    base_name = result["userName"]
    if not base_name or base_name == format_addr(result["address"]):
        # No username — build one from category
        cats = [t for t in result["tags"] if t != "Leaderboard"]
        prefix = cats[0] if cats else "Trader"
        base_name = f"{prefix}_{result['address'][2:8]}"

    return base_name


def format_pnl(value):
    """Format PnL for display."""
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:+.1f}M"
    elif abs(value) >= 1_000:
        return f"${value / 1_000:+.1f}K"
    else:
        return f"${value:+.0f}"


def write_csv(results, filepath):
    """Write CSV in dashboard bulk import format (no header)."""
    with open(filepath, "w") as f:
        for r in results:
            nickname = generate_nickname(r)
            tags = "|".join(r["tags"])
            f.write(f"{r['address']}, {nickname}, {tags}\n")
    print(f"  [Output] Wrote {len(results)} wallets to {os.path.basename(filepath)}")


def write_report(results, stats, filepath):
    """Write markdown analysis report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    elapsed_m = stats["elapsed_seconds"] / 60

    lines = []
    lines.append("# Sharp Wallet Discovery Report\n")
    lines.append(f"**Generated:** {now}  ")
    lines.append(f"**Runtime:** {elapsed_m:.1f} minutes ({stats['api_requests']} API calls)  \n")

    lines.append("\n## Summary\n")
    lines.append(f"- Leaderboard candidates scanned: **{stats['total_candidates']}**")
    lines.append(f"- Passed filters (>{MIN_WIN_RATE:.0%} WR, {MIN_POSITIONS}+ positions, active {MAX_INACTIVE_DAYS}d): **{stats['passed_filters']}**")
    lines.append(f"- Skipped: {stats['skipped_positions']} too few positions, {stats['skipped_winrate']} low win rate, {stats['skipped_inactive']} inactive")
    lines.append(f"- Top wallets exported: **{stats['exported']}**\n")

    # Category distribution
    cat_counts = {}
    for r in results:
        for tag in r["tags"]:
            if tag != "Leaderboard":
                cat_counts[tag] = cat_counts.get(tag, 0) + 1
    lines.append("## Category Distribution\n")
    for cat in sorted(cat_counts, key=cat_counts.get, reverse=True):
        bar = "█" * min(40, cat_counts[cat])
        lines.append(f"- **{cat}**: {cat_counts[cat]} wallets {bar}")
    lines.append("")

    # Top 20 table
    lines.append("## Top 20 Sharp Wallets\n")
    lines.append("| # | Nickname | Score | Win Rate | Positions | 30d PnL | Avg Size | Tags |")
    lines.append("|---|----------|-------|----------|-----------|---------|----------|------|")
    for i, r in enumerate(results[:20], 1):
        nickname = generate_nickname(r)
        tags = ", ".join(t for t in r["tags"] if t != "Leaderboard")
        pnl = format_pnl(r["total_pnl"])
        avg_size = f"${r['avg_position_size']:,.0f}" if r["avg_position_size"] >= 1 else "<$1"
        lines.append(
            f"| {i} | {nickname} | {r['score']:.1f} | {r['win_rate']:.1%} | "
            f"{r['total_positions']} | {pnl} | {avg_size} | {tags} |"
        )
    lines.append("")

    # All wallets brief list
    if len(results) > 20:
        lines.append("## Full List (remaining)\n")
        lines.append("| # | Nickname | Score | Win Rate | Positions | Tags |")
        lines.append("|---|----------|-------|----------|-----------|------|")
        for i, r in enumerate(results[20:], 21):
            nickname = generate_nickname(r)
            tags = ", ".join(t for t in r["tags"] if t != "Leaderboard")
            lines.append(
                f"| {i} | {nickname} | {r['score']:.1f} | {r['win_rate']:.1%} | "
                f"{r['total_positions']} | {tags} |"
            )
        lines.append("")

    # Usage instructions
    lines.append("## How to Import\n")
    lines.append("1. Open the Polymarket Alpha Tracker dashboard")
    lines.append("2. Click **Bulk Import** in the sidebar")
    lines.append("3. Copy the contents of `sharp_wallets.csv`")
    lines.append("4. Paste into the import textarea and click **Import**\n")

    lines.append("## Rerun\n")
    lines.append("```bash")
    lines.append("python3 discover.py")
    lines.append("```\n")

    with open(filepath, "w") as f:
        f.write("\n".join(lines))
    print(f"  [Output] Wrote report to {os.path.basename(filepath)}")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(script_dir, "sharp_wallets.csv")
    report_path = os.path.join(script_dir, "sharp_wallets_report.md")

    results, stats = run_discovery()

    if not results:
        print("  No sharp wallets found. Try relaxing filters or checking the API.")
        return

    write_csv(results, csv_path)
    write_report(results, stats, report_path)

    elapsed = stats["elapsed_seconds"]
    print()
    print("=" * 55)
    print(f"  Done in {elapsed / 60:.1f} minutes ({stats['api_requests']} API calls)")
    print()
    print("  Top 5:")
    for i, r in enumerate(results[:5], 1):
        nickname = generate_nickname(r)
        tags = "|".join(r["tags"])
        print(f"    {i}. {nickname:20s} {r['score']:5.1f}  {r['win_rate']:.1%} WR  {r['total_positions']:3d} pos  {tags}")
    print()
    print(f"  Files: {os.path.basename(csv_path)}, {os.path.basename(report_path)}")
    print("=" * 55)


if __name__ == "__main__":
    main()
