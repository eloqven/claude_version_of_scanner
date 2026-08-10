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

import re
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests


def _setup_console() -> None:
    """Force UTF-8 output with replacement fallback (Windows cp1252 safety)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


_setup_console()


# ── ANSI palette ──────────────────────────────────────────────────────────────

GRN = "\033[92m";  RED = "\033[91m";  YLW = "\033[93m"
CYN = "\033[96m";  DIM = "\033[2m";   BLD = "\033[1m";  RST = "\033[0m"
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


# ── File logging ──────────────────────────────────────────────────────────────

class _Tee:
    """Duplicate console output into a UTF-8 log file with line timestamps."""

    def __init__(self, target, fh) -> None:
        self._target = target
        self._fh = fh
        self._line_start = True

    def write(self, text: str) -> int:
        self._target.write(text)
        clean = _ANSI_RE.sub("", text).replace("\r", "\n")
        for i, chunk in enumerate(clean.split("\n")):
            if i > 0:
                self._fh.write("\n")
            if self._line_start and chunk and not chunk.startswith("["):
                self._fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ")
            self._fh.write(chunk)
            self._line_start = True
        self._fh.flush()
        return len(text)

    def flush(self) -> None:
        self._target.flush()
        self._fh.flush()

    def isatty(self) -> bool:
        return False


_LOG_ACTIVE = False
_LOG_PATH = ""


def init_logfile(prefix: str = "proto", logdir: str = "logs") -> str:
    """Tee stdout/stderr into a timestamped UTF-8 log file. Returns its path."""
    global _LOG_ACTIVE, _LOG_PATH
    if _LOG_ACTIVE:
        return _LOG_PATH
    path = Path(logdir) / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.log"
    Path(logdir).mkdir(parents=True, exist_ok=True)
    fh = open(path, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)
    _LOG_ACTIVE = True
    _LOG_PATH = str(path)
    return _LOG_PATH


# ── Verbose console logger ─────────────────────────────────────────────────────

class _Log:
    """Colored console logger — mirrors the V1 verbose output style."""

    @staticmethod
    def _ts() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def raw(self, s: str = "")  -> None: print(s)
    def info(self, msg: str)    -> None: print(f"{DIM}[{self._ts()}] INFO{RST}  {msg}")
    def ok(self, msg: str)      -> None: print(f"{GRN}[{self._ts()}] PASS{RST}  {msg}")
    def skip(self, msg: str)    -> None: print(f"{YLW}[{self._ts()}] SKIP{RST}  {msg}")
    def fail(self, msg: str)    -> None: print(f"{RED}[{self._ts()}] FAIL{RST}  {msg}")

    def section(self, title: str) -> None:
        bar = "─" * 70
        print(f"\n{CYN}{BLD}{bar}{RST}")
        print(f"{CYN}{BLD}  {title}{RST}")
        print(f"{CYN}{BLD}{bar}{RST}")

    def header(self, rows: List[str]) -> None:
        bar = "═" * 70
        print(f"\n{BLD}{bar}{RST}")
        for r in rows:
            print(f"  {r}")
        print(f"{BLD}{bar}{RST}")

    def pair_start(self, idx: int, total: int, sym: str,
                   price: str, vol: float, q: str) -> None:
        print(f"\n{BLD}[{self._ts()}] PAIR [{idx:>3}/{total}] "
              f"{CYN}{sym:<16}{RST}{BLD} price={price} vol={vol:,.0f} {q}{RST}")

    def tree(self, last: bool, label: str, val: str, st: str = "") -> None:
        pfx   = "└─" if last else "├─"
        badge = {"ok": f" {GRN}✓{RST}", "fail": f" {RED}✗{RST}",
                 "warn": f" {YLW}!{RST}"}.get(st, "")
        print(f"               {pfx} {BLD}{label:<16}{RST}{val}{badge}")


log = _Log()


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

def exchange_filter_counts(data: List[Dict],
                           log: Optional[_Log] = None) -> Tuple[List[Dict], Dict[str, int]]:
    """Apply the eligibility filters, counting per-reason rejections.

    Returns (eligible pairs, counts dict). get_pairs() delegates here.
    """
    counts: Dict[str, int] = dict(
        NOT_TRADING=0, WRONG_QUOTE=0, NOT_SPOT=0,
        HIGH_NOTIONAL=0, PASSED=0
    )
    pairs: List[Dict] = []

    for s in data["symbols"]:
        sym = s["symbol"]

        if s["status"] != "TRADING":
            counts["NOT_TRADING"] += 1
            continue
        if s["quoteAsset"] not in ("USDT", "USDC"):
            counts["WRONG_QUOTE"] += 1
            continue
        if not s["isSpotTradingAllowed"]:
            counts["NOT_SPOT"] += 1
            continue

        fmap = {f["filterType"]: f for f in s["filters"]}

        # min notional — Binance uses "NOTIONAL" in newer response, "MIN_NOTIONAL" in older
        nf      = fmap.get("NOTIONAL") or fmap.get("MIN_NOTIONAL") or {}
        min_val = Decimal(nf.get("minNotional", "1.0"))
        if min_val > Decimal(str(BUDGET)):
            counts["HIGH_NOTIONAL"] += 1
            if log:
                log.skip(f"{sym:<18} HIGH_MIN_NOTIONAL  "
                         f"minNotional={min_val:.2f} > budget={BUDGET:.2f}")
            continue

        lot_f  = fmap.get("LOT_SIZE", {})
        tick_f = fmap.get("PRICE_FILTER", {})

        counts["PASSED"] += 1
        pairs.append(dict(
            symbol  = s["symbol"],
            base    = s["baseAsset"],
            quote   = s["quoteAsset"],
            min_val = min_val,
            min_qty = Decimal(lot_f.get("minQty", "0")),
            step    = Decimal(lot_f.get("stepSize",  "0.01")),
            tick    = Decimal(tick_f.get("tickSize", "0.0001")),
        ))

    return pairs, counts


def get_pairs() -> List[Dict]:
    """Fetch exchange info and return eligible USDT / USDC spot pairs."""
    data = GET(f"{BASE_URL}/api/v3/exchangeInfo")
    if not data:
        sys.exit("  [ERROR]  Cannot reach Binance API. Check your connection.")
    pairs, _ = exchange_filter_counts(data)
    return pairs


def ticker_filter_counts(pairs: List[Dict],
                         log: Optional[_Log] = None) -> Tuple[List[Dict], Dict[str, int]]:
    """Add 24 h price, volume, % change; apply volume + affordability filter.

    Counts each rejection reason. Returns (passing pairs, counts dict) —
    enrich_ticker() delegates here with identical acceptance criteria.
    """
    data = GET(f"{BASE_URL}/api/v3/ticker/24hr")
    if not data:
        sys.exit("  [ERROR]  Cannot fetch 24 h ticker data — aborting scan.")

    tmap = {t["symbol"]: t for t in data}
    counts: Dict[str, int] = dict(
        NO_TICKER=0, ZERO_PRICE=0, LOW_VOL=0,
        UNAFFORDABLE=0, PASSED=0
    )
    out: List[Dict] = []

    for p in pairs:
        sym = p["symbol"]
        t   = tmap.get(sym)

        if not t:
            counts["NO_TICKER"] += 1
            if log:
                log.skip(f"{sym:<18} NO_TICKER       — symbol absent from 24 h ticker")
            continue

        price = float(t["lastPrice"])
        vol   = float(t["quoteVolume"])

        if price <= 0:
            counts["ZERO_PRICE"] += 1
            if log:
                log.skip(f"{sym:<18} ZERO_PRICE      — lastPrice={price}")
            continue

        if vol < MIN_VOL:
            counts["LOW_VOL"] += 1
            if log:
                log.skip(f"{sym:<18} LOW_VOLUME      — "
                         f"vol={vol:>14,.0f}  (need ≥ {MIN_VOL:,.0f} {p['quote']})")
            continue

        if BUDGET / price < 1e-6:       # can't buy a meaningful quantity
            counts["UNAFFORDABLE"] += 1
            if log:
                log.skip(f"{sym:<18} UNAFFORDABLE    — "
                         f"price={fmt(price)}  budget/price={BUDGET/price:.2e}")
            continue

        counts["PASSED"] += 1
        if log:
            log.ok(f"{sym:<18} PASS            — "
                   f"price={fmt(price):>12}  vol={vol:>16,.0f}  "
                   f"chg={float(t['priceChangePercent']):>+7.2f}%")
        out.append({
            **p,
            "price"  : price,
            "volume" : vol,
            "chg24"  : float(t["priceChangePercent"]),
        })

    return out, counts


def enrich_ticker(pairs: List[Dict]) -> List[Dict]:
    """Add 24 h price, volume, % change; apply volume + affordability filter."""
    out, _ = ticker_filter_counts(pairs)
    return out


# ── Candle data ───────────────────────────────────────────────────────────────

def fetch_candles(symbol: str, interval: str = INTERVAL,
                  start_ms: Optional[int] = None,
                  end_ms: Optional[int] = None,
                  limit: int = N_CANDLES) -> Optional[pd.DataFrame]:
    params: Dict = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        params["endTime"] = end_ms
    raw = GET(f"{BASE_URL}/api/v3/klines", params)
    if not raw or (start_ms is None and len(raw) < 60):
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

_CHILD_INTERVAL = {
    "1M": "1d", "1w": "1d", "3d": "1d",
    "1d": "12h", "12h": "6h", "8h": "4h",
    "6h": "2h", "4h": "2h", "2h": "1h",
    "1h": "30m", "30m": "15m", "15m": "5m",
    "5m": "1m", "3m": "1m", "1m": "1s",
}

_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000,
    "1w": 604_800_000, "1M": 2_592_000_000,
}


def _resolve_dual_hit(symbol: str, interval: str,
                      open_ts_ms: int, close_ts_ms: int,
                      tp_p: float, sl_p: float) -> str:
    """Resolve a same-candle TP/SL dual hit by drilling into lower timeframes."""
    child = _CHILD_INTERVAL.get(interval)
    if child is None:
        return "loss"
    df = fetch_candles(symbol, interval=child, start_ms=open_ts_ms,
                       end_ms=close_ts_ms, limit=1000)
    if df is None or df.empty:
        return "loss"
    df = df[(df["ts"] >= open_ts_ms) & (df["ts"] < close_ts_ms)]
    if df.empty:
        return "loss"
    for k in range(len(df)):
        hit_tp = df["high"].iloc[k] >= tp_p
        hit_sl = df["low"].iloc[k] <= sl_p
        if hit_tp and not hit_sl:
            return "win"
        if hit_sl and not hit_tp:
            return "loss"
        if child == "1s":
            return "loss"
        c_open = int(df["ts"].iloc[k])
        if k + 1 < len(df):
            c_close = int(df["ts"].iloc[k + 1])
        else:
            c_close = c_open + _INTERVAL_MS.get(child, 0)
        return _resolve_dual_hit(symbol, child, c_open, c_close, tp_p, sl_p)
    return "loss"


def _backtest_detail(df: pd.DataFrame, atr_s: pd.Series,
                     symbol: str) -> Tuple[int, int, int, int]:
    """
    Signal: RSI oversold (20–36) AND close ≤ 20-bar low × 1.025
    TP:     entry + ATR × TP_MULT      (8 ATR above)
    SL:     entry − ATR × SL_MULT      (1 ATR below)
    Outcome checked over the next FWD_BARS candles.

    Returns (wins, losses, total_signals, flat_candle_skips).
    """
    rsi_s  = calc_rsi(df["close"])
    wins   = losses = flat_skips = 0
    last_i = -(COOL_DOWN + 1)
    start  = max(LO_LOOKBACK + 14 + 1, 30)

    for i in range(start, len(df) - FWD_BARS):
        if i - last_i < COOL_DOWN:
            continue

        av = atr_s.iloc[i]
        rv = rsi_s.iloc[i]
        if np.isnan(av) or av == 0 or np.isnan(rv):
            continue

        pr = df["close"].iloc[i]
        if av / pr < MIN_ATR_PCT:       # coin is too flat
            flat_skips += 1
            continue

        lo20 = df["low"].iloc[i - LO_LOOKBACK: i].min()

        # ── Entry condition ──────────────────────────────────────────────────
        if not (RSI_LOW <= rv <= RSI_HIGH and pr <= lo20 * LO_MARGIN):
            continue

        tp_p = pr + av * TP_MULT
        sl_p = pr - av * SL_MULT

        # ── Forward outcome ──────────────────────────────────────────────────
        for j in range(i + 1, i + FWD_BARS + 1):
            hi = df["high"].iloc[j]
            lo = df["low"].iloc[j]
            if hi >= tp_p and lo <= sl_p:
                open_ms = int(df["ts"].iloc[j])
                close_ms = open_ms + _INTERVAL_MS.get(INTERVAL, 0)
                if j + 1 < len(df):
                    close_ms = int(df["ts"].iloc[j + 1])
                if _resolve_dual_hit(symbol, INTERVAL, open_ms, close_ms, tp_p, sl_p) == "win":
                    wins += 1
                else:
                    losses += 1
                last_i = i
                break
            if hi >= tp_p:
                wins   += 1
                last_i  = i
                break
            if lo <= sl_p:
                losses += 1
                last_i  = i
                break

    return wins, losses, wins + losses, flat_skips


def backtest(df: pd.DataFrame, atr_s: pd.Series, symbol: str) -> Tuple[Optional[float], int]:
    """
    Signal: RSI oversold (20–36) AND close ≤ 20-bar low × 1.025
    TP:     entry + ATR × TP_MULT      (8 ATR above)
    SL:     entry − ATR × SL_MULT      (1 ATR below)
    Outcome checked over the next FWD_BARS candles.

    Returns (win_rate, n_signals) or (None, 0) if too few signals.
    """
    wins, losses, total, _ = _backtest_detail(df, atr_s, symbol)
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


def _interval_hours(interval: str) -> float:
    """Approximate hours per candle for a given interval string."""
    mul = {"m": 1/60, "h": 1, "d": 24, "w": 168, "M": 720}
    try:
        return float(interval[:-1]) * mul.get(interval[-1], 1)
    except Exception:
        return 1.0


def bar(n: int, total: int, width: int = 46) -> str:
    filled = int(width * n / total)
    return "█" * filled + "░" * (width - filled)


# ── Order construction (Binance-valid) ────────────────────────────────────────

def _floor_step(v: Decimal, step: Decimal) -> Decimal:
    """Round quantity down to an exact step multiple."""
    if step <= 0:
        return v
    return v - (v % step)


def _ceil_tick(v: Decimal, tick: Decimal) -> Decimal:
    """Round a price up to an exact tick multiple."""
    if tick <= 0:
        return v
    rem = v % tick
    if rem == 0:
        return v
    return v + (tick - rem)


def build_order(price: float, atr: float, p: Dict) -> Optional[Dict]:
    """
    Quantize qty / TP / trigger / SL to Binance-valid values (KTD3) and
    validate them against the symbol filters (R3). Returns the executable
    order dict, or None when validation fails.
    """
    pr = Decimal(str(price))
    av = Decimal(str(atr))

    qty  = _floor_step(Decimal(str(BUDGET)) / pr, p["step"])
    tp   = _ceil_tick(pr + av * Decimal(str(TP_MULT)),  p["tick"])
    sl   = _floor_step(pr - av * Decimal(str(SL_MULT)), p["tick"])
    trig = _ceil_tick(sl + av * Decimal(str(TRIG_MULT)), p["tick"])

    if qty <= 0:
        return None
    if p["min_qty"] > 0 and qty < p["min_qty"]:
        return None
    if qty * pr < p["min_val"]:
        return None
    if qty * pr > Decimal(str(BUDGET)):
        return None
    if not (tp > pr > trig > sl):
        return None

    gain = (tp - pr) * qty
    loss = (pr - sl) * qty
    rr   = (tp - pr) / max(pr - sl, Decimal("1e-12"))

    return {
        "tp"    : float(tp),
        "sl"    : float(sl),
        "trig"  : float(trig),
        "qty"   : float(qty),
        "gain"  : float(gain),
        "loss"  : -float(loss),   # shown as negative
        "rr"    : float(rr),
    }


# ── Markdown results ─────────────────────────────────────────────────────────

def save_results_md(candidates: List[Dict], scan_started: datetime,
                    outdir: str = ".") -> str:
    """Save the completed scan results as a timestamped Markdown file."""
    path = Path(outdir) / f"binance_scan_{scan_started:%Y%m%d_%H%M%S}.md"

    lines = [
        "# Binance Spot Scanner Results",
        "",
        f"- **Scan time:** {scan_started:%Y-%m-%d %H:%M:%S}",
        f"- **Budget:** ${BUDGET:.2f}",
        "- **Quote pairs:** USDT / USDC",
        f"- **Win-rate window:** {MIN_WR*100:.2f}% – {MAX_WR*100:.2f}%",
        "- **Strategy:** RSI oversold bounce near 20-bar low",
        f"- **TP / SL:** {TP_MULT}×ATR / {SL_MULT}×ATR  "
        f"(SL trigger: SL + {TRIG_MULT}×ATR)",
        f"- **RSI window:** {RSI_LOW}–{RSI_HIGH}"
        f"   ·   Near {LO_LOOKBACK}-bar low × {LO_MARGIN}",
        f"- **Min ATR:** {MIN_ATR_PCT*100:.2f}% of price",
        f"- **Forward bars:** {FWD_BARS}   ·   **Cool-down:** {COOL_DOWN}",
        f"- **Min signals:** {MIN_SIGNALS}",
        f"- **Interval:** {INTERVAL}",
        f"- **Candles per symbol:** {N_CANDLES}",
        f"- **Pairs scanned:** up to {MAX_SCAN}",
        f"- **Minimum 24h volume:** ${MIN_VOL:,.0f}",
        "",
    ]

    if not candidates:
        lines += [
            "## Results",
            "",
            "No pairs matched the win-rate window at this moment.",
            "",
        ]
    else:
        lines += ["## Results", "", f"**{len(candidates)} candidate(s)**", ""]

        for i, c in enumerate(candidates, 1):
            pr  = c["price"]
            atp = c["atr"] / pr * 100
            ev  = c["wr"] * TP_MULT - (1 - c["wr"]) * SL_MULT

            lines += [
                f"### {i}. {c['symbol']} ({c['quote']} pair)",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| Current Price | {fmt(pr)} {c['quote']} |",
                f"| 24h Volume | {c['volume']:,.0f} {c['quote']} |",
                f"| 24h Change | {c['chg24']:+.2f}% |",
                f"| ATR ({INTERVAL}) | {fmt(c['atr'])} ({atp:.2f}%) |",
                f"| Win Rate | {c['wr']*100:.2f}% ({c['sigs']} signals) |",
                f"| R:R | {c['rr']:.1f}:1 |",
                f"| Expected Value / Risk | {ev:+.3f} per $1 risked |",
                "",
                "#### Trade Setup",
                "",
                "| Order | Price | Distance from Entry |",
                "|---|---:|---:|",
                f"| Entry | {fmt(pr)} {c['quote']} | — |",
                f"| TP | {fmt(c['tp'])} {c['quote']} | {pct(c['tp'], pr):+.2f}% |",
                f"| SL Trigger | {fmt(c['trig'])} {c['quote']} | {pct(c['trig'], pr):+.2f}% |",
                f"| SL Limit | {fmt(c['sl'])} {c['quote']} | {pct(c['sl'], pr):+.2f}% |",
                "",
                f"**Quantity:** {c['qty']:.6f} {c['base']}",
                "",
                f"- If TP hits: **{c['gain']:+.4f} {c['quote']}**",
                f"- If SL hits: **{c['loss']:+.4f} {c['quote']}**",
                "",
            ]

    lines += [
        "## Execution",
        "",
        "1. Spot buy at ENTRY (market or limit).",
        "2. Set OCO order: Limit sell @ TP | Stop-Limit: Trigger @ SL Trigger, Limit @ SL.",
        "",
        "> **Warning:** Backtest results do not guarantee future performance.",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log_path = init_logfile()
    scan_started = datetime.now()

    log.header([
        f"{BLD}BINANCE SPOT SCANNER  ·  ${BUDGET:.0f} Budget  ·  BNB Fees{RST}",
        "",
        f"  Budget        : ${BUDGET:.2f} USD",
        f"  Win Rate      : {MIN_WR*100:.2f}% – {MAX_WR*100:.2f}%"
        f"  (break-even @ {1/(1+TP_MULT/SL_MULT)*100:.2f}% for {TP_MULT:.0f}:1 R:R)",
        f"  TP mult       : {TP_MULT}×ATR     SL mult : {SL_MULT}×ATR"
        f"     Trigger : SL + {TRIG_MULT}×ATR",
        f"  Candle TF     : {INTERVAL}   ·   {N_CANDLES} bars   ·   "
        f"max scan: {MAX_SCAN} pairs",
        f"  RSI window    : {RSI_LOW}–{RSI_HIGH}"
        f"   ·   Near {LO_LOOKBACK}-bar low × {LO_MARGIN}"
        f"   ·   ATR ≥ {MIN_ATR_PCT*100:.2f}%",
        f"  Min signals   : {MIN_SIGNALS}"
        f"   ·   Forward bars: {FWD_BARS}"
        f"   ·   Cool-down: {COOL_DOWN} bars",
        f"  Min 24h vol   : {MIN_VOL:,.0f} USDT",
        f"  Log file      : {log_path}",
    ])

    # ── 1. Exchange-info filter ──────────────────────────────────────────────
    log.section("STEP 1 / 3  ·  EXCHANGE INFO FILTER")
    log.info("Fetching https://api.binance.com/api/v3/exchangeInfo …")

    data = GET(f"{BASE_URL}/api/v3/exchangeInfo")
    if not data:
        log.fail("Cannot reach Binance. Check your connection.")
        sys.exit(1)
    log.info(f"Total symbols in response      : {len(data['symbols']):>6,}")

    pairs, counts = exchange_filter_counts(data, log)
    log.raw()
    log.info(f"  Status != TRADING            : {counts['NOT_TRADING']:>6,}")
    log.info(f"  Wrong quote (not USDT/USDC)  : {counts['WRONG_QUOTE']:>6,}")
    log.info(f"  Spot not allowed             : {counts['NOT_SPOT']:>6,}")
    log.info(f"  Min notional > ${BUDGET:.2f}       : {counts['HIGH_NOTIONAL']:>6,}")
    log.ok(  f"  Eligible pairs               : {counts['PASSED']:>6,}")

    # ── 2. Ticker filter ─────────────────────────────────────────────────────
    log.section("STEP 2 / 3  ·  24 H TICKER FILTER")
    log.info("Fetching https://api.binance.com/api/v3/ticker/24hr …")

    pairs, tcounts = ticker_filter_counts(pairs, log)
    pairs.sort(key=lambda x: x["volume"], reverse=True)   # top volume first
    n_scan = min(len(pairs), MAX_SCAN)

    log.raw()
    log.info(f"  No ticker data               : {tcounts['NO_TICKER']:>6,}")
    log.info(f"  Zero / invalid price         : {tcounts['ZERO_PRICE']:>6,}")
    log.info(f"  Volume < {MIN_VOL/1e3:,.0f}k USDT           : {tcounts['LOW_VOL']:>6,}")
    log.info(f"  Qty unaffordable             : {tcounts['UNAFFORDABLE']:>6,}")
    log.ok(  f"  Pass → queued for scan       : {tcounts['PASSED']:>6,}")

    if n_scan == 0:
        log.fail("No pairs survived the filters. Exiting.")
        return

    # ── 3. Candle scan & backtest ────────────────────────────────────────────
    log.section(f"STEP 3 / 3  ·  BACKTEST SCAN  "
                f"({n_scan} pairs, sorted by 24h volume)")
    log.info(f"Strategy : RSI({RSI_LOW}–{RSI_HIGH}) oversold + "
             f"close ≤ {LO_LOOKBACK}-bar-low × {LO_MARGIN}")
    log.info(f"Entry    : above conditions met simultaneously")
    log.info(f"TP       : entry + {TP_MULT}×ATR(14)")
    log.info(f"SL       : entry − {SL_MULT}×ATR(14)")
    log.info(f"Win      : high reaches TP within {FWD_BARS} bars")
    log.info(f"Loss     : low reaches SL first")
    log.raw()

    candidates: List[Dict] = []

    for idx, p in enumerate(pairs[:n_scan], 1):
        sym   = p["symbol"]
        quote = p["quote"]
        price = p["price"]
        vol   = p["volume"]

        log.pair_start(idx, n_scan, sym, fmt(price), vol, quote)
        log.tree(False, "24h Volume", f"{vol:>18,.0f} {quote}")
        log.tree(False, "24h Change", f"{p['chg24']:>+18.2f}%")

        log.tree(False, "Candle fetch",
                 f"GET /klines  symbol={sym}  interval={INTERVAL}  limit={N_CANDLES}")

        df = fetch_candles(sym)
        if df is None:
            log.tree(True, "VERDICT",
                     f"{RED}REJECTED{RST}  ·  CANDLE_FETCH_FAIL  "
                     f"{DIM}API returned None / insufficient bars{RST}")
            time.sleep(0.06)
            continue

        n_bars = len(df)
        log.tree(False, "Candles",
                 f"{n_bars} bars fetched  "
                 f"({INTERVAL}, spanning ≈ {n_bars * _interval_hours(INTERVAL):.0f} h)",
                 "ok")

        atr_s = calc_atr(df)
        av    = atr_s.iloc[-1]
        if np.isnan(av) or av == 0:
            log.tree(True, "VERDICT",
                     f"{RED}REJECTED{RST}  ·  ATR_INVALID  "
                     f"{DIM}ATR(14)={'NaN' if np.isnan(av) else '0'}{RST}")
            time.sleep(0.06)
            continue

        atr_pct = av / price * 100
        if atr_pct < MIN_ATR_PCT * 100:
            log.tree(False, "ATR(14)",
                     f"{fmt(av)}  ({atr_pct:.3f}% of price)", "fail")
            log.tree(True, "VERDICT",
                     f"{RED}REJECTED{RST}  ·  ATR_TOO_FLAT  "
                     f"{DIM}{atr_pct:.3f}% < threshold {MIN_ATR_PCT*100:.2f}%  "
                     f"(coin too flat to trade){RST}")
            time.sleep(0.07)
            continue

        log.tree(False, "ATR(14)",
                 f"{fmt(av)}  ({atr_pct:.3f}% of price)  "
                 f"[threshold: ≥ {MIN_ATR_PCT*100:.2f}%]", "ok")

        log.tree(False, "Backtest",
                 f"RSI({RSI_LOW}–{RSI_HIGH}) oversold + near {LO_LOOKBACK}-bar low  "
                 f"→  TP={TP_MULT}×ATR / SL={SL_MULT}×ATR")

        wins, losses, total_sigs, flat_skips = _backtest_detail(df, atr_s, sym)

        log.tree(False, "Signals found",
                 f"total={total_sigs}  wins={wins}  losses={losses}  "
                 f"flat_candle_skips={flat_skips}")

        if total_sigs < MIN_SIGNALS:
            log.tree(True, "VERDICT",
                     f"{RED}REJECTED{RST}  ·  FEW_SIGNALS  "
                     f"{DIM}only {total_sigs} signals found, need ≥ {MIN_SIGNALS}{RST}")
            time.sleep(0.07)
            continue

        wr = wins / total_sigs

        in_range  = MIN_WR <= wr <= MAX_WR
        wr_color  = GRN if in_range else RED
        range_str = f"[{MIN_WR*100:.2f}%  –  {MAX_WR*100:.2f}%]"
        be_wr     = 1 / (1 + TP_MULT / SL_MULT)     # break-even win rate

        log.tree(False, "Win Rate",
                 f"{wr_color}{BLD}{wr*100:.2f}%{RST}  in {range_str}  "
                 f"{'✓ IN RANGE' if in_range else '✗ OUT OF RANGE'}  "
                 f"{DIM}(break-even: {be_wr*100:.2f}%){RST}",
                 "ok" if in_range else "fail")

        if wr < MIN_WR:
            log.tree(True, "VERDICT",
                     f"{RED}REJECTED{RST}  ·  WR_TOO_LOW  "
                     f"{DIM}{wr*100:.2f}% < {MIN_WR*100:.2f}%  "
                     f"({total_sigs} signals){RST}")
            time.sleep(0.08)
            continue

        if wr > MAX_WR:
            log.tree(True, "VERDICT",
                     f"{RED}REJECTED{RST}  ·  WR_TOO_HIGH  "
                     f"{DIM}{wr*100:.2f}% > {MAX_WR*100:.2f}%  "
                     f"({total_sigs} signals){RST}")
            time.sleep(0.08)
            continue

        order = build_order(price, av, p)
        if order is None:
            log.tree(False, "Order check",
                     f"{RED}invalid after quantization{RST}  "
                     f"{DIM}qty/tick/step/notional or OCO ordering{RST}", "fail")
            log.tree(True, "VERDICT",
                     f"{RED}REJECTED{RST}  ·  ORDER_INVALID  "
                     f"{DIM}executable levels violate Binance filters{RST}")
            time.sleep(0.08)
            continue

        ev = wr * TP_MULT - (1 - wr) * SL_MULT
        ev_color = GRN if ev > 0 else RED
        log.tree(False, "R : R",       f"{order['rr']:.1f} : 1")
        log.tree(False, "Exp. Value",  f"{ev_color}{ev:+.4f}{RST} per $1 risked  "
                                       f"({'positive EV ✓' if ev > 0 else 'negative EV ✗'})")
        log.tree(False, "TP",          f"{fmt(order['tp']):>14}  ({pct(order['tp'], price):>+7.2f}%)")
        log.tree(False, "SL Trigger",  f"{fmt(order['trig']):>14}  ({pct(order['trig'], price):>+7.2f}%)  "
                                       f"{DIM}← Binance stop price{RST}")
        log.tree(False, "SL",          f"{fmt(order['sl']):>14}  ({pct(order['sl'], price):>+7.2f}%)  "
                                       f"{DIM}← Binance limit price{RST}")
        log.tree(False, f"Qty (${BUDGET:.0f})",
                 f"{order['qty']:.6f} {p['base']}  →  "
                 f"TP profit: {order['gain']:+.4f} {quote}  /  "
                 f"SL loss: {order['loss']:+.4f} {quote}")

        log.tree(True, "VERDICT",
                 f"{GRN}{BLD}CANDIDATE ✓{RST}  {DIM}"
                 f"WR={wr*100:.2f}%  EV={ev:+.4f}  R:R={order['rr']:.1f}:1  "
                 f"signals={total_sigs}(W:{wins}/L:{losses}){RST}")

        candidates.append({
            **p,
            "atr"   : av,
            "wr"    : wr,
            "sigs"  : total_sigs,
            **order,
        })

        time.sleep(0.08)   # stay well under Binance rate limits

    # ── Results ──────────────────────────────────────────────────────────────
    SEP = "═" * 70

    log.section("CANDIDATES")

    if not candidates:
        log.fail("No pairs matched the win-rate window during this run.")
        log.info("Tip: the window is "
                 f"{MIN_WR*100:.2f}%–{MAX_WR*100:.2f}%; markets shift — re-run later.")
        log.raw()
        md_file = save_results_md(candidates, scan_started)
        log.info(f"Saved Markdown results → {md_file}")
        log.info(f"Full scan log saved → {log_path}")
        return

    # Sort: highest win rate first; break ties by volume
    candidates.sort(key=lambda x: (x["wr"], x["volume"]), reverse=True)

    for i, c in enumerate(candidates, 1):
        pr   = c["price"]
        q    = c["quote"]
        b    = c["base"]
        atp  = c["atr"] / pr * 100   # ATR as % of price
        ev   = c["wr"] * TP_MULT - (1 - c["wr"]) * SL_MULT
        ev_c = GRN if ev > 0 else RED

        log.raw(f"""
{SEP}
{BLD}  #{i}  {CYN}{c['symbol']}{RST}{BLD}  ·  {q} pair{RST}
  {"─"*66}
  Current Price    {fmt(pr):>20}  {q}
  24h Volume       {c['volume']:>20,.0f}  {q}
  24h Change       {c['chg24']:>+19.2f}%
  ATR ({INTERVAL})         {fmt(c['atr']):>20}  ({atp:.3f}%)
  Win Rate         {GRN}{BLD}{c['wr']*100:>19.2f}%{RST}   ({c['sigs']} signals)
  R : R            {c['rr']:>19.1f} : 1
  Exp. Value       {ev_c}{ev:>+19.4f}{RST}  (per $1 risked)

  ┌─ ENTRY    ──── {fmt(pr):>20}  {q}
  ├─ TP       ──── {GRN}{fmt(c['tp']):>20}{RST}  {q}   ({pct(c['tp'],  pr):>+7.2f}%)
  ├─ SL Trig  ──── {YLW}{fmt(c['trig']):>20}{RST}  {q}   ({pct(c['trig'], pr):>+7.2f}%)
  └─ SL       ──── {RED}{fmt(c['sl']):>20}{RST}  {q}   ({pct(c['sl'],  pr):>+7.2f}%)

  With ${BUDGET:.2f} budget:
    Quantity   {c['qty']:>20.6f}  {b}
    If TP hit  {c['gain']:>+20.4f}  {q}
    If SL hit  {c['loss']:>+20.4f}  {q}""")

    log.raw(SEP)
    log.raw()
    log.info("Binance OCO order:")
    log.info("  ① Spot-buy at ENTRY (market or limit)")
    log.info("  ② OCO sell:  Limit @ TP  |  Stop-Limit: stop=SL Trig, limit=SL")

    log.raw()
    md_file = save_results_md(candidates, scan_started)
    log.info(f"Saved Markdown results → {md_file}")
    log.info(f"Full scan log saved → {log_path}")
    log.raw()
    log.raw(f"{DIM}  ⚠  Backtest ≠ live performance.  "
            f"Only trade what you can afford to lose.{RST}")
    log.raw()


if __name__ == "__main__":
    main()
