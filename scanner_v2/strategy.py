"""Pure adaptive TP selection and conservative opportunity scoring."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math
from statistics import median
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from .models import Candle, IndicatorFrame


STRATEGY_VERSION = "adaptive-tp-v2"


def _decimal(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("strategy values must be finite")
    return result


def floor_tick(value: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        return value
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def ceil_tick(value: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        return value
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


@dataclass(frozen=True)
class AdaptiveConfig:
    min_wr: float = 0.0987
    max_wr: float = 0.1440
    tp_mult: Decimal = Decimal("8")
    sl_mult: Decimal = Decimal("1")
    trig_mult: Decimal = Decimal("0.15")
    rsi_low: float = 20
    rsi_high: float = 36
    lo_lookback: int = 20
    lo_margin: Decimal = Decimal("1.025")
    min_atr_pct: Decimal = Decimal("0.004")
    fwd_bars: int = 72
    cool_down: int = 5
    min_signals: int = 8
    resistance_lookback: int = 100
    resistance_cluster_atr: Decimal = Decimal("0.25")
    version: str = STRATEGY_VERSION

    def __post_init__(self) -> None:
        for value in (self.tp_mult, self.sl_mult, self.trig_mult, self.lo_margin,
                      self.min_atr_pct, self.resistance_cluster_atr):
            if _decimal(value) < 0:
                raise ValueError("adaptive multipliers cannot be negative")
        if not (0 < self.min_wr <= self.max_wr <= 1):
            raise ValueError("min_wr and max_wr must be in the inclusive 0..1 range")
        if self.tp_mult <= 0 or self.sl_mult <= 0 or not (0 < self.trig_mult < self.sl_mult):
            raise ValueError("TP/SL/trigger multipliers are invalid")
        if self.fwd_bars <= 0 or self.cool_down < 0 or self.min_signals <= 0:
            raise ValueError("forward bars, cooldown, and minimum signals are invalid")
        if self.lo_lookback <= 0 or self.resistance_lookback < 5:
            raise ValueError("lookbacks are invalid")


@dataclass(frozen=True)
class Opportunity:
    """A signal frozen before target evaluation; fields use candle indexes."""

    signal_index: int
    entry_index: int
    last_index: int
    entry: Decimal
    atr: Decimal

    def __post_init__(self) -> None:
        if not (0 <= self.signal_index < self.entry_index <= self.last_index):
            raise ValueError("opportunity indexes are invalid")
        if self.entry <= 0 or self.atr <= 0:
            raise ValueError("opportunity entry and ATR must be positive")


@dataclass(frozen=True)
class TargetScore:
    multiplier: Decimal
    source: str
    wins: int
    losses: int
    timeouts: int

    @property
    def opportunities(self) -> int:
        return self.wins + self.losses + self.timeouts

    @property
    def hit_rate(self) -> float:
        return self.wins / self.opportunities if self.opportunities else 0.0

    def as_dict(self) -> dict:
        return {
            "multiplier": str(self.multiplier), "source": self.source,
            "wins": self.wins, "losses": self.losses, "timeouts": self.timeouts,
            "opportunities": self.opportunities, "hit_rate": self.hit_rate,
        }


@dataclass(frozen=True)
class ResistanceEvidence:
    resistance: Decimal
    touches: int
    median_absolute_deviation: Decimal
    buffer: Decimal
    target: Decimal
    multiplier: Decimal


@dataclass(frozen=True)
class PairEvaluation:
    symbol: str
    signal_state: str
    baseline: TargetScore
    selected: Optional[TargetScore]
    target_source: str
    resistance: Optional[ResistanceEvidence]
    warning: Optional[str]
    opportunities: Tuple[Opportunity, ...]


@dataclass(frozen=True)
class StrategyTrace:
    opportunities: Tuple[Opportunity, ...]
    resistance_candidates: Tuple[ResistanceEvidence, ...]
    scores: Tuple[TargetScore, ...]


def _signal_at(frame: pd.DataFrame, index: int, config: AdaptiveConfig) -> bool:
    if index < max(config.lo_lookback, 1):
        return False
    atr = frame.at[index, "atr"]
    rsi = frame.at[index, "rsi"]
    if pd.isna(atr) or pd.isna(rsi) or atr <= 0:
        return False
    close = _decimal(frame.at[index, "close"])
    if close <= 0 or _decimal(atr) / close < config.min_atr_pct:
        return False
    low = min(_decimal(value) for value in frame.loc[
        index - config.lo_lookback:index - 1, "low"])
    return config.rsi_low <= float(rsi) <= config.rsi_high and close <= low * config.lo_margin


def freeze_opportunities(indicators: IndicatorFrame, config: AdaptiveConfig) -> Tuple[Opportunity, ...]:
    """Lock non-overlapping full-window signals before any target is tested."""
    frame = indicators.frame
    candles = indicators.batch.candles
    lockout = max(config.fwd_bars, config.cool_down)
    first = max(config.lo_lookback, 14) + 1
    next_allowed = 0
    output: List[Opportunity] = []
    # The next candle is the entry; last_index is inclusive and provides exactly fwd_bars bars.
    for index in range(first, len(candles) - config.fwd_bars):
        if index < next_allowed or not _signal_at(frame, index, config):
            continue
        entry_index = index + 1
        opportunity = Opportunity(
            signal_index=index, entry_index=entry_index,
            last_index=index + config.fwd_bars,
            entry=candles[entry_index].open, atr=_decimal(frame.at[index, "atr"]),
        )
        output.append(opportunity)
        next_allowed = index + lockout
    return tuple(output)


def score_multiplier(candles: Sequence[Candle], opportunities: Sequence[Opportunity],
                     config: AdaptiveConfig, multiplier: Decimal, *, tick: Decimal,
                     source: str = "ATR_FALLBACK",
                     dual_hit_resolver: Optional[Callable[[Opportunity, Candle, Decimal, Decimal], str]] = None) -> TargetScore:
    """Score one target multiplier against an already-frozen opportunity set."""
    multiplier = _decimal(multiplier)
    tick = _decimal(tick)
    if multiplier < config.tp_mult:
        raise ValueError("target multiplier is below the configured minimum")
    wins = losses = timeouts = 0
    for opportunity in opportunities:
        entry = floor_tick(opportunity.entry, tick)
        target = floor_tick(entry + opportunity.atr * multiplier, tick)
        stop_limit = floor_tick(entry - opportunity.atr * config.sl_mult, tick)
        stop_trigger = ceil_tick(stop_limit + opportunity.atr * config.trig_mult, tick)
        if not (target > entry > stop_trigger > stop_limit > 0):
            losses += 1
            continue
        outcome = "timeout"
        for candle in candles[opportunity.entry_index:opportunity.last_index + 1]:
            hit_target = candle.high >= target
            hit_stop = candle.low <= stop_trigger
            if hit_target and hit_stop:
                outcome = dual_hit_resolver(opportunity, candle, target, stop_trigger) if dual_hit_resolver else "loss"
                if outcome not in {"win", "loss"}:
                    outcome = "loss"
                break
            if hit_target:
                outcome = "win"
                break
            if hit_stop:
                outcome = "loss"
                break
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        else:
            timeouts += 1
    return TargetScore(multiplier, source, wins, losses, timeouts)


def _band_has_discrete_rate(opportunities: int, min_wr: float, max_wr: float) -> bool:
    if opportunities <= 0:
        return False
    return math.ceil(min_wr * opportunities - 1e-12) <= math.floor(max_wr * opportunities + 1e-12)


def select_hardest_passing(scores: Iterable[TargetScore], min_wr: float,
                           max_wr: float,
                           executable: Optional[Callable[[TargetScore], bool]] = None) -> Tuple[Optional[TargetScore], bool]:
    """Choose the farthest in-range target and flag an impossible discrete band."""
    ordered = sorted(scores, key=lambda score: score.multiplier)
    passing = [score for score in ordered
               if min_wr <= score.hit_rate <= max_wr and
               (executable is None or executable(score))]
    opportunities = ordered[0].opportunities if ordered else 0
    return (passing[-1] if passing else None,
            not _band_has_discrete_rate(opportunities, min_wr, max_wr))


def _swing_highs(candles: Sequence[Candle]) -> List[Decimal]:
    output: List[Decimal] = []
    for index in range(2, len(candles) - 2):
        value = candles[index].high
        if value > max(candle.high for candle in candles[index - 2:index]) and \
                value > max(candle.high for candle in candles[index + 1:index + 3]):
            output.append(value)
    return output


def resistance_candidates(candles: Sequence[Candle], *, entry: Decimal, atr: Decimal,
                          tick: Decimal, spread: Decimal,
                          config: AdaptiveConfig) -> Tuple[ResistanceEvidence, ...]:
    """Create executable, clustered five-candle swing-high resistance targets."""
    window = candles[-config.resistance_lookback:]
    levels = sorted(_swing_highs(window))
    if not levels:
        return ()
    clusters: List[List[Decimal]] = [[levels[0]]]
    threshold = atr * config.resistance_cluster_atr
    for level in levels[1:]:
        if level - clusters[-1][-1] <= threshold:
            clusters[-1].append(level)
        else:
            clusters.append([level])
    evidence: List[ResistanceEvidence] = []
    minimum_target = entry + atr * config.tp_mult
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        level = _decimal(median(cluster))
        mad = _decimal(median([abs(value - level) for value in cluster]))
        buffer = max(tick, spread, mad)
        target = floor_tick(level - buffer, tick)
        if target < minimum_target or target <= entry:
            continue
        evidence.append(ResistanceEvidence(
            resistance=level, touches=len(cluster), median_absolute_deviation=mad,
            buffer=buffer, target=target, multiplier=(target - entry) / atr,
        ))
    return tuple(sorted(evidence, key=lambda item: item.target))


def fallback_multipliers(candles: Sequence[Candle], opportunities: Sequence[Opportunity],
                         config: AdaptiveConfig) -> Tuple[Decimal, ...]:
    """Derive ATR breakpoints from each frozen opportunity's favorable excursion."""
    breakpoints = set()
    for opportunity in opportunities:
        high = max(candle.high for candle in candles[
            opportunity.entry_index:opportunity.last_index + 1])
        multiplier = (high - opportunity.entry) / opportunity.atr
        if multiplier >= config.tp_mult:
            breakpoints.add(multiplier.normalize())
    return tuple(sorted(breakpoints))


