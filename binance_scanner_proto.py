#!/usr/bin/env python3
"""
Binance Spot Scanner
──────────────────────────────────────────────────────────────────────────────
Budget    : $10 USD  |  BNB-paid fees (no deduction)
Quote     : USDT and USDC pairs
Filter    : Backtested win rate  9.87 % – 14.40 %
Strategy  : RSI-oversold bounce near 20-bar low  |  8 : 1 R:R  (ATR-based)
Candles   : 4 h  |  Up to 500 bars per symbol

SL order layout (Binance Stop-Limit):
  SL Trigger → activates the limit order
  SL         → the actual limit sell price (slightly below trigger)

Install:
    pip install requests pandas numpy

Run:
    python binance_scanner.py
──────────────────────────────────────────────────────────────────────────────
"""

import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

# ── Configuration ─────────────────────────────────────────────────────────────
BASE_URL    = "https://api.binance.com"
BUDGET      = 10.0        # USD

# Win-rate window
MIN_WR      = 0.0987      # 9.87 %
MAX_WR      = 0.1440      # 14.40 %

# ATR multipliers
TP_MULT     = 8.0         # TP   = entry + ATR * TP_MULT
SL_MULT     = 1.0         # SL   = entry − ATR * SL_MULT
TRIG_MULT   = 0.15        # Trigger offset: SL + ATR * TRIG_MULT  (fires first)

# Filters
MIN_VOL     = 300_000     # Minimum 24 h quote-volume
MIN_SIGNALS = 8           # Minimum backtest signals to trust win rate
MAX_SCAN    = 200         # Cap on pairs scanned (sorted by volume)

# Candles
INTERVAL    = "4h"
N_CANDLES   = 500

# Backtest parameters
RSI_LOW     = 20          # Oversold entry lower bound
RSI_HIGH    = 36          # Oversold entry upper bound
LO_LOOKBACK = 20          # Bars for "near recent low" check
LO_MARGIN   = 1.025       # Price must be ≤ lo20 * LO_MARGIN
MIN_ATR_PCT = 0.004       # Skip coins where ATR/price < this (too flat)
FWD_BARS    = 72          # Max forward bars to check for outcome
COOL_DOWN   = 5           # Min bars between two signals (avoid overlap)
# ─────────────────────────────────────────────────────────────────────────────


# ── Network ───────────────────────────────────────────────────────────────────

def GET(url: str, params: Optional[Dict] = None, retries: int = 3) -> Optional[any]:
    """GET with retries and basic 429 handling."""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=12)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30))
                print(f"\n  [Rate-limited]  Waiting {wait} s …", flush=True)
                time.sleep(wait)
                continue
            if r.status_code == 418:          # IP banned temporarily
                time.sleep(60)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            time.sleep(1)
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
    return None


# ── Exchange helpers ───────────────────────────────────────────────────────────

def get_pairs() -> List[Dict]:
    """Fetch exchange info and return eligible USDT / USDC spot pairs."""
    data = GET(f"{BASE_URL}/api/v3/exchangeInfo")
    if not data:
        sys.exit("  [ERROR]  Cannot reach Binance API. Check your connection.")

    blocked_suffixes = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "2L", "2S")
    pairs: List[Dict] = []

    for s in data["symbols"]:
        if (
            s["status"] != "TRADING"
            or s["quoteAsset"] not in ("USDT", "USDC")
            or not s["isSpotTradingAllowed"]
            or any(s["baseAsset"].endswith(x) for x in blocked_suffixes)
        ):
            continue

        fmap = {f["filterType"]: f for f in s["filters"]}

        # min notional — Binance uses "NOTIONAL" in newer response, "MIN_NOTIONAL" in older
        nf      = fmap.get("NOTIONAL") or fmap.get("MIN_NOTIONAL") or {}
        min_val = float(nf.get("minNotional", 1.0))
        if min_val > BUDGET:
            continue

        lot_f  = fmap.get("LOT_SIZE", {})
        tick_f = fmap.get("PRICE_FILTER", {})

        pairs.append(dict(
            symbol  = s["symbol"],
            base    = s["baseAsset"],
            quote   = s["quoteAsset"],
            min_val = min_val,
            step    = float(lot_f.get("stepSize",  "0.01")),
            tick    = float(tick_f.get("tickSize", "0.0001")),
        ))

    return pairs


