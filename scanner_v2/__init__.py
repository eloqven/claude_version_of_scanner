"""Source-independent primitives for the adaptive TP research scanner."""

from .indicators import IndicatorEngine
from .models import (
    BookQuote,
    Candle,
    CandleBatch,
    CandleIntegrityError,
    CandleQuery,
    IndicatorFrame,
    IndicatorSpec,
    SourceError,
    interval_to_us,
)
from .sources import CandleSource, QuoteSource, RestCandleSource, RestQuoteSource, resample_candles
from .stores import ResearchStore, ScanStore
from .orders import OrderLevels, build_order
from .strategy import (
    AdaptiveConfig,
    AdaptiveStrategy,
    Opportunity,
    PairEvaluation,
    ResistanceEvidence,
    StrategyTrace,
    TargetScore,
    fallback_multipliers,
    floor_tick,
    freeze_opportunities,
    resistance_candidates,
    score_multiplier,
    select_hardest_passing,
)

__all__ = [
    "AdaptiveConfig", "AdaptiveStrategy", "BookQuote", "Candle", "CandleBatch", "CandleIntegrityError", "CandleQuery",
    "CandleSource", "IndicatorEngine", "IndicatorFrame", "IndicatorSpec",
    "QuoteSource", "ResearchStore", "RestCandleSource", "RestQuoteSource", "interval_to_us",
    "Opportunity", "OrderLevels", "PairEvaluation", "ResistanceEvidence", "ScanStore", "SourceError",
    "StrategyTrace", "TargetScore", "fallback_multipliers", "freeze_opportunities",
    "build_order", "floor_tick", "resistance_candidates", "resample_candles", "score_multiplier", "select_hardest_passing",
]
