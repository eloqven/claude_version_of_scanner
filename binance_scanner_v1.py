#!/usr/bin/env python3
"""
Binance Spot Scanner — Verbose + DB Edition
────────────────────────────────────────────────────────────────────────────────
Every decision for every pair is logged in full detail.
All runs and per-pair results are persisted to SQLite.
All key parameters are overridable via CLI on every run.

Quick start:
  pip install requests pandas numpy
  python binance_scanner.py

Usage examples:
  python binance_scanner.py                                # defaults
  python binance_scanner.py --budget 25 --min-wr 8.5 --max-wr 16
  python binance_scanner.py --budget 50 --interval 1h --max-scan 300
  python binance_scanner.py --tp-mult 10 --sl-mult 1.5 --min-signals 12
  python binance_scanner.py --db ./data/scans.db --log-file run.log
  python binance_scanner.py --history                      # show past runs
────────────────────────────────────────────────────────────────────────────────
"""

import argparse
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
    import pandas as pd
    import requests
except ImportError as exc:
    print(f"\n[ERROR] Missing dependency: {exc}")
    print("        Run:  pip install requests pandas numpy\n")
    sys.exit(1)


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


# ══════════════════════════════════════════════════════════════════════════════
#  ANSI palette
# ══════════════════════════════════════════════════════════════════════════════
GRN = "\033[92m";  RED = "\033[91m";  YLW = "\033[93m"
CYN = "\033[96m";  DIM = "\033[2m";   BLD = "\033[1m";  RST = "\033[0m"
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


# ══════════════════════════════════════════════════════════════════════════════
#  Config dataclass  (one source of truth for every tunable parameter)
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class Config:
    # ── Core ─────────────────────────────────────────────────
    budget:      float = 10.0
    min_wr:      float = 0.0987   # stored as fraction; CLI accepts %
    max_wr:      float = 0.1440
    tp_mult:     float = 8.0
    sl_mult:     float = 1.0
    trig_mult:   float = 0.15
    # ── Market ───────────────────────────────────────────────
    min_vol:     float = 300_000
    max_scan:    int   = 200
    interval:    str   = "4h"
    n_candles:   int   = 500
    # ── Backtest ─────────────────────────────────────────────
    rsi_low:     int   = 20
    rsi_high:    int   = 36
    lo_lookback: int   = 20
    lo_margin:   float = 1.025
    min_atr_pct: float = 0.004    # stored as fraction; CLI accepts %
    fwd_bars:    int   = 72
    cool_down:   int   = 5
    min_signals: int   = 8
    # ── I/O ──────────────────────────────────────────────────
    db_path:     str   = "scanner.db"
    log_file:    str   = ""


# ══════════════════════════════════════════════════════════════════════════════
#  Logger  (dual output: colored console + plain file)
# ══════════════════════════════════════════════════════════════════════════════
class Logger:
    def __init__(self, path: str = "") -> None:
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "w", encoding="utf-8") if path else None

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _emit(self, line: str) -> None:
        print(line)
        if self._fh:
            self._fh.write(_ANSI_RE.sub("", line) + "\n")
            self._fh.flush()

    # ── Generic levels ────────────────────────────────────────
    def raw(self, s: str = "")  -> None: self._emit(s)
    def info(self, msg: str)    -> None: self._emit(f"{DIM}[{self._ts()}] INFO{RST}  {msg}")
    def ok(self, msg: str)      -> None: self._emit(f"{GRN}[{self._ts()}] PASS{RST}  {msg}")
    def skip(self, msg: str)    -> None: self._emit(f"{YLW}[{self._ts()}] SKIP{RST}  {msg}")
    def fail(self, msg: str)    -> None: self._emit(f"{RED}[{self._ts()}] FAIL{RST}  {msg}")
    def cand(self, msg: str)    -> None: self._emit(f"{GRN}{BLD}[{self._ts()}] CAND{RST}  {msg}")
    def warn(self, msg: str)    -> None: self._emit(f"{YLW}[{self._ts()}] WARN{RST}  {msg}")
    def dblog(self, msg: str)   -> None: self._emit(f"{DIM}[{self._ts()}]  DB  {RST}  {msg}")

    # ── Structural ────────────────────────────────────────────
    def section(self, title: str) -> None:
        bar = "─" * 70
        self._emit(f"\n{CYN}{BLD}{bar}{RST}")
        self._emit(f"{CYN}{BLD}  {title}{RST}")
        self._emit(f"{CYN}{BLD}{bar}{RST}")

    def header(self, rows: List[str]) -> None:
        bar = "═" * 70
        self._emit(f"\n{BLD}{bar}{RST}")
        for r in rows:
            self._emit(f"  {r}")
        self._emit(f"{BLD}{bar}{RST}")

    # ── Per-pair tree ─────────────────────────────────────────
    def pair_start(self, idx: int, total: int, sym: str,
                   price: str, vol: float, q: str) -> None:
        self._emit(
            f"\n{BLD}[{self._ts()}] PAIR  [{idx:>3}/{total}]  "
            f"{CYN}{sym:<16}{RST}{BLD}  price={price}  "
            f"vol={vol:,.0f} {q}{RST}"
        )

    def tree(self, last: bool, label: str, val: str, st: str = "") -> None:
        """Single tree row.  st: 'ok' | 'fail' | 'warn' | ''"""
        pfx = "└─" if last else "├─"
        badge = {"ok": f" {GRN}✓{RST}", "fail": f" {RED}✗{RST}",
                 "warn": f" {YLW}!{RST}"}.get(st, "")
        self._emit(f"               {pfx} {BLD}{label:<16}{RST}{val}{badge}")

    def tree_block(self, last: bool, label: str, lines: List[str]) -> None:
        """Multi-line tree entry (first line has prefix, rest indented)."""
        pfx = "└─" if last else "├─"
        self._emit(f"               {pfx} {BLD}{label:<16}{RST}{lines[0]}")
        for l in lines[1:]:
            self._emit(f"                                {l}")

    def close(self) -> None:
        if self._fh:
            self._fh.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Formatting helpers
