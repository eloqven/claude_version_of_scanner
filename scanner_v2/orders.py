"""Executable V2 limit-entry and OCO layout validation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Optional

from .strategy import AdaptiveConfig, ceil_tick, floor_tick


@dataclass(frozen=True)
class OrderLevels:
    entry: Decimal
    quantity: Decimal
    take_profit: Decimal
    stop_trigger: Decimal
    stop_limit: Decimal

    @property
    def rr_to_trigger(self) -> Decimal:
        return (self.take_profit - self.entry) / (self.entry - self.stop_trigger)

    @property
    def rr_to_limit(self) -> Decimal:
        return (self.take_profit - self.entry) / (self.entry - self.stop_limit)

    def as_dict(self) -> dict:
        return {
            "entry": str(self.entry), "quantity": str(self.quantity),
            "take_profit": str(self.take_profit), "stop_trigger": str(self.stop_trigger),
            "stop_limit": str(self.stop_limit),
            "rr_to_trigger": str(self.rr_to_trigger), "rr_to_limit": str(self.rr_to_limit),
        }


def _is_tick_aligned(value: Decimal, tick: Decimal) -> bool:
    return tick > 0 and value % tick == 0


def _valid_price(value: Decimal, filters: Mapping[str, object], tick: Decimal) -> bool:
    if value <= 0 or not value.is_finite() or not _is_tick_aligned(value, tick):
        return False
    minimum = filters.get("min_price")
    maximum = filters.get("max_price")
    if ((minimum is not None and (not isinstance(minimum, Decimal) or not minimum.is_finite())) or
            (maximum is not None and (not isinstance(maximum, Decimal) or not maximum.is_finite()))):
        return False
    return ((minimum is None or value >= minimum) and
            (maximum is None or value <= maximum))


def _in_percent_band(value: Decimal, lower: object, upper: object,
                     reference: object) -> bool:
    if lower is None and upper is None:
        return True
    if not isinstance(lower, Decimal) or not isinstance(upper, Decimal) or \
            not isinstance(reference, Decimal) or not reference.is_finite() or reference <= 0:
        return False
    return reference * lower <= value <= reference * upper


def build_order(*, entry: Decimal, atr: Decimal, target: Decimal, budget: Decimal,
                filters: Mapping[str, object], config: AdaptiveConfig) -> Optional[OrderLevels]:
    """Build a tick-valid public-data order layout, failing closed on missing filters.

    `entry` must be the live best ask and is not rounded by this function: a quote that
    is not already tick-valid is rejected rather than silently changed.
    """
    try:
        entry, atr, target, budget = (Decimal(str(value)) for value in
                                      (entry, atr, target, budget))
        tick = filters.get("tick")
        step = filters.get("step")
        min_qty = filters.get("min_qty")
        max_qty = filters.get("max_qty")
        min_val = filters.get("min_val")
        if not all(isinstance(value, Decimal) for value in
                   (tick, step, min_qty, max_qty, min_val)):
            return None
        if not all(value.is_finite() for value in (entry, atr, target, budget, tick, step,
                                                   min_qty, max_qty, min_val)):
            return None
        if entry <= 0 or atr <= 0 or target <= 0 or budget <= 0 or tick <= 0 or step <= 0:
            return None
        if not _is_tick_aligned(entry, tick):
            return None
        quantity = floor_tick(budget / entry, step)
        take_profit = floor_tick(target, tick)
        stop_limit = floor_tick(entry - atr * config.sl_mult, tick)
        stop_trigger = ceil_tick(stop_limit + atr * config.trig_mult, tick)
    except (ArithmeticError, ValueError):
        return None
    if quantity <= 0 or quantity < min_qty or quantity > max_qty or quantity % step != 0:
        return None
    # Do not apply MARKET_LOT_SIZE: this is a limit entry and sell OCO layout.
    if not all(_valid_price(price, filters, tick)
               for price in (entry, take_profit, stop_trigger, stop_limit)):
        return None
    if not (take_profit > entry > stop_trigger > stop_limit > 0):
        return None
    reference = filters.get("percent_ref")
    if not _in_percent_band(entry, filters.get("percent_buy_min_mult"),
                            filters.get("percent_buy_max_mult"), reference):
        return None
    if not all(_in_percent_band(price, filters.get("percent_min_mult"),
                                filters.get("percent_max_mult"), reference)
               for price in (take_profit, stop_trigger, stop_limit)):
        return None
    max_val = filters.get("max_val")
    if max_val is not None and (not isinstance(max_val, Decimal) or not max_val.is_finite() or max_val <= 0):
        return None
    for price in (entry, take_profit, stop_limit):
        notional = quantity * price
        if notional < min_val or (max_val is not None and notional > max_val):
            return None
    return OrderLevels(entry, quantity, take_profit, stop_trigger, stop_limit)
