"""Versioned indicator calculations that intentionally match scanner V1."""

from __future__ import annotations

import pandas as pd

from .models import CandleBatch, IndicatorFrame, IndicatorSpec


class IndicatorEngine:
    """Compute V1-compatible SMA ATR and RSI from normalized candles."""

    def compute(self, batch: CandleBatch, spec: IndicatorSpec) -> IndicatorFrame:
        frame = pd.DataFrame({
            "open_time_us": [candle.open_time_us for candle in batch.candles],
            "open": [float(candle.open) for candle in batch.candles],
            "high": [float(candle.high) for candle in batch.candles],
            "low": [float(candle.low) for candle in batch.candles],
            "close": [float(candle.close) for candle in batch.candles],
            "volume": [float(candle.volume) for candle in batch.candles],
            "trades": [candle.trade_count for candle in batch.candles],
        })
        previous_close = frame["close"].shift(1)
        true_range = pd.concat([
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ], axis=1).max(axis=1)
        frame["atr"] = true_range.rolling(spec.atr_period).mean()
        delta = frame["close"].diff()
        gain = delta.clip(lower=0).rolling(spec.rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(spec.rsi_period).mean()
        frame["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
        return IndicatorFrame(batch, spec, frame)
