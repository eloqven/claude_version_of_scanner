"""Shared validation and file helpers for both scanner entry points."""

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Dict, Optional


def exclusive_log_path(logdir: Path, prefix: str, stamp: str) -> Path:
    """Create a collision-safe timestamped log path and return it."""
    for attempt in range(1000):
        tag = f"_{attempt}" if attempt else ""
        candidate = logdir / f"{prefix}_{stamp}{tag}.log"
        try:
            candidate.open("x", encoding="utf-8").close()
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"cannot create a unique log file in {logdir}")


def parse_symbol_filters(symbol: Dict) -> Optional[Dict]:
    """Parse required Binance symbol filters without unsafe defaults."""
    try:
        raw_filters = symbol.get("filters")
        if not isinstance(raw_filters, list):
            return None
        filter_map = {}
        for item in raw_filters:
            if not isinstance(item, dict):
                return None
            filter_type = item.get("filterType")
            if not isinstance(filter_type, str) or not filter_type:
                return None
            filter_map[filter_type] = item

        price_filter = filter_map.get("PRICE_FILTER")
        lot_filter = filter_map.get("LOT_SIZE")
        notional_filter = (filter_map.get("NOTIONAL")
                           or filter_map.get("MIN_NOTIONAL"))
        if price_filter is None or lot_filter is None or notional_filter is None:
            return None

        raw_min_price = Decimal(price_filter["minPrice"])
        raw_max_price = Decimal(price_filter["maxPrice"])
        raw_tick = Decimal(price_filter["tickSize"])
        min_qty = Decimal(lot_filter["minQty"])
        max_qty = Decimal(lot_filter["maxQty"])
        step = Decimal(lot_filter["stepSize"])
        min_val = Decimal(notional_filter["minNotional"])
        max_notional = notional_filter.get("maxNotional")
        if "NOTIONAL" in filter_map and max_notional is None:
            return None
        max_val = Decimal(max_notional) if max_notional is not None else None

        market_min_qty = market_max_qty = market_step = None
        market_lot = filter_map.get("MARKET_LOT_SIZE")
        if market_lot is not None:
            raw_market_min = Decimal(market_lot["minQty"])
            raw_market_max = Decimal(market_lot["maxQty"])
            raw_market_step = Decimal(market_lot["stepSize"])
            if not all(value.is_finite() for value in
                       (raw_market_min, raw_market_max, raw_market_step)):
                return None
            if min(raw_market_min, raw_market_max, raw_market_step) < 0:
                return None
            market_min_qty = raw_market_min if raw_market_min > 0 else None
            market_max_qty = raw_market_max if raw_market_max > 0 else None
            market_step = raw_market_step if raw_market_step > 0 else None
            if (market_min_qty is not None and market_max_qty is not None
                    and market_min_qty > market_max_qty):
                return None

        percent_buy_ranges = []
        percent_sell_ranges = []
        percent_mins = set()
        percent_price = filter_map.get("PERCENT_PRICE")
        if percent_price is not None:
            common_range = (Decimal(percent_price["multiplierDown"]),
                            Decimal(percent_price["multiplierUp"]))
            percent_buy_ranges.append(common_range)
            percent_sell_ranges.append(common_range)
            avg_mins = percent_price["avgPriceMins"]
            if type(avg_mins) is not int or avg_mins < 0:
                return None
            percent_mins.add(avg_mins)
        percent_side = filter_map.get("PERCENT_PRICE_BY_SIDE")
        if percent_side is not None:
            percent_buy_ranges.append(
                (Decimal(percent_side["bidMultiplierDown"]),
                 Decimal(percent_side["bidMultiplierUp"])))
            percent_sell_ranges.append(
                (Decimal(percent_side["askMultiplierDown"]),
                 Decimal(percent_side["askMultiplierUp"])))
            avg_mins = percent_side["avgPriceMins"]
            if type(avg_mins) is not int or avg_mins < 0:
                return None
            percent_mins.add(avg_mins)
        if len(percent_mins) > 1:
            return None
        all_percent_ranges = percent_buy_ranges + percent_sell_ranges
        if any(not low.is_finite() or not high.is_finite()
               or low <= 0 or high <= 0 or low > high
               for low, high in all_percent_ranges):
            return None
        percent_buy_min_mult = (max(low for low, _ in percent_buy_ranges)
                                if percent_buy_ranges else None)
        percent_buy_max_mult = (min(high for _, high in percent_buy_ranges)
                                if percent_buy_ranges else None)
        percent_min_mult = (max(low for low, _ in percent_sell_ranges)
                            if percent_sell_ranges else None)
        percent_max_mult = (min(high for _, high in percent_sell_ranges)
                            if percent_sell_ranges else None)
        if (percent_buy_min_mult is not None
                and percent_buy_min_mult > percent_buy_max_mult):
            return None
        if (percent_min_mult is not None
                and percent_min_mult > percent_max_mult):
            return None

        required_values = (raw_min_price, raw_max_price, raw_tick, min_qty,
                           max_qty, step, min_val)
        if not all(value.is_finite() for value in required_values):
            return None
        if min(raw_min_price, raw_max_price, raw_tick, min_qty, min_val) < 0:
            return None
        if max_qty <= 0 or step <= 0 or min_qty > max_qty:
            return None
        if (raw_min_price > 0 and raw_max_price > 0
                and raw_min_price > raw_max_price):
            return None
        if max_val is not None:
            if not max_val.is_finite() or max_val <= 0 or min_val > max_val:
                return None

        filters = dict(
            min_price=raw_min_price if raw_min_price > 0 else None,
            max_price=raw_max_price if raw_max_price > 0 else None,
            tick=raw_tick if raw_tick > 0 else None,
            min_qty=min_qty,
            max_qty=max_qty,
            step=step,
            min_val=min_val,
            max_val=max_val,
            market_min_qty=market_min_qty,
            market_max_qty=market_max_qty,
            market_step=market_step,
            percent_min_mult=percent_min_mult,
            percent_max_mult=percent_max_mult,
            percent_buy_min_mult=percent_buy_min_mult,
            percent_buy_max_mult=percent_buy_max_mult,
            percent_avg_mins=next(iter(percent_mins), None),
            percent_ref=None,
        )
    except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation):
        return None
    return filters


def load_percent_price_reference(
        pair: Dict, get_json: Callable, base_url: str) -> bool:
    """Load the Binance reference price used by percent-price filters."""
    if pair.get("percent_min_mult") is None:
        return True
    if pair.get("percent_avg_mins") == 0:
        data = {"price": pair.get("price"), "mins": 0}
    else:
        data = get_json(
            f"{base_url}/api/v3/avgPrice", {"symbol": pair["symbol"]})
    try:
        if (not isinstance(data, dict)
                or data.get("mins") != pair["percent_avg_mins"]):
            return False
        reference = Decimal(str(data["price"]))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return False
    if not reference.is_finite() or reference <= 0:
        return False
    pair["percent_ref"] = reference
    return True