# ══════════════════════════════════════════════════════════════════════════════
def fmt(v: float) -> str:
    """Smart price formatter — auto decimal places."""
    if v == 0:      return "0"
    if v >= 1_000:  return f"{v:,.2f}"
    if v >= 1:      return f"{v:.4f}"
    if v >= 0.01:   return f"{v:.6f}"
    return f"{v:.8f}"

def pct(a: float, ref: float) -> float:
    return (a - ref) / ref * 100


# ══════════════════════════════════════════════════════════════════════════════
#  SQLite database
# ══════════════════════════════════════════════════════════════════════════════
_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at           TEXT    NOT NULL,
    -- parameters saved verbatim so every run is fully reproducible
    budget           REAL    NOT NULL,
    min_wr           REAL    NOT NULL,
    max_wr           REAL    NOT NULL,
    tp_mult          REAL    NOT NULL,
    sl_mult          REAL    NOT NULL,
    trig_mult        REAL    NOT NULL,
    interval         TEXT    NOT NULL,
    n_candles        INTEGER NOT NULL,
    min_vol          REAL    NOT NULL,
    max_scan         INTEGER NOT NULL,
    rsi_low          INTEGER NOT NULL,
    rsi_high         INTEGER NOT NULL,
    lo_lookback      INTEGER NOT NULL,
    lo_margin        REAL    NOT NULL,
    min_atr_pct      REAL    NOT NULL,
    fwd_bars         INTEGER NOT NULL,
    cool_down        INTEGER NOT NULL,
    min_signals      INTEGER NOT NULL,
    -- results (updated when run finishes)
    n_eligible       INTEGER,
    n_after_ticker   INTEGER,
    n_scanned        INTEGER,
    n_candidates     INTEGER,
    duration_s       REAL,
    log_file         TEXT
);

CREATE TABLE IF NOT EXISTS pair_scans (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES scan_runs(id),
    vol_rank         INTEGER,          -- position in top-N-by-volume list
    symbol           TEXT    NOT NULL,
    base             TEXT    NOT NULL,
    quote            TEXT    NOT NULL,
    price            REAL,
    volume_24h       REAL,
    change_24h       REAL,
    -- technical
    atr              REAL,
    atr_pct          REAL,
    -- backtest
    win_rate         REAL,
    n_signals        INTEGER,
    wins             INTEGER,
    losses           INTEGER,
    flat_skips       INTEGER,
    -- levels
    tp               REAL,
    sl               REAL,
    sl_trigger       REAL,
    qty              REAL,
    pot_gain         REAL,
    pot_loss         REAL,
    rr_ratio         REAL,
    ev_per_risk      REAL,
    -- verdict
    status           TEXT    NOT NULL,   -- CANDIDATE | REJECTED
    reason           TEXT    NOT NULL,   -- rejection code or WR_IN_RANGE
    reason_detail    TEXT,
    scanned_at       TEXT    NOT NULL
);
"""

def db_open(path: str, log: Logger) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    log.dblog(f"Database ready → {Path(path).resolve()}")
    return conn


def db_insert_run(conn: sqlite3.Connection, cfg: Config) -> int:
    cur = conn.execute(
        """INSERT INTO scan_runs
           (run_at, budget, min_wr, max_wr, tp_mult, sl_mult, trig_mult,
            interval, n_candles, min_vol, max_scan,
            rsi_low, rsi_high, lo_lookback, lo_margin, min_atr_pct,
            fwd_bars, cool_down, min_signals, log_file)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now().isoformat(timespec="seconds"),
            cfg.budget, cfg.min_wr, cfg.max_wr,
            cfg.tp_mult, cfg.sl_mult, cfg.trig_mult,
            cfg.interval, cfg.n_candles, cfg.min_vol, cfg.max_scan,
            cfg.rsi_low, cfg.rsi_high, cfg.lo_lookback, cfg.lo_margin,
            cfg.min_atr_pct, cfg.fwd_bars, cfg.cool_down, cfg.min_signals,
            cfg.log_file or None,
        )
    )
    conn.commit()
    return cur.lastrowid


def db_finalise_run(conn: sqlite3.Connection, run_id: int,
                    n_eligible: int, n_ticker: int,
                    n_scanned: int, n_cand: int, duration: float) -> None:
    conn.execute(
        """UPDATE scan_runs
           SET n_eligible=?, n_after_ticker=?, n_scanned=?,
               n_candidates=?, duration_s=?
           WHERE id=?""",
        (n_eligible, n_ticker, n_scanned, n_cand, round(duration, 2), run_id)
    )
    conn.commit()


def db_insert_pair(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    cols = ", ".join(row.keys())
    qs   = ", ".join("?" * len(row))
    conn.execute(f"INSERT INTO pair_scans ({cols}) VALUES ({qs})", list(row.values()))
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
#  Binance REST helpers
# ══════════════════════════════════════════════════════════════════════════════
BASE_URL = "https://api.binance.com"


def _get(url: str, params: Optional[Dict] = None, retries: int = 3) -> Optional[Any]:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=12)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30))
                print(f"\n{YLW}  [rate-limit] waiting {wait}s …{RST}", flush=True)
                time.sleep(wait)
                continue
            if r.status_code == 418:       # IP temporarily banned
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