def enrich_ticker(pairs: List[Dict]) -> List[Dict]:
    """Add 24 h price, volume, % change; apply volume + affordability filter."""
    data = GET(f"{BASE_URL}/api/v3/ticker/24hr")
    if not data:
        print("  [WARN]  Could not fetch ticker — continuing without volume data.")
        return pairs

    tmap = {t["symbol"]: t for t in data}
    out: List[Dict] = []

    for p in pairs:
        t = tmap.get(p["symbol"])
        if not t:
            continue
        price = float(t["lastPrice"])
        vol   = float(t["quoteVolume"])
        if price <= 0 or vol < MIN_VOL:
            continue
        if BUDGET / price < 1e-6:       # can't buy a meaningful quantity
            continue
        out.append({
            **p,
            "price"  : price,
            "volume" : vol,
            "chg24"  : float(t["priceChangePercent"]),
        })

    return out


# ── Candle data ───────────────────────────────────────────────────────────────

def fetch_candles(symbol: str) -> Optional[pd.DataFrame]:
    raw = GET(
        f"{BASE_URL}/api/v3/klines",
        {"symbol": symbol, "interval": INTERVAL, "limit": N_CANDLES},
    )
    if not raw or len(raw) < 60:
        return None

    cols = ["ts", "open", "high", "low", "close", "vol",
            "cts", "qvol", "n", "tbv", "tqv", "_"]
    df = pd.DataFrame(raw, columns=cols)
    num = ["open", "high", "low", "close", "vol", "qvol"]
    df[num] = df[num].astype(float)
    return df


# ── Indicators ────────────────────────────────────────────────────────────────

def calc_atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def calc_rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d   = close.diff()
    g   = d.clip(lower=0).rolling(n).mean()
    ls  = (-d.clip(upper=0)).rolling(n).mean()
    rs  = g / ls.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# ── Backtest ──────────────────────────────────────────────────────────────────

def backtest(df: pd.DataFrame, atr_s: pd.Series) -> Tuple[Optional[float], int]:
    """
    Signal: RSI oversold (20–36) AND close ≤ 20-bar low × 1.025
    TP:     entry + ATR × TP_MULT      (8 ATR above)
    SL:     entry − ATR × SL_MULT      (1 ATR below)
    Outcome checked over the next FWD_BARS candles.

    Returns (win_rate, n_signals) or (None, 0) if too few signals.
    """
    rsi_s  = calc_rsi(df["close"])
    wins   = losses = 0
    last_i = -(COOL_DOWN + 1)
    start  = max(LO_LOOKBACK + 14 + 1, 30)

    for i in range(start, len(df) - 1):
        if i - last_i < COOL_DOWN:
            continue

        av = atr_s.iloc[i]
        rv = rsi_s.iloc[i]
        if np.isnan(av) or av == 0 or np.isnan(rv):
            continue

        pr = df["close"].iloc[i]
        if av / pr < MIN_ATR_PCT:       # coin is too flat
            continue

        lo20 = df["low"].iloc[i - LO_LOOKBACK: i].min()

        # ── Entry condition ──────────────────────────────────────────────────
        if not (RSI_LOW <= rv <= RSI_HIGH and pr <= lo20 * LO_MARGIN):
            continue

        tp_p = pr + av * TP_MULT
        sl_p = pr - av * SL_MULT

        # ── Forward outcome ──────────────────────────────────────────────────
        for j in range(i + 1, min(i + FWD_BARS + 1, len(df))):
            hi = df["high"].iloc[j]
            lo = df["low"].iloc[j]
            if hi >= tp_p:
                wins   += 1
                last_i  = i
                break
            if lo <= sl_p:
                losses += 1
                last_i  = i
                break

    total = wins + losses
    if total < MIN_SIGNALS:
        return None, 0
    return wins / total, total


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt(v: float) -> str:
    """Smart price formatter."""
    if v == 0:      return "0"
    if v >= 1000:   return f"{v:,.2f}"
    if v >= 1:      return f"{v:.4f}"
    if v >= 0.01:   return f"{v:.6f}"
    return f"{v:.8f}"


def pct(a: float, ref: float) -> float:
    return (a - ref) / ref * 100