class AdaptiveStrategy:
    """Pure strategy: normalized data in, evaluation plus trace out."""

    def __init__(self, config: AdaptiveConfig) -> None:
        self.config = config

    def evaluate(self, indicators: IndicatorFrame, *, entry: Decimal, tick: Decimal,
                 spread: Decimal = Decimal("0"),
                 is_executable: Optional[Callable[[Decimal], bool]] = None,
                 dual_hit_resolver: Optional[Callable[[Opportunity, Candle, Decimal, Decimal], str]] = None) -> Tuple[PairEvaluation, StrategyTrace]:
        candles = indicators.batch.candles
        opportunities = freeze_opportunities(indicators, self.config)
        current_index = len(candles) - 1
        signal_state = "ACTIVE" if _signal_at(indicators.frame, current_index, self.config) else "INACTIVE"
        current_atr = indicators.frame.at[current_index, "atr"]
        if pd.isna(current_atr) or current_atr <= 0:
            baseline = TargetScore(self.config.tp_mult, "ATR_FALLBACK", 0, 0, 0)
            evaluation = PairEvaluation(indicators.batch.symbol, signal_state, baseline, None,
                                        "NO_FEASIBLE_TP", None, "ATR_INVALID", opportunities)
            return evaluation, StrategyTrace(opportunities, (), ())
        atr = _decimal(current_atr)
        minimum_history = max(self.config.lo_lookback, 14) + self.config.fwd_bars + 2
        if len(candles) < minimum_history:
            baseline = TargetScore(self.config.tp_mult, "ATR_FALLBACK", 0, 0, 0)
            evaluation = PairEvaluation(
                indicators.batch.symbol, signal_state, baseline, None,
                "NO_FEASIBLE_TP", None, "INSUFFICIENT_HISTORY", opportunities,
            )
            return evaluation, StrategyTrace(opportunities, (), (baseline,))
        baseline = score_multiplier(candles, opportunities, self.config, self.config.tp_mult,
                                    tick=tick, source="ATR_FALLBACK",
                                    dual_hit_resolver=dual_hit_resolver)
        if baseline.opportunities < self.config.min_signals:
            evaluation = PairEvaluation(
                indicators.batch.symbol, signal_state, baseline, None,
                "NO_FEASIBLE_TP", None, "INSUFFICIENT_SIGNALS", opportunities,
            )
            return evaluation, StrategyTrace(opportunities, (), (baseline,))
        resistance = resistance_candidates(candles, entry=entry, atr=atr, tick=tick,
                                           spread=spread, config=self.config)
        scores: List[TargetScore] = []
        for item in resistance:
            scores.append(score_multiplier(candles, opportunities, self.config, item.multiplier,
                                           tick=tick, source="RESISTANCE",
                                           dual_hit_resolver=dual_hit_resolver))
        resistance_by_multiplier = {item.multiplier: item for item in resistance}
        selected, warning = select_hardest_passing(
            scores, self.config.min_wr, self.config.max_wr,
            executable=(lambda score: is_executable(resistance_by_multiplier[score.multiplier].target))
            if is_executable else None,
        )
        if selected is not None:
            item = next(candidate for candidate in resistance
                        if candidate.multiplier == selected.multiplier)
            evaluation = PairEvaluation(indicators.batch.symbol, signal_state, baseline, selected,
                                        "RESISTANCE", item,
                                        "DISCRETE_RATE_BAND" if warning else None, opportunities)
            return evaluation, StrategyTrace(opportunities, resistance, tuple(scores))
        fallback_scores = [
            score_multiplier(candles, opportunities, self.config, multiplier, tick=tick,
                             source="ATR_FALLBACK", dual_hit_resolver=dual_hit_resolver)
            for multiplier in fallback_multipliers(candles, opportunities, self.config)
        ]
        selected, fallback_warning = select_hardest_passing(
            fallback_scores, self.config.min_wr, self.config.max_wr,
            executable=(lambda score: is_executable(
                floor_tick(entry + atr * score.multiplier, tick))) if is_executable else None,
        )
        if selected is not None:
            evaluation = PairEvaluation(indicators.batch.symbol, signal_state, baseline, selected,
                                        "ATR_FALLBACK", None,
                                        "DISCRETE_RATE_BAND" if fallback_warning else None, opportunities)
            return evaluation, StrategyTrace(opportunities, resistance, tuple(scores + fallback_scores))
        warning_text = "DISCRETE_RATE_BAND" if (warning or fallback_warning) else None
        evaluation = PairEvaluation(indicators.batch.symbol, signal_state, baseline, None,
                                    "NO_FEASIBLE_TP", None, warning_text, opportunities)
        return evaluation, StrategyTrace(opportunities, resistance, tuple(scores + fallback_scores))