# ══════════════════════════════════════════════════════════════════════════════
#  Step 1 — Exchange-info filter
# ══════════════════════════════════════════════════════════════════════════════
def step_exchange(cfg: Config, log: Logger) -> List[Dict]:
    """
    Pull exchangeInfo, apply structural filters, log per-reason counts.
    Returns the list of eligible pairs (dicts).
    """
    log.section("STEP 1 / 3  ·  EXCHANGE INFO FILTER")
    log.info("Fetching https://api.binance.com/api/v3/exchangeInfo …")

    data = _get(f"{BASE_URL}/api/v3/exchangeInfo")
    if not data:
        log.fail("Cannot reach Binance. Check your connection.")
        sys.exit(1)

    all_syms = data["symbols"]
    log.info(f"Total symbols in response      : {len(all_syms):>6,}")

    counts: Dict[str, int] = dict(
        NOT_TRADING=0, WRONG_QUOTE=0, NOT_SPOT=0,
        HIGH_NOTIONAL=0, PASSED=0
    )

    pairs: List[Dict] = []
    for s in all_syms:
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

        fmap    = {f["filterType"]: f for f in s["filters"]}
        nf      = fmap.get("NOTIONAL") or fmap.get("MIN_NOTIONAL") or {}
        min_val = float(nf.get("minNotional", 1.0))
        if min_val > cfg.budget:
            counts["HIGH_NOTIONAL"] += 1
            log.skip(f"{sym:<18} HIGH_MIN_NOTIONAL  minNotional={min_val:.2f} > budget={cfg.budget:.2f}")
            continue

        lot_f  = fmap.get("LOT_SIZE", {})
        tick_f = fmap.get("PRICE_FILTER", {})
        counts["PASSED"] += 1
        pairs.append(dict(
            symbol  = sym,
            base    = s["baseAsset"],
            quote   = s["quoteAsset"],
            min_val = min_val,
            step    = float(lot_f.get("stepSize",  "0.01")),
            tick    = float(tick_f.get("tickSize", "0.0001")),
        ))

    # Summary
    log.raw()
    log.info(f"  Status != TRADING            : {counts['NOT_TRADING']:>6,}")
    log.info(f"  Wrong quote (not USDT/USDC)  : {counts['WRONG_QUOTE']:>6,}")
    log.info(f"  Spot not allowed             : {counts['NOT_SPOT']:>6,}")
    log.info(f"  Min notional > ${cfg.budget:.2f}       : {counts['HIGH_NOTIONAL']:>6,}")
    log.ok(  f"  Eligible pairs               : {counts['PASSED']:>6,}")

    return pairs


# ══════════════════════════════════════════════════════════════════════════════
#  Step 2 — 24 h Ticker filter
# ══════════════════════════════════════════════════════════════════════════════
def step_ticker(pairs: List[Dict], cfg: Config, log: Logger) -> List[Dict]:
    """
    Bulk-fetch 24 h tickers. For every eligible pair log the exact reason
    it passes or fails. Returns passing pairs sorted by volume desc.
    """
    log.section("STEP 2 / 3  ·  24 H TICKER FILTER")
    log.info("Fetching https://api.binance.com/api/v3/ticker/24hr …")

    data = _get(f"{BASE_URL}/api/v3/ticker/24hr")
    if not data:
        log.fail("Ticker fetch failed after retries — aborting scan.")
        sys.exit(1)

    tmap = {t["symbol"]: t for t in data}

    counts: Dict[str, int] = dict(
        NO_TICKER=0, ZERO_PRICE=0, LOW_VOL=0,
        UNAFFORDABLE=0, PASSED=0
    )
    out: List[Dict] = []

    for p in pairs:
        sym = p["symbol"]
        t   = tmap.get(sym)

        if t is None:
            log.skip(f"{sym:<18} NO_TICKER       — symbol absent from 24 h ticker")
            counts["NO_TICKER"] += 1
            continue

        price = float(t["lastPrice"])
        vol   = float(t["quoteVolume"])
        chg   = float(t["priceChangePercent"])

        if price <= 0:
            log.skip(f"{sym:<18} ZERO_PRICE      — lastPrice={price}")
            counts["ZERO_PRICE"] += 1
            continue

        if vol < cfg.min_vol:
            log.skip(
                f"{sym:<18} LOW_VOLUME      — "
                f"vol={vol:>14,.0f}  (need ≥ {cfg.min_vol:,.0f} {p['quote']})"
            )
            counts["LOW_VOL"] += 1
            continue

        min_qty = cfg.budget / price
        if min_qty < 1e-6:
            log.skip(
                f"{sym:<18} UNAFFORDABLE    — "
                f"price={fmt(price)}  budget/price={min_qty:.2e}"
            )
            counts["UNAFFORDABLE"] += 1
            continue

        log.ok(
            f"{sym:<18} PASS            — "
            f"price={fmt(price):>12}  vol={vol:>16,.0f}  chg={chg:>+7.2f}%"
        )
        counts["PASSED"] += 1
        out.append({**p, "price": price, "volume": vol, "chg24": chg})

    log.raw()
    quote_label = pairs[0]["quote"] if pairs else "USDT"
    log.info(f"  No ticker data               : {counts['NO_TICKER']:>6,}")
    log.info(f"  Zero / invalid price         : {counts['ZERO_PRICE']:>6,}")
    log.info(f"  Volume < {cfg.min_vol/1e3:,.0f}k {quote_label:4}          : {counts['LOW_VOL']:>6,}")
    log.info(f"  Qty unaffordable             : {counts['UNAFFORDABLE']:>6,}")
    log.ok(  f"  Pass → queued for scan       : {counts['PASSED']:>6,}")

    out.sort(key=lambda x: x["volume"], reverse=True)
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Indicators
# ══════════════════════════════════════════════════════════════════════════════
def _candles(sym: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    raw = _get(f"{BASE_URL}/api/v3/klines",
               {"symbol": sym, "interval": interval, "limit": limit})
    if not raw:
        return None
    df = pd.DataFrame(raw, columns=[
        "ts","open","high","low","close","vol",
        "cts","qvol","n","tbv","tqv","_"
    ])
    df[["open","high","low","close","vol","qvol"]] = (
        df[["open","high","low","close","vol","qvol"]].astype(float)
    )
    return df


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d  = close.diff()
    g  = d.clip(lower=0).rolling(n).mean()
    ls = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / ls.replace(0, np.nan))