def bar(n: int, total: int, width: int = 46) -> str:
    filled = int(width * n / total)
    return "█" * filled + "░" * (width - filled)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║      BINANCE SPOT SCANNER  ·  $10 Budget  ·  BNB Fees           ║")
    print(f"║  Win Rate: {MIN_WR*100:.2f}%–{MAX_WR*100:.2f}%  ·  {TP_MULT:.0f}:1 R:R  ·  Interval: {INTERVAL}            ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()

    # ── 1. Load pairs ─────────────────────────────────────────────────────
    print("  [1/3]  Loading exchange info …")
    pairs = get_pairs()
    print(f"         {len(pairs)} USDT / USDC spot pairs eligible")
    print()

    # ── 2. Ticker filter ──────────────────────────────────────────────────
    print("  [2/3]  Applying price & volume filter (≥ $300 k / 24 h) …")
    pairs = enrich_ticker(pairs)
    pairs.sort(key=lambda x: x["volume"], reverse=True)   # top volume first
    n_scan = min(len(pairs), MAX_SCAN)
    print(f"         {len(pairs)} pairs pass — scanning top {n_scan} by 24 h volume")
    print()

    # ── 3. Candle scan & backtest ─────────────────────────────────────────
    print(f"  [3/3]  Running backtest  ({INTERVAL} candles, ATR-14, RSI-14) …")
    print()

    candidates: List[Dict] = []

    for idx, p in enumerate(pairs[:n_scan], 1):
        sym = p["symbol"]
        print(f"         [{bar(idx, n_scan)}]  {idx:>3}/{n_scan}  {sym:<14}", end="\r", flush=True)

        df = fetch_candles(sym)
        if df is None:
            time.sleep(0.06)
            continue

        atr_s = calc_atr(df)
        av    = atr_s.iloc[-1]
        if np.isnan(av) or av == 0:
            time.sleep(0.06)
            continue

        wr, sigs = backtest(df, atr_s)
        if wr is None:
            time.sleep(0.07)
            continue

        if MIN_WR <= wr <= MAX_WR:
            pr   = p["price"]
            tp   = pr + av * TP_MULT
            sl   = pr - av * SL_MULT
            trig = sl + av * TRIG_MULT      # trigger fires before limit executes
            qty  = BUDGET / pr
            rr   = (tp - pr) / max(pr - sl, 1e-12)

            candidates.append({
                **p,
                "atr"   : av,
                "wr"    : wr,
                "sigs"  : sigs,
                "tp"    : tp,
                "sl"    : sl,
                "trig"  : trig,
                "qty"   : qty,
                "gain"  :  (tp - pr) * qty,
                "loss"  : -(pr - sl) * qty,   # shown as negative
                "rr"    : rr,
            })

        time.sleep(0.08)   # stay well under Binance rate limits

    print()
    print()

    # ── Results ───────────────────────────────────────────────────────────
    SEP  = "═" * 68
    sep2 = "─" * 64

    if not candidates:
        print("  ⚠   No pairs matched the win-rate window at this moment.")
        print("      Markets shift hourly — re-run in a few hours.")
        print()
        return

    # Sort: highest win rate first; break ties by volume
    candidates.sort(key=lambda x: (x["wr"], x["volume"]), reverse=True)

    print(SEP)
    print(f"  RESULTS  ·  {len(candidates)} candidate(s)  ·  {datetime.now():%Y-%m-%d  %H:%M:%S}")
    print(SEP)

    for i, c in enumerate(candidates, 1):
        pr  = c["price"]
        q   = c["quote"]
        b   = c["base"]
        atp = c["atr"] / pr * 100   # ATR as % of price

        ev = c["wr"] * TP_MULT - (1 - c["wr"]) * SL_MULT
        ev_str = f"{ev:+.3f}"

        print(f"""
  # {i}   {c['symbol']}   ({q} pair)
  {sep2}
  Current Price    {fmt(pr):>18}  {q}
  24 h Volume      {c['volume']:>18,.0f}  {q}
  24 h Change      {c['chg24']:>+17.2f} %
  ATR ({INTERVAL})         {fmt(c['atr']):>18}  ({atp:.2f} %)
  Win Rate         {c['wr']*100:>17.2f} %   ({c['sigs']} signals)
  R : R            {c['rr']:>17.1f} : 1
  Exp. Value/risk  {ev_str:>17}  (per $1 risked)

  ┌─ ENTRY    ──── {fmt(pr):>18}  {q}
  ├─ TP       ──── {fmt(c['tp']):>18}  {q}   ({pct(c['tp'],  pr):>+7.2f} %)
  ├─ SL Trig  ──── {fmt(c['trig']):>18}  {q}   ({pct(c['trig'], pr):>+7.2f} %)
  └─ SL       ──── {fmt(c['sl']):>18}  {q}   ({pct(c['sl'],  pr):>+7.2f} %)

  With ${BUDGET:.0f} budget:
    Quantity     {c['qty']:>18.6f}  {b}
    If TP hit    {c['gain']:>+18.4f}  {q}
    If SL hit    {c['loss']:>+18.4f}  {q}""")

    print()
    print(SEP)
    print()
    print("  How to place the trade on Binance:")
    print("  ① Spot buy at ENTRY (market or limit)")
    print("  ② Set OCO order:  Limit sell @ TP  |  Stop-Limit: Trigger @ SL Trig, Limit @ SL")
    print()
    print("  ⚠  Backtest ≠ future results.  Only trade what you can afford to lose.")
    print()
    print(SEP)
    print()


if __name__ == "__main__":
    main()