# ══════════════════════════════════════════════════════════════════════════════
#  Backtest
# ══════════════════════════════════════════════════════════════════════════════
def _backtest(df: pd.DataFrame, atr_s: pd.Series, cfg: Config) -> Tuple[int, int, int, int]:
    """
    Strategy: RSI oversold + price near 20-bar low → high R:R long.
    Returns (wins, losses, total_signals, flat_candle_skips).
    """
    rsi_s      = _rsi(df["close"])
    wins = losses = flat_skips = 0
    last_i = -(cfg.cool_down + 1)
    start  = max(cfg.lo_lookback + 14 + 1, 30)

    for i in range(start, len(df) - 1):
        if i - last_i < cfg.cool_down:
            continue

        av = atr_s.iloc[i]
        rv = rsi_s.iloc[i]

        if np.isnan(av) or av == 0 or np.isnan(rv):
            continue

        pr = df["close"].iloc[i]

        # Skip flat candles
        if av / pr < cfg.min_atr_pct:
            flat_skips += 1
            continue

        lo20 = df["low"].iloc[i - cfg.lo_lookback: i].min()

        # Entry condition
        if not (cfg.rsi_low <= rv <= cfg.rsi_high and pr <= lo20 * cfg.lo_margin):
            continue

        tp_p = pr + av * cfg.tp_mult
        sl_p = pr - av * cfg.sl_mult

        # Forward outcome
        for j in range(i + 1, min(i + cfg.fwd_bars + 1, len(df))):
            if df["high"].iloc[j] >= tp_p:
                wins   += 1
                last_i  = i
                break
            if df["low"].iloc[j] <= sl_p:
                losses += 1
                last_i  = i
                break

    return wins, losses, wins + losses, flat_skips


# ══════════════════════════════════════════════════════════════════════════════
#  Step 3 — per-pair scan  (the verbose heart of the scanner)
# ══════════════════════════════════════════════════════════════════════════════
def scan_pair(p: Dict, idx: int, total: int, run_id: int,
              cfg: Config, log: Logger, conn: sqlite3.Connection) -> bool:
    """
    Full pipeline for one pair: candles → ATR → backtest → decision.
    Logs every checkpoint. Writes result to DB.
    Returns True if pair is a CANDIDATE.
    """
    sym   = p["symbol"]
    base  = p["base"]
    quote = p["quote"]
    price = p["price"]
    vol   = p["volume"]
    chg24 = p["chg24"]

    # Shared DB row — filled incrementally
    row: Dict[str, Any] = dict(
        run_id=run_id, vol_rank=idx,
        symbol=sym, base=base, quote=quote,
        price=price, volume_24h=vol, change_24h=chg24,
        atr=None, atr_pct=None,
        win_rate=None, n_signals=None, wins=None, losses=None, flat_skips=None,
        tp=None, sl=None, sl_trigger=None,
        qty=None, pot_gain=None, pot_loss=None,
        rr_ratio=None, ev_per_risk=None,
        status="REJECTED", reason="UNKNOWN", reason_detail="",
        scanned_at=datetime.now().isoformat(timespec="seconds"),
    )

    def _save(status: str, reason: str, detail: str = "") -> bool:
        row["status"]        = status
        row["reason"]        = reason
        row["reason_detail"] = detail
        db_insert_pair(conn, row)
        return status == "CANDIDATE"

    # ── Pair header ───────────────────────────────────────────────────────────
    log.pair_start(idx, total, sym, fmt(price), vol, quote)
    log.tree(False, "24h Volume", f"{vol:>18,.0f} {quote}")
    log.tree(False, "24h Change", f"{chg24:>+18.2f}%")

    # ── 1. Candle fetch ───────────────────────────────────────────────────────
    log.tree(False, "Candle fetch",
             f"GET /klines  symbol={sym}  interval={cfg.interval}  limit={cfg.n_candles}")

    df = _candles(sym, cfg.interval, cfg.n_candles)

    if df is None:
        log.tree(True, "VERDICT",
                 f"{RED}REJECTED{RST}  ·  CANDLE_FETCH_FAIL  "
                 f"{DIM}API returned None (timeout / unknown symbol){RST}")
        return _save("REJECTED", "CANDLE_FETCH_FAIL", "API returned None")

    n_bars = len(df)
    if n_bars < 60:
        log.tree(True, "VERDICT",
                 f"{RED}REJECTED{RST}  ·  INSUFFICIENT_CANDLES  "
                 f"{DIM}got {n_bars} bars, need ≥ 60{RST}")
        return _save("REJECTED", "INSUFFICIENT_CANDLES", f"got {n_bars} bars")

    log.tree(False, "Candles",
             f"{n_bars} bars fetched  "
             f"({cfg.interval}, spanning ≈ {n_bars * _interval_hours(cfg.interval):.0f} h)",
             "ok")

    # ── 2. ATR ────────────────────────────────────────────────────────────────
    atr_s = _atr(df)
    av    = atr_s.iloc[-1]

    if np.isnan(av) or av == 0:
        log.tree(True, "VERDICT",
                 f"{RED}REJECTED{RST}  ·  ATR_INVALID  "
                 f"{DIM}ATR(14)={'NaN' if np.isnan(av) else '0'}{RST}")
        return _save("REJECTED", "ATR_INVALID", f"ATR={av}")

    atr_pct = av / price * 100
    row["atr"]     = round(float(av), 10)
    row["atr_pct"] = round(atr_pct, 4)

    if atr_pct < cfg.min_atr_pct * 100:
        log.tree(False, "ATR(14)",
                 f"{fmt(av)}  ({atr_pct:.3f}% of price)", "fail")
        log.tree(True, "VERDICT",
                 f"{RED}REJECTED{RST}  ·  ATR_TOO_FLAT  "
                 f"{DIM}{atr_pct:.3f}% < threshold {cfg.min_atr_pct*100:.2f}%  "
                 f"(coin too flat to trade){RST}")
        return _save("REJECTED", "ATR_TOO_FLAT",
                     f"ATR={fmt(av)} ({atr_pct:.3f}%) < {cfg.min_atr_pct*100:.2f}%")

    log.tree(False, "ATR(14)",
             f"{fmt(av)}  ({atr_pct:.3f}% of price)  "
             f"[threshold: ≥ {cfg.min_atr_pct*100:.2f}%]", "ok")

    # ── 3. Backtest ───────────────────────────────────────────────────────────
    log.tree(False, "Backtest",
             f"RSI({cfg.rsi_low}–{cfg.rsi_high}) oversold + near {cfg.lo_lookback}-bar low  "
             f"→  TP={cfg.tp_mult}×ATR / SL={cfg.sl_mult}×ATR")

    wins, losses, total_sigs, flat_skips = _backtest(df, atr_s, cfg)

    row["wins"]       = wins
    row["losses"]     = losses
    row["flat_skips"] = flat_skips

    log.tree(False, "Signals found",
             f"total={total_sigs}  wins={wins}  losses={losses}  "
             f"flat_candle_skips={flat_skips}")

    if total_sigs < cfg.min_signals:
        log.tree(True, "VERDICT",
                 f"{RED}REJECTED{RST}  ·  FEW_SIGNALS  "
                 f"{DIM}only {total_sigs} signals found, need ≥ {cfg.min_signals}  "
                 f"(consider --min-signals or wider RSI window){RST}")
        return _save("REJECTED", "FEW_SIGNALS",
                     f"got {total_sigs} signals (need ≥ {cfg.min_signals})")

    row["n_signals"] = total_sigs
    wr = wins / total_sigs
    row["win_rate"] = round(wr, 6)

    wr_pct     = wr * 100
    in_range   = cfg.min_wr <= wr <= cfg.max_wr
    wr_color   = GRN if in_range else RED
    range_str  = f"[{cfg.min_wr*100:.2f}%  –  {cfg.max_wr*100:.2f}%]"
    be_wr      = 1 / (1 + cfg.tp_mult / cfg.sl_mult)     # break-even win rate

    log.tree(False, "Win Rate",
             f"{wr_color}{BLD}{wr_pct:.2f}%{RST}  in {range_str}  "
             f"{'✓ IN RANGE' if in_range else '✗ OUT OF RANGE'}  "
             f"{DIM}(break-even: {be_wr*100:.2f}%){RST}",
             "ok" if in_range else "fail")

    if wr < cfg.min_wr:
        log.tree(True, "VERDICT",
                 f"{RED}REJECTED{RST}  ·  WR_TOO_LOW  "
                 f"{DIM}{wr_pct:.2f}% < {cfg.min_wr*100:.2f}%  "
                 f"({total_sigs} signals){RST}")
        return _save("REJECTED", "WR_TOO_LOW",
                     f"WR={wr_pct:.2f}% < {cfg.min_wr*100:.2f}% ({total_sigs} signals)")

    if wr > cfg.max_wr:
        log.tree(True, "VERDICT",
                 f"{RED}REJECTED{RST}  ·  WR_TOO_HIGH  "
                 f"{DIM}{wr_pct:.2f}% > {cfg.max_wr*100:.2f}%  "
                 f"({total_sigs} signals){RST}")
        return _save("REJECTED", "WR_TOO_HIGH",
                     f"WR={wr_pct:.2f}% > {cfg.max_wr*100:.2f}% ({total_sigs} signals)")

    # ── 4. Compute levels ─────────────────────────────────────────────────────
    tp   = price + av * cfg.tp_mult
    sl   = price - av * cfg.sl_mult
    trig = sl    + av * cfg.trig_mult
    qty  = cfg.budget / price
    gain =  (tp - price) * qty
    loss = -(price - sl) * qty
    rr   =  (tp - price) / max(price - sl, 1e-12)
    ev   =  wr * cfg.tp_mult - (1 - wr) * cfg.sl_mult

    row.update(dict(
        tp=round(tp, 10), sl=round(sl, 10), sl_trigger=round(trig, 10),
        qty=round(qty, 8), pot_gain=round(gain, 6), pot_loss=round(loss, 6),
        rr_ratio=round(rr, 4), ev_per_risk=round(ev, 6),
    ))

    ev_color = GRN if ev > 0 else RED
    log.tree(False, "R : R",       f"{rr:.1f} : 1")
    log.tree(False, "Exp. Value",  f"{ev_color}{ev:+.4f}{RST} per $1 risked  "
                                   f"({'positive EV ✓' if ev > 0 else 'negative EV ✗'})")
    log.tree(False, "TP",          f"{fmt(tp):>14}  ({pct(tp, price):>+7.2f}%)")
    log.tree(False, "SL Trigger",  f"{fmt(trig):>14}  ({pct(trig, price):>+7.2f}%)  "
                                   f"{DIM}← Binance stop price{RST}")
    log.tree(False, "SL",          f"{fmt(sl):>14}  ({pct(sl, price):>+7.2f}%)  "
                                   f"{DIM}← Binance limit price{RST}")

    qty_str = f"{qty:.6f} {base}"
    log.tree(False, f"Qty (${cfg.budget:.0f})",
             f"{qty_str}  →  TP profit: {gain:+.4f} {quote}  /  SL loss: {loss:+.4f} {quote}")

    detail = (f"WR={wr_pct:.2f}%  EV={ev:+.4f}  R:R={rr:.1f}:1  "
              f"signals={total_sigs}(W:{wins}/L:{losses})")

    log.tree(True, "VERDICT",
             f"{GRN}{BLD}CANDIDATE ✓{RST}  {DIM}{detail}{RST}")

    return _save("CANDIDATE", "WR_IN_RANGE", detail)


def _interval_hours(interval: str) -> float:
    """Approximate hours per candle for a given interval string."""
    mul = {"m": 1/60, "h": 1, "d": 24, "w": 168, "M": 720}
    try:
        return float(interval[:-1]) * mul.get(interval[-1], 1)
    except Exception:
        return 1.0


# ══════════════════════════════════════════════════════════════════════════════
#  Results display
# ══════════════════════════════════════════════════════════════════════════════
def display_results(conn: sqlite3.Connection, run_id: int,
                    cfg: Config, log: Logger) -> None:
    cur  = conn.execute(
        """SELECT * FROM pair_scans
           WHERE run_id=? AND status='CANDIDATE'
           ORDER BY win_rate DESC""",
        (run_id,)
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    log.section("CANDIDATES")

    if not rows:
        log.fail("No pairs matched the win-rate window during this run.")
        log.info("Tip: widen --min-wr / --max-wr, lower --min-vol, or re-run later.")
        return

    SEP = "═" * 70

    for i, r in enumerate(rows, 1):
        pr    = r["price"]
        q     = r["quote"]
        b     = r["base"]
        wr    = r["win_rate"]
        ev    = r["ev_per_risk"]
        ev_c  = GRN if ev and ev > 0 else RED

        log.raw(f"""
{SEP}
{BLD}  #{i}  {CYN}{r['symbol']}{RST}{BLD}  ·  {q} pair{RST}
  {"─"*66}
  Current Price    {fmt(pr):>20}  {q}
  24h Volume       {r['volume_24h']:>20,.0f}  {q}
  24h Change       {r['change_24h']:>+19.2f}%
  ATR ({cfg.interval})         {fmt(r['atr']):>20}  ({r['atr_pct']:.3f}%)
  Win Rate         {GRN}{BLD}{wr*100:>19.2f}%{RST}   ({r['n_signals']} signals  W:{r['wins']} / L:{r['losses']})
  R : R            {r['rr_ratio']:>19.1f} : 1
  Exp. Value       {ev_c}{ev:>+19.4f}{RST}  (per $1 risked)

  ┌─ ENTRY    ──── {fmt(pr):>20}  {q}
  ├─ TP       ──── {GRN}{fmt(r['tp']):>20}{RST}  {q}   ({pct(r['tp'],  pr):>+7.2f}%)
  ├─ SL Trig  ──── {YLW}{fmt(r['sl_trigger']):>20}{RST}  {q}   ({pct(r['sl_trigger'], pr):>+7.2f}%)
  └─ SL       ──── {RED}{fmt(r['sl']):>20}{RST}  {q}   ({pct(r['sl'],  pr):>+7.2f}%)

  With ${cfg.budget:.2f} budget:
    Quantity   {r['qty']:>20.6f}  {b}
    If TP hit  {r['pot_gain']:>+20.4f}  {q}
    If SL hit  {r['pot_loss']:>+20.4f}  {q}""")

    log.raw(SEP)
    log.raw()
    log.info("Binance OCO order:")
    log.info("  ① Spot-buy at ENTRY (market or limit)")
    log.info("  ② OCO sell:  Limit @ TP  |  Stop-Limit: stop=SL Trig, limit=SL")


# ══════════════════════════════════════════════════════════════════════════════
#  Run history
# ══════════════════════════════════════════════════════════════════════════════
def show_history(db_path: str) -> None:
    if not Path(db_path).exists():
        print(f"[INFO] No database found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    runs = conn.execute(
        "SELECT * FROM scan_runs ORDER BY id DESC LIMIT 30"
    ).fetchall()

    if not runs:
        print("[INFO] Database exists but no runs recorded yet.")
        return

    SEP = "═" * 82
    print(f"\n{BLD}{SEP}{RST}")
    print(f"  SCAN HISTORY  ·  {db_path}")
    print(f"{BLD}{SEP}{RST}")
    print(f"  {'#':>4}  {'Date / Time':<20}  {'Budget':>8}  "
          f"{'WR min':>7}  {'WR max':>7}  {'R:R':>5}  "
          f"{'Scanned':>8}  {'Cands':>6}  {'Time':>6}")
    print(f"  {'─'*78}")

    for run in runs:
        r = dict(run)
        print(
            f"  {r['id']:>4}  {r['run_at']:<20}  "
            f"${r['budget']:>7.2f}  "
            f"{r['min_wr']*100:>6.2f}%  {r['max_wr']*100:>6.2f}%  "
            f"{r['tp_mult']:.0f}:1  "
            f"{r.get('n_scanned') or 0:>8,}  "
            f"{r.get('n_candidates') or 0:>6}  "
            f"{r.get('duration_s') or 0:>5.1f}s"
        )

    # Recent candidates across all runs
    cands = conn.execute(
        """SELECT ps.symbol, ps.quote, ps.win_rate, ps.ev_per_risk,
                  ps.rr_ratio, ps.n_signals, ps.scanned_at, sr.id as run_id
           FROM pair_scans ps
           JOIN scan_runs sr ON ps.run_id = sr.id
           WHERE ps.status = 'CANDIDATE'
           ORDER BY ps.scanned_at DESC
           LIMIT 20"""
    ).fetchall()

    if cands:
        print(f"\n{BLD}{SEP}{RST}")
        print(f"  RECENT CANDIDATES (last 20 across all runs)")
        print(f"{BLD}{SEP}{RST}")
        print(f"  {'Run':>4}  {'Symbol':<16}  {'Quote':<6}  "
              f"{'WR':>7}  {'EV/risk':>8}  {'R:R':>5}  {'Sigs':>5}  {'Scanned At'}")
        print(f"  {'─'*76}")
        for c in cands:
            r = dict(c)
            ev_c = GRN if r['ev_per_risk'] and r['ev_per_risk'] > 0 else RED
            print(
                f"  {r['run_id']:>4}  {r['symbol']:<16}  {r['quote']:<6}  "
                f"{r['win_rate']*100:>6.2f}%  "
                f"{ev_c}{r['ev_per_risk']:>+7.4f}{RST}  "
                f"{r['rr_ratio']:>4.1f}:1  "
                f"{r['n_signals']:>5}  {r['scanned_at']}"
            )

    print(f"\n{BLD}{SEP}{RST}\n")
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════
def parse_args() -> Tuple[Config, bool]:
    p = argparse.ArgumentParser(
        prog="binance_scanner.py",
        description="Binance Spot Scanner — Verbose + DB Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python binance_scanner.py
  python binance_scanner.py --budget 25 --min-wr 8.5 --max-wr 16
  python binance_scanner.py --budget 10 --interval 1h --max-scan 300
  python binance_scanner.py --tp-mult 10 --sl-mult 1.5 --min-signals 12
  python binance_scanner.py --db ./data/scans.db --log-file run.log
  python binance_scanner.py --history
        """,
    )

    # ── Core ───────────────────────────────────────────────────────────────
    g1 = p.add_argument_group("core parameters")
    g1.add_argument("--budget",     type=float, default=10.0,   metavar="USD",
                    help="Trading budget in USD (default: 10.0)")
    g1.add_argument("--min-wr",     type=float, default=9.87,   metavar="PCT",
                    help="Minimum win rate in %% (default: 9.87)")
    g1.add_argument("--max-wr",     type=float, default=14.40,  metavar="PCT",
                    help="Maximum win rate in %% (default: 14.40)")
    g1.add_argument("--tp-mult",    type=float, default=8.0,    metavar="X",
                    help="TP = entry + ATR × X (default: 8.0)")
    g1.add_argument("--sl-mult",    type=float, default=1.0,    metavar="X",
                    help="SL = entry − ATR × X (default: 1.0)")
    g1.add_argument("--trig-mult",  type=float, default=0.15,   metavar="X",
                    help="SL trigger = SL + ATR × X (default: 0.15)")

    # ── Market ─────────────────────────────────────────────────────────────
    g2 = p.add_argument_group("market parameters")
    g2.add_argument("--min-vol",    type=float, default=300_000, metavar="USDT",
                    help="Minimum 24h quote volume (default: 300000)")
    g2.add_argument("--max-scan",   type=int,   default=200,     metavar="N",
                    help="Max pairs to scan, by volume (default: 200)")
    g2.add_argument("--interval",   type=str,   default="4h",    metavar="TF",
                    help="Candle interval: 1m 5m 15m 1h 4h 1d … (default: 4h)")
    g2.add_argument("--n-candles",  type=int,   default=500,     metavar="N",
                    help="Candles to fetch per pair (default: 500)")

    # ── Backtest ───────────────────────────────────────────────────────────
    g3 = p.add_argument_group("backtest parameters")
    g3.add_argument("--rsi-low",      type=int,   default=20,    metavar="N",
                    help="RSI oversold lower bound (default: 20)")
    g3.add_argument("--rsi-high",     type=int,   default=36,    metavar="N",
                    help="RSI oversold upper bound (default: 36)")
    g3.add_argument("--lo-lookback",  type=int,   default=20,    metavar="N",
                    help="Bars for 'near recent low' check (default: 20)")
    g3.add_argument("--lo-margin",    type=float, default=1.025, metavar="X",
                    help="Close ≤ lo×X to qualify (default: 1.025)")
    g3.add_argument("--min-atr-pct",  type=float, default=0.40,  metavar="PCT",
                    help="Min ATR/price %% — skip flat coins (default: 0.40)")
    g3.add_argument("--fwd-bars",     type=int,   default=72,    metavar="N",
                    help="Max forward bars for outcome (default: 72)")
    g3.add_argument("--cool-down",    type=int,   default=5,     metavar="N",
                    help="Min bars between signals (default: 5)")
    g3.add_argument("--min-signals",  type=int,   default=8,     metavar="N",
                    help="Min backtest signals required (default: 8)")

    # ── I/O ────────────────────────────────────────────────────────────────
    g4 = p.add_argument_group("I/O")
    g4.add_argument("--db",       type=str, default="scanner.db",
                    help="SQLite database path (default: scanner.db)")
    g4.add_argument("--log-file", type=str, default="",
                    help="Optional log file path (default: none)")
    g4.add_argument("--history",  action="store_true",
                    help="Print past runs from DB and exit")

    args = p.parse_args()

    cfg = Config(
        budget      = args.budget,
        min_wr      = args.min_wr      / 100,
        max_wr      = args.max_wr      / 100,
        tp_mult     = args.tp_mult,
        sl_mult     = args.sl_mult,
        trig_mult   = args.trig_mult,
        min_vol     = args.min_vol,
        max_scan    = args.max_scan,
        interval    = args.interval,
        n_candles   = args.n_candles,
        rsi_low     = args.rsi_low,
        rsi_high    = args.rsi_high,
        lo_lookback = args.lo_lookback,
        lo_margin   = args.lo_margin,
        min_atr_pct = args.min_atr_pct / 100,
        fwd_bars    = args.fwd_bars,
        cool_down   = args.cool_down,
        min_signals = args.min_signals,
        db_path     = args.db,
        log_file    = args.log_file,
    )
    return cfg, args.history


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    cfg, want_history = parse_args()

    if want_history:
        show_history(cfg.db_path)
        return

    log = Logger(cfg.log_file)

    be_wr = 1 / (1 + cfg.tp_mult / cfg.sl_mult)

    log.header([
        f"{BLD}BINANCE SPOT SCANNER  ·  Verbose + DB Edition{RST}",
        "",
        f"  Budget        : ${cfg.budget:.2f} USD",
        f"  Win Rate      : {cfg.min_wr*100:.2f}% – {cfg.max_wr*100:.2f}%"
        f"  (break-even @ {be_wr*100:.2f}% for {cfg.tp_mult:.0f}:1 R:R)",
        f"  TP mult       : {cfg.tp_mult}×ATR     SL mult : {cfg.sl_mult}×ATR"
        f"     Trigger : SL + {cfg.trig_mult}×ATR",
        f"  Candle TF     : {cfg.interval}   ·   {cfg.n_candles} bars   ·   "
        f"max scan: {cfg.max_scan} pairs",
        f"  RSI window    : {cfg.rsi_low}–{cfg.rsi_high}"
        f"   ·   Near {cfg.lo_lookback}-bar low × {cfg.lo_margin}"
        f"   ·   ATR ≥ {cfg.min_atr_pct*100:.2f}%",
        f"  Min signals   : {cfg.min_signals}"
        f"   ·   Forward bars: {cfg.fwd_bars}"
        f"   ·   Cool-down: {cfg.cool_down} bars",
        f"  Min 24h vol   : {cfg.min_vol:,.0f} USDT",
        f"  Database      : {Path(cfg.db_path).resolve()}",
        f"  Log file      : {cfg.log_file or '(console only)'}",
    ])

    # ── DB ────────────────────────────────────────────────────────────────────
    conn = db_open(cfg.db_path, log)

    t_start = time.time()

    # ── Step 1 ────────────────────────────────────────────────────────────────
    eligible = step_exchange(cfg, log)

    # ── Step 2 ────────────────────────────────────────────────────────────────
    ticker_passed = step_ticker(eligible, cfg, log)

    n_scan  = min(len(ticker_passed), cfg.max_scan)
    run_id  = db_insert_run(conn, cfg)
    log.dblog(f"Run #{run_id} opened  "
              f"(eligible={len(eligible)}, after_ticker={len(ticker_passed)})")

    if n_scan == 0:
        log.fail("No pairs survived the filters. Exiting.")
        db_finalise_run(conn, run_id, len(eligible), len(ticker_passed), 0, 0, 0)
        log.close()
        return

    # ── Step 3 ────────────────────────────────────────────────────────────────
    log.section(
        f"STEP 3 / 3  ·  BACKTEST SCAN  "
        f"({n_scan} pairs, sorted by 24h volume)"
    )
    log.info(f"Strategy : RSI({cfg.rsi_low}–{cfg.rsi_high}) oversold + "
             f"close ≤ {cfg.lo_lookback}-bar-low × {cfg.lo_margin}")
    log.info(f"Entry    : above conditions met simultaneously")
    log.info(f"TP       : entry + {cfg.tp_mult}×ATR(14)")
    log.info(f"SL       : entry − {cfg.sl_mult}×ATR(14)")
    log.info(f"Win      : high reaches TP within {cfg.fwd_bars} bars")
    log.info(f"Loss     : low reaches SL first")
    log.raw()

    n_cand = 0

    for idx, p in enumerate(ticker_passed[:n_scan], 1):
        is_cand = scan_pair(p, idx, n_scan, run_id, cfg, log, conn)
        if is_cand:
            n_cand += 1
        time.sleep(0.08)

    # ── Finalise ──────────────────────────────────────────────────────────────
    duration = time.time() - t_start
    db_finalise_run(conn, run_id, len(eligible), len(ticker_passed),
                    n_scan, n_cand, duration)

    log.raw()
    log.dblog(
        f"Run #{run_id} finalised — "
        f"scanned={n_scan}  candidates={n_cand}  "
        f"duration={duration:.1f}s"
    )

    # ── Results ───────────────────────────────────────────────────────────────
    display_results(conn, run_id, cfg, log)

    log.raw()
    log.info(f"All results saved → {Path(cfg.db_path).resolve()}")
    log.info(f"  Table scan_runs  row #{run_id}")
    log.info(f"  Table pair_scans {n_scan} rows  (run_id={run_id})")
    log.info(f"  View history:  python binance_scanner.py --history --db {cfg.db_path}")
    log.raw()
    log.raw(f"{DIM}  ⚠  Backtest ≠ live performance.  "
            f"Only trade what you can afford to lose.{RST}")
    log.raw()
    log.close()
    conn.close()


if __name__ == "__main__":
    main()
