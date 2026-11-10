"""NIFTY two-minute Supertrend long-option strategy."""

import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from growwapi import GrowwAPI


INTERVAL_MINUTES = 2
INTERVAL_NAME = "2minute"

CONFIG = {
    "script_name": "NIFTYSupperBuy",
    "index_symbol": "NIFTY",
    "exchange": "NSE",
    "trading_timezone_offset_minutes": 330,
    "atr_period": 10,
    "factor": 3.0,
    "strike_step": 50,
    # Position sizing is bounded by both the allocation and this lot cap.
    "lot_size": 65,
    "max_lots_per_trade": 1,
    "expiry_fs": ["19Oct26", "27Oct26", "03Nov26", "09Nov26", "17Nov26", "23Nov26"],
    "order_expiry": ["26O19", "26OCT", "26N03", "26N09", "26N17", "26NOV"],
    "entry_window_start": "09:15",
    "entry_window_end": "15:10",
    "run_end_time": "15:30",
    # Maximum option-premium budget for each trade, subject to available margin.
    "allocation_per_trade": 35000,
    "milestone_points": 20,
    "milestone_points_by_side": {"CE": 20, "PE": 20},
    "enable_actual_trade": False,
    "api_rate_limit_retry_delay": 5,
    "api_rate_limit_max_retries": 3,
}


def _now_ist(config):
    offset = timedelta(minutes=int(config.get("trading_timezone_offset_minutes", 330)))
    return datetime.now(timezone.utc).astimezone(timezone(offset))


def _log(config, message):
    now = _now_ist(config).strftime("%Y-%m-%d %H:%M:%S")
    text = f"[{now}] {message}"
    print(text, flush=True)
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    path = log_dir / f"{config['script_name']}_{_now_ist(config):%Y%m%d}.log"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError as exc:
        print(f"[LOG ERROR] {path}: {exc}", flush=True)


def _normalize_access_token(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("access_token", "token", "jwt", "value"):
            token = value.get(key)
            if isinstance(token, str) and token.strip():
                return token.strip()
        for key in ("data", "payload"):
            if isinstance(value.get(key), dict):
                return _normalize_access_token(value[key])
    raise ValueError(f"Unsupported Groww access token response: {type(value).__name__}")


def _candle_rows(response):
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        for key in ("candles", "data", "payload"):
            nested = response.get(key)
            rows = _candle_rows(nested)
            if rows is not None:
                return rows
    return None


def _dataframe(rows):
    if not rows:
        return pd.DataFrame()
    if len(rows[0]) not in (6, 7):
        raise ValueError(f"Unexpected candle row length: {len(rows[0])}")
    columns = ["date", "open", "high", "low", "close", "volume"]
    if len(rows[0]) == 7:
        columns.append("oi")
    result = pd.DataFrame(rows, columns=columns)
    result["date"] = pd.to_datetime(result["date"])
    for column in ("open", "high", "low", "close"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return (
        result.dropna(subset=["date", "open", "high", "low", "close"])
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )


def _fetch_candles(api, config, symbol, segment):
    exchange = config["exchange"]
    groww_symbol = f"{exchange}-{symbol}"
    end_time = datetime.now()
    start_time = end_time - timedelta(days=7)
    retries = int(config.get("api_rate_limit_max_retries", 3))
    for attempt in range(retries + 1):
        try:
            response = api.get_historical_candles(
                groww_symbol=groww_symbol,
                exchange=getattr(api, f"EXCHANGE_{exchange}"),
                segment=getattr(api, segment),
                candle_interval=INTERVAL_NAME,
                start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            frame = _dataframe(_candle_rows(response))
            if not frame.empty:
                return frame
            raise RuntimeError(f"No {INTERVAL_MINUTES}-minute candles returned for {groww_symbol}")
        except Exception as exc:
            if "rate limit" in str(exc).lower() and attempt < retries:
                delay = int(config.get("api_rate_limit_retry_delay", 5))
                _log(config, f"[RATE LIMIT] {groww_symbol}; retrying in {delay}s ({attempt + 1}/{retries})")
                time.sleep(delay)
                continue
            raise


def _wilder_rma(values, period):
    result = np.full(len(values), np.nan)
    if period < 1 or len(values) < period:
        return result
    result[period - 1] = float(np.nanmean(values[:period]))
    for index in range(period, len(values)):
        result[index] = (result[index - 1] * (period - 1) + values[index]) / period
    return result


def supertrend(frame, period, factor):
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    previous_close = np.roll(close, 1)
    previous_close[0] = np.nan
    true_range = np.fmax.reduce(
        np.vstack((high - low, np.abs(high - previous_close), np.abs(low - previous_close))),
        axis=0,
    )
    atr = _wilder_rma(true_range, int(period))
    midpoint = (high + low) / 2
    basic_upper = midpoint + float(factor) * atr
    basic_lower = midpoint - float(factor) * atr
    upper = np.full(len(frame), np.nan)
    lower = np.full(len(frame), np.nan)
    direction = np.full(len(frame), -1, dtype=int)
    line = np.full(len(frame), np.nan)

    for index in range(len(frame)):
        if np.isnan(atr[index]):
            continue
        if index == 0 or np.isnan(atr[index - 1]):
            upper[index] = basic_upper[index]
            lower[index] = basic_lower[index]
            line[index] = upper[index]
            continue
        upper[index] = (
            basic_upper[index]
            if basic_upper[index] < upper[index - 1] or close[index - 1] > upper[index - 1]
            else upper[index - 1]
        )
        lower[index] = (
            basic_lower[index]
            if basic_lower[index] > lower[index - 1] or close[index - 1] < lower[index - 1]
            else lower[index - 1]
        )
        direction[index] = (
            1 if close[index] > upper[index] else -1
        ) if direction[index - 1] == -1 else (
            -1 if close[index] < lower[index] else 1
        )
        line[index] = lower[index] if direction[index] == 1 else upper[index]
    return line, direction


def _expiry_pairs(config, now):
    expiries = config.get("expiry_fs", [])
    order_expiries = config.get("order_expiry", [])
    pairs = []
    for index, expiry_text in enumerate(expiries):
        try:
            expiry_date = datetime.strptime(str(expiry_text), "%d%b%y").date()
        except ValueError as exc:
            raise ValueError(f"Invalid expiry_fs entry {expiry_text!r}; expected DDMonYY") from exc
        if index >= len(order_expiries):
            raise ValueError(f"Missing order_expiry for {expiry_text}")
        pairs.append((expiry_date, str(expiry_text), str(order_expiries[index]).upper()))
    return pairs


def _nearest_expiry_pair(config, now):
    pairs = _expiry_pairs(config, now)
    upcoming = [pair for pair in pairs if pair[0] >= now.date()]
    if not upcoming:
        raise ValueError(f"No configured future option expiry for {config['index_symbol']}")
    return min(upcoming, key=lambda pair: pair[0])


def _atm_strike(index_price, step, side):
    lower = math.floor(float(index_price) / step) * step
    upper = lower + step
    lower_gap = float(index_price) - lower
    upper_gap = upper - float(index_price)
    if lower_gap < upper_gap:
        return lower
    if upper_gap < lower_gap:
        return upper
    return upper if side == "CE" else lower


def _make_contract(config, expiry, order_expiry, strike, side):
    root = config["index_symbol"].upper()
    candle_symbol = f"{root}-{expiry}-{int(strike)}-{side}"
    order_symbol = f"{root}{order_expiry}{int(strike)}{side}"
    return {
        "root": root,
        "candle_symbol": candle_symbol,
        "order_symbol": order_symbol,
        "strike": int(strike),
        "side": side,
        "expiry": expiry,
        "order_expiry": order_expiry,
    }


def _contract_from_order(config, order_symbol):
    root = re.escape(config["index_symbol"].upper())
    for _, expiry, order_expiry in _expiry_pairs(config, _now_ist(config)):
        match = re.fullmatch(
            rf"{root}{re.escape(order_expiry)}(\d+)(CE|PE)",
            str(order_symbol or "").upper(),
        )
        if match:
            strike, side = match.groups()
            return _make_contract(config, expiry, order_expiry, int(strike), side)
    return None


def _positions_response_rows(response):
    if isinstance(response, dict):
        rows = response.get("data") or response.get("positions")
        if isinstance(rows, list):
            return rows
    raise RuntimeError("Groww returned an unexpected open-positions response")


def _get_live_positions(api, config):
    response = api.get_positions_for_user(segment=api.SEGMENT_FNO)
    root = config["index_symbol"].upper()
    active = []
    for row in _positions_response_rows(response):
        symbol = str(row.get("trading_symbol") or row.get("symbol") or "").upper()
        if not symbol.startswith(root):
            continue
        quantity = int(row.get("quantity", row.get("net_quantity", 0)) or 0)
        if quantity < 0:
            raise RuntimeError(
                f"Unexpected short {root} position {symbol} ({quantity}); "
                "long-option strategy will not reconcile it as a long position"
            )
        if quantity == 0:
            continue
        active.append(
            {
                "order_symbol": symbol,
                "qty": abs(quantity),
                "entry_price": float(row.get("average_price", row.get("net_price", 0)) or 0),
            }
        )
    return active


def _extract_response_rows(response, list_keys):
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        for key in list_keys:
            if key in response:
                rows = _extract_response_rows(response[key], list_keys)
                if rows is not None:
                    return rows
    return None


def _parse_order_time(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(minutes=330)))
    return parsed.astimezone(timezone.utc)


def _manual_exit_fill(api, config, position, requested_quantity, previously_closed=0):
    entry_time = _parse_order_time(position.get("entry_time"))
    if entry_time is None:
        raise RuntimeError("Cannot reconcile manual exit without the recorded entry time")

    sell_orders = []
    for page in range(5):
        response = api.get_order_list(page=page, page_size=25, segment=api.SEGMENT_FNO)
        rows = _extract_response_rows(response, ("orders", "order_list", "data", "payload"))
        if rows is None:
            raise RuntimeError("Groww returned an unexpected order-list response")
        if not rows:
            break
        for row in rows:
            symbol = str(row.get("trading_symbol") or row.get("symbol") or "").upper()
            transaction = str(
                row.get("transaction_type") or row.get("transactionType") or row.get("side") or ""
            ).upper()
            status = str(row.get("order_status") or row.get("status") or "").upper()
            order_time = _parse_order_time(
                row.get("order_date")
                or row.get("order_date_time")
                or row.get("created_at")
                or row.get("timestamp")
                or row.get("order_timestamp")
                or row.get("exchange_order_timestamp")
                or row.get("exchange_timestamp")
            )
            if (
                symbol == str(position["order_symbol"]).upper()
                and transaction == "SELL"
                and status in {
                    "EXECUTED", "COMPLETED", "COMPLETE", "DELIVERY_AWAITED",
                    "SUCCESS", "FILLED", "TRADED",
                    "PARTIALLY_EXECUTED", "PARTIALLY_FILLED", "CANCELLED",
                }
                and order_time is not None
                and order_time >= entry_time
            ):
                sell_orders.append((order_time, row))
        if len(rows) < 25:
            break

    sell_orders.sort(key=lambda item: item[0])
    fills = []
    for order_time, order in sell_orders:
        order_id = str(order.get("groww_order_id") or order.get("order_id") or "")
        if not order_id:
            continue
        response = api.get_trade_list_for_order(
            groww_order_id=order_id,
            segment=api.SEGMENT_FNO,
            page=0,
            page_size=100,
        )
        rows = _extract_response_rows(response, ("trades", "trade_list", "data", "payload"))
        if rows is None:
            raise RuntimeError(f"Groww returned an unexpected trade-list response for order {order_id}")
        for row in rows:
            try:
                fill_quantity = float(
                    row.get("quantity")
                    or row.get("traded_quantity")
                    or row.get("filled_quantity")
                    or row.get("filled_qty")
                    or row.get("filledQuantity")
                    or 0
                )
                price = float(
                    row.get("price")
                    or row.get("trade_price")
                    or row.get("average_price")
                    or row.get("average_fill_price")
                    or row.get("fill_price")
                    or 0
                )
            except (TypeError, ValueError):
                continue
            if fill_quantity > 0 and price > 0:
                fills.append((fill_quantity, price, order_time))

    fills.sort(key=lambda item: item[2])
    remaining = int(requested_quantity)
    skip_quantity = int(previously_closed)
    notional = 0.0
    for fill_quantity, price, _ in fills:
        skipped = min(skip_quantity, fill_quantity)
        skip_quantity -= skipped
        available_quantity = fill_quantity - skipped
        matched = min(remaining, available_quantity)
        notional += matched * price
        remaining -= matched
        if remaining <= 0:
            break
    if remaining:
        found_quantity = int(requested_quantity) - remaining
        raise RuntimeError(
            f"Found manual SELL fills for {found_quantity:g} of {int(requested_quantity)} new shares; "
            "waiting for complete execution details"
        )
    return notional / int(requested_quantity)


def _margin_available(api):
    response = api.get_available_margin_details()
    if not isinstance(response, dict):
        raise RuntimeError("Groww returned an unexpected margin response")
    details = response.get("fno_margin_details")
    if not isinstance(details, dict):
        raise RuntimeError("Groww response did not contain F&O margin details")
    for key in ("future_balance_available", "option_buying_power", "available_margin", "margin_available"):
        try:
            return float(details[key])
        except (KeyError, TypeError, ValueError):
            continue
    raise RuntimeError("Groww F&O margin response did not contain an available balance")


def _place_live_buy(api, config, contract, price):
    available = _margin_available(api)
    budget = min(float(config["allocation_per_trade"]), available)
    lot_size = int(config["lot_size"])
    unit_cost = float(price) * lot_size
    if budget < unit_cost:
        raise RuntimeError(
            f"Allocation/margin {budget:.2f} is below one-lot premium cost {unit_cost:.2f}"
        )
    lots = min(
        int(budget // unit_cost),
        int(config.get("max_lots_per_trade", 1)),
    )
    quantity = lots * lot_size
    response = api.place_order(
        trading_symbol=contract["order_symbol"],
        quantity=quantity,
        validity=api.VALIDITY_DAY,
        exchange=getattr(api, f"EXCHANGE_{config['exchange']}"),
        segment=api.SEGMENT_FNO,
        product=api.PRODUCT_MIS,
        order_type=api.ORDER_TYPE_MARKET,
        transaction_type=api.TRANSACTION_TYPE_BUY,
    )
    if not isinstance(response, dict):
        raise RuntimeError(f"Groww returned an unexpected order response: {response!r}")
    status = str(response.get("order_status", "UNKNOWN")).upper()
    if status in {"REJECTED", "CANCELLED", "FAILED"}:
        raise RuntimeError(f"BUY order {status}: {response.get('message') or response.get('remark') or 'no details'}")
    order_id = response.get("groww_order_id")
    if not order_id:
        raise RuntimeError(f"BUY order has no Groww order ID; status={status}")
    if status not in {"SUCCESS", "EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
        time.sleep(2)
    details = api.get_order_detail(groww_order_id=order_id, segment=api.SEGMENT_FNO)
    final_status = str(details.get("order_status", "UNKNOWN")).upper()
    if final_status not in {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
        raise RuntimeError(f"BUY order {order_id} did not execute; status={final_status}")
    fill_price = float(details.get("average_fill_price") or details.get("price") or price)
    return quantity, fill_price, str(order_id)


def _place_live_sell(api, config, position, price):
    response = api.place_order(
        trading_symbol=position["order_symbol"],
        quantity=int(position["qty"]),
        validity=api.VALIDITY_DAY,
        exchange=getattr(api, f"EXCHANGE_{config['exchange']}"),
        segment=api.SEGMENT_FNO,
        product=api.PRODUCT_MIS,
        order_type=api.ORDER_TYPE_MARKET,
        transaction_type=api.TRANSACTION_TYPE_SELL,
    )
    if not isinstance(response, dict):
        raise RuntimeError(f"Groww returned an unexpected sell response: {response!r}")
    status = str(response.get("order_status", "UNKNOWN")).upper()
    if status in {"REJECTED", "CANCELLED", "FAILED"}:
        raise RuntimeError(f"SELL order {status}: {response.get('message') or response.get('remark') or 'no details'}")
    order_id = response.get("groww_order_id")
    if not order_id:
        raise RuntimeError(f"SELL order has no Groww order ID; status={status}")
    if status not in {"SUCCESS", "EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
        time.sleep(2)
    details = api.get_order_detail(groww_order_id=order_id, segment=api.SEGMENT_FNO)
    final_status = str(details.get("order_status", "UNKNOWN")).upper()
    if final_status not in {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
        raise RuntimeError(f"SELL order {order_id} did not execute; status={final_status}")
    fill_price = float(details.get("average_fill_price") or details.get("price") or price)
    return fill_price, str(order_id)


def _emit_trade_signal(
    config, side, contract, price, reason, placed, quantity=0,
    buy_price=None, sell_price=None, pnl=None, signal_time=None,
):
    signal_time = signal_time or _now_ist(config).strftime("%Y-%m-%dT%H:%M:%S")
    _log(
        config,
        f"[TRADE SIGNAL] Script={config['script_name']} Side={side} Time={signal_time} "
        f"Symbol={contract['candle_symbol']} Strike={contract['strike']} Price={float(price):.2f} "
        f"{f'BuyAt={float(buy_price):.2f} ' if buy_price is not None else ''}"
        f"{f'SellAt={float(sell_price):.2f} ' if sell_price is not None else ''}"
        f"{f'PnL={float(pnl):.2f} ' if pnl is not None else ''}"
        f"Qty={int(quantity)} Reason={reason} OrderPlaced={'YES' if placed else 'NO'}",
    )


def _emit_milestone(config, position, option_price, threshold):
    _log(
        config,
        f"[PRICE MILESTONE] Script={config['script_name']} Basis=OPTION "
        f"Time={_now_ist(config).strftime('%Y-%m-%dT%H:%M:%S')} "
        f"Symbol={position['candle_symbol']} Side={position['side']} "
        f"Entry={float(position['entry_price']):.2f} "
        f"Price={float(option_price):.2f} Move={int(threshold)} Direction=UP",
    )


def _state_path(config):
    return Path(__file__).parent / "logs" / f"{config['script_name']}.state.json"


def _save_live_state(config, position):
    path = _state_path(config)
    path.parent.mkdir(exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    try:
        temporary_path.write_text(json.dumps(position), encoding="utf-8")
        temporary_path.replace(path)
    except OSError as exc:
        _log(config, f"[STATE ERROR] Failed to save position state: {exc}")


def _load_live_state(config):
    path = _state_path(config)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log(config, f"[STATE ERROR] Failed to load position state: {exc}")
        return None
    return state if isinstance(state, dict) else None


def _clear_live_state(config):
    try:
        _state_path(config).unlink(missing_ok=True)
    except OSError as exc:
        _log(config, f"[STATE ERROR] Failed to clear position state: {exc}")


def _wait_for_next_candle(config):
    now = _now_ist(config)
    minutes_to_next_candle = 2 if now.minute % 2 else 1
    seconds = minutes_to_next_candle * 60 - now.second + 2
    time.sleep(max(seconds, 1))


def _entry_candidate(frame, config):
    if len(frame) < int(config["atr_period"]) + 4:
        return None
    _, direction = supertrend(frame, config["atr_period"], config["factor"])
    if len(direction) < 3 or not (
        direction[-3] == -1 and direction[-2] == 1 and direction[-1] == 1
    ):
        return None
    return {
        "price": float(frame["close"].iloc[-1]),
        "signal_time": str(frame["date"].iloc[-1]),
    }


def _log_option_snapshot(config, contract, frame):
    line, direction = supertrend(frame, config["atr_period"], config["factor"])
    candidate = _entry_candidate(frame, config)
    st_value = float(line[-1]) if len(line) and np.isfinite(line[-1]) else None
    st_text = f"{st_value:.2f}" if st_value is not None else "NA"
    _log(
        config,
        f"[OPTION] {contract['candle_symbol']} "
        f"Price={float(frame['close'].iloc[-1]):.2f} "
        f"Open={float(frame['open'].iloc[-1]):.2f} "
        f"Low={float(frame['low'].iloc[-1]):.2f} "
        f"ST={st_text} "
        f"Signal={'BUY' if candidate is not None else 'NO_SIGNAL'} "
        f"Direction={'up' if direction[-1] == 1 else 'down'}",
    )


def _monitor_position(api, config, position, option_frame, dry_run):
    _, direction = supertrend(option_frame, config["atr_period"], config["factor"])
    if len(direction) < 1:
        return False
    option_price = float(option_frame["close"].iloc[-1])
    entry_price = float(position["entry_price"])
    quantity = int(position["qty"])
    unrealized_pnl = (option_price - entry_price) * quantity
    pnl_percent = (
        unrealized_pnl / (entry_price * quantity) * 100
        if entry_price > 0 and quantity > 0
        else 0.0
    )
    mode = (
        "LIVE"
        if config["enable_actual_trade"] and not dry_run and not position.get("paper_only", False)
        else "PAPER"
    )
    _log(
        config,
        f"[POSITION TRACK] Time={_now_ist(config).strftime('%Y-%m-%dT%H:%M:%S')} "
        f"Mode={mode} Status=OPEN Symbol={position['order_symbol']} Side={position['side']} "
        f"Qty={quantity} BuyAt={entry_price:.2f} CurrentPrice={option_price:.2f} "
        f"UnrealizedPnL={unrealized_pnl:.2f} PnLPercent={pnl_percent:.2f}%",
    )
    if direction[-1] == -1:
        if config["enable_actual_trade"] and not dry_run and not position.get("paper_only", False):
            try:
                exit_price, order_id = _place_live_sell(api, config, position, option_price)
            except Exception as exc:
                _log(config, f"[SELL ERROR] {position['order_symbol']}: {type(exc).__name__}: {exc}")
                return False
            position["exit_report"] = {
                "price": exit_price,
                "reason": f"Supertrend flipped down; position closed; OrderID={order_id}",
                "placed": True,
                "time": _now_ist(config).strftime("%Y-%m-%dT%H:%M:%S"),
            }
            if config["enable_actual_trade"] and not dry_run and not position.get("paper_only", False):
                _save_live_state(config, position)
            _emit_trade_signal(
                config,
                "SELL",
                position,
                exit_price,
                position["exit_report"]["reason"],
                True,
                position.get("qty", 0),
                buy_price=position["entry_price"],
                sell_price=exit_price,
                pnl=(exit_price - float(position["entry_price"])) * int(position["qty"]),
                signal_time=position["exit_report"]["time"],
            )
            return True
        position["exit_report"] = {
            "price": option_price,
            "reason": "Supertrend flipped down; paper position closed",
            "placed": False,
            "time": _now_ist(config).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _emit_trade_signal(
            config,
            "SELL",
            position,
            option_price,
            position["exit_report"]["reason"],
            False,
            position.get("qty", 0),
            buy_price=position["entry_price"],
            sell_price=option_price,
            pnl=(option_price - float(position["entry_price"])) * int(position["qty"]),
            signal_time=position["exit_report"]["time"],
        )
        return True
    favorable_move = option_price - float(position["entry_price"])
    step = _milestone_step(config, position["side"])
    threshold = int(position.get("next_milestone", step))
    while favorable_move >= threshold:
        _emit_milestone(config, position, option_price, threshold)
        threshold += step
    if threshold != int(position.get("next_milestone", step)):
        position["next_milestone"] = threshold
        if config["enable_actual_trade"] and not dry_run and not position.get("paper_only", False):
            _save_live_state(config, position)
    return False


def _milestone_step(config, side):
    return int(config["milestone_points_by_side"][side])


def _make_live_position(config, live_record, prior_state=None):
    contract = _contract_from_order(config, live_record["order_symbol"])
    if contract is None:
        return {
            **live_record,
            "candle_symbol": live_record["order_symbol"],
            "side": "CE" if live_record["order_symbol"].endswith("CE") else "PE",
            "strike": 0,
            "next_milestone": _milestone_step(config, "CE" if live_record["order_symbol"].endswith("CE") else "PE"),
            "sell_alert_sent": False,
            "monitorable": False,
        }
    position = {
        **contract,
        **live_record,
        "entry_time": _now_ist(config).isoformat(),
        "initial_qty": int(live_record["qty"]),
        "manual_exit_qty": 0,
        "unreconciled_manual_exit_qty": 0,
        "next_milestone": _milestone_step(config, contract["side"]),
        "sell_alert_sent": False,
        "monitorable": True,
    }
    if prior_state and prior_state.get("order_symbol") == live_record["order_symbol"]:
        for key in (
            "entry_index_price",
            "entry_time",
            "entry_price",
            "initial_qty",
            "manual_exit_qty",
            "unreconciled_manual_exit_qty",
            "next_milestone",
            "sell_alert_sent",
        ):
            if key in prior_state:
                position[key] = prior_state[key]
    return position


def _record_manual_exit(api, config, position, quantity=None):
    quantity = int(quantity if quantity is not None else position["qty"])
    previously_closed = int(position.get("manual_exit_qty", 0))
    if quantity <= 0:
        return False
    try:
        exit_price = _manual_exit_fill(
            api, config, position, quantity, previously_closed=previously_closed
        )
    except Exception as exc:
        _log(
            config,
            f"[MANUAL EXIT RECONCILIATION ERROR] {position['order_symbol']}: "
            f"{type(exc).__name__}: {exc}",
        )
        return False
    pnl = (exit_price - float(position["entry_price"])) * quantity
    position["manual_exit_qty"] = previously_closed + quantity
    position["last_manual_exit_price"] = exit_price
    position["last_manual_exit_time"] = _now_ist(config).strftime("%Y-%m-%dT%H:%M:%S")
    _save_live_state(config, position)
    _log(
        config,
        f"[MANUAL EXIT DETECTED] Symbol={position['order_symbol']} Qty={quantity} "
        f"BuyAt={float(position['entry_price']):.2f} SellAt={exit_price:.2f} "
        f"PnL={pnl:.2f}",
    )
    _emit_trade_signal(
        config,
        "SELL",
        position,
        exit_price,
        "Manual exit detected and matched to Groww SELL fills",
        True,
        quantity,
        buy_price=position["entry_price"],
        sell_price=exit_price,
        pnl=pnl,
        signal_time=position["last_manual_exit_time"],
    )
    return True


def run_strategy(config, dry_run=False):
    api_key = os.getenv("GROWW_API_KEY", "").strip()
    api_secret = os.getenv("GROWW_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("Set GROWW_API_KEY and GROWW_API_SECRET before starting the strategy")
    token = _normalize_access_token(GrowwAPI.get_access_token(api_key=api_key, secret=api_secret))
    api = GrowwAPI(token)

    _, expiry, order_expiry = _nearest_expiry_pair(config, _now_ist(config))
    _log(
        config,
        f"[STARTED] {config['script_name']} interval=2M expiry={expiry} "
        f"mode={'PAPER' if dry_run or not config['enable_actual_trade'] else 'LIVE'} "
        f"allocation={float(config['allocation_per_trade']):.2f}",
    )
    if config["enable_actual_trade"] and not dry_run:
        _log(config, "[LIVE TRADING ENABLED] Market BUY orders may be submitted")

    active_position = None
    last_index_candle = None
    last_block_warning = None
    last_unknown_position_warning = None
    pending_state = _load_live_state(config) if config["enable_actual_trade"] and not dry_run else None

    while True:
        now = _now_ist(config)
        start_time = datetime.strptime(config["entry_window_start"], "%H:%M").time()
        entry_end_time = datetime.strptime(config["entry_window_end"], "%H:%M").time()
        run_end_time = datetime.strptime(config["run_end_time"], "%H:%M").time()
        if now.time() >= run_end_time:
            position_to_reconcile = active_position or pending_state
            if position_to_reconcile and config["enable_actual_trade"] and not dry_run:
                try:
                    final_positions = _get_live_positions(api, config)
                except Exception as exc:
                    _log(config, f"[STOP RECONCILIATION ERROR] {type(exc).__name__}: {exc}")
                    final_positions = None
                final_matching = next(
                    (
                        item for item in final_positions or []
                        if item["order_symbol"] == position_to_reconcile["order_symbol"]
                    ),
                    None,
                )
                if final_matching is not None:
                    old_quantity = int(position_to_reconcile["qty"])
                    final_quantity = int(final_matching["qty"])
                    if final_quantity < old_quantity:
                        closed_quantity = old_quantity - final_quantity
                        if not _record_manual_exit(
                            api, config, position_to_reconcile, quantity=closed_quantity
                        ):
                            position_to_reconcile["unreconciled_manual_exit_qty"] = (
                                int(position_to_reconcile.get("unreconciled_manual_exit_qty", 0))
                                + closed_quantity
                            )
                        position_to_reconcile["qty"] = final_quantity
                    pending_quantity = int(
                        position_to_reconcile.get("unreconciled_manual_exit_qty", 0)
                    )
                    if pending_quantity and _record_manual_exit(
                        api, config, position_to_reconcile, quantity=pending_quantity
                    ):
                        position_to_reconcile["unreconciled_manual_exit_qty"] = 0
                    _save_live_state(config, position_to_reconcile)
                still_open = final_positions is None or final_matching is not None
                if not still_open and _record_manual_exit(
                    api,
                    config,
                    position_to_reconcile,
                    quantity=(
                        int(position_to_reconcile["qty"])
                        + int(position_to_reconcile.get("unreconciled_manual_exit_qty", 0))
                    ),
                ):
                    _clear_live_state(config)
                    active_position = None
                    pending_state = None
                elif still_open:
                    _log(
                        config,
                        f"[STOPPING WITH OPEN POSITION] {position_to_reconcile['order_symbol']} "
                        f"Qty={position_to_reconcile['qty']}; "
                        "position remains open and is not automatically squared off",
                    )
            elif active_position:
                _log(config, f"[STOPPING WITH OPEN PAPER POSITION] {active_position['order_symbol']}")
            _log(config, "[STOPPED] End of configured run window")
            return
        if now.time() < start_time:
            time.sleep(min(30, max(1, (datetime.combine(now.date(), start_time, tzinfo=now.tzinfo) - now).total_seconds())))
            continue

        try:
            index_frame = _fetch_candles(api, config, config["index_symbol"], "SEGMENT_CASH")
            if len(index_frame) < int(config["atr_period"]) + 5:
                raise RuntimeError(f"Insufficient index candles: {len(index_frame)}")
            closed_index = index_frame.iloc[:-1].reset_index(drop=True)
            if len(closed_index) < int(config["atr_period"]) + 4:
                raise RuntimeError(f"Insufficient closed index candles: {len(closed_index)}")
            candle_time = str(closed_index["date"].iloc[-1])
            if candle_time == last_index_candle:
                _wait_for_next_candle(config)
                continue
            last_index_candle = candle_time
            index_price = float(closed_index["close"].iloc[-1])
            _log(config, f"[INDEX] {candle_time} {config['index_symbol']}={index_price:.2f}")

            if config["enable_actual_trade"] and not dry_run:
                live_positions = _get_live_positions(api, config)
                if len(live_positions) > 1:
                    _log(config, f"[POSITION WARNING] Found {len(live_positions)} open {config['index_symbol']} positions; new entries blocked")
                tracked_position = active_position or pending_state
                matching = next(
                    (
                        item for item in live_positions
                        if tracked_position
                        and item["order_symbol"] == tracked_position["order_symbol"]
                    ),
                    None,
                )
                if tracked_position is None and live_positions:
                    matching = live_positions[0]
                if matching is not None:
                    if active_position is None or active_position.get("order_symbol") != matching["order_symbol"]:
                        active_position = _make_live_position(config, matching, pending_state)
                        pending_state = None
                        _save_live_state(config, active_position)
                        _log(config, f"[OPEN POSITION DETECTED] {matching['order_symbol']} Qty={matching['qty']}")
                    else:
                        old_quantity = int(active_position["qty"])
                        new_quantity = int(matching["qty"])
                        if new_quantity < old_quantity:
                            closed_quantity = old_quantity - new_quantity
                            if _record_manual_exit(
                                api, config, active_position, quantity=closed_quantity
                            ):
                                active_position["qty"] = new_quantity
                                _save_live_state(config, active_position)
                            else:
                                active_position["qty"] = new_quantity
                                active_position["unreconciled_manual_exit_qty"] = (
                                    int(active_position.get("unreconciled_manual_exit_qty", 0))
                                    + closed_quantity
                                )
                                _save_live_state(config, active_position)
                        elif int(active_position.get("unreconciled_manual_exit_qty", 0)) > 0:
                            pending_quantity = int(active_position["unreconciled_manual_exit_qty"])
                            if _record_manual_exit(
                                api, config, active_position, quantity=pending_quantity
                            ):
                                active_position["unreconciled_manual_exit_qty"] = 0
                                _save_live_state(config, active_position)
                        elif new_quantity > old_quantity:
                            added_quantity = new_quantity - old_quantity
                            old_cost = float(active_position["entry_price"]) * old_quantity
                            new_cost = float(matching["entry_price"]) * new_quantity
                            added_price = max(0.0, (new_cost - old_cost) / added_quantity)
                            _log(
                                config,
                                f"[POSITION STATUS CHANGE] Symbol={matching['order_symbol']} "
                                f"Action=MANUAL_ADD OldQty={old_quantity} NewQty={new_quantity} "
                                f"AddedQty={added_quantity} EstimatedBuyAt={added_price:.2f}",
                            )
                            _emit_trade_signal(
                                config,
                                "BUY",
                                active_position,
                                added_price,
                                "Manual position increase detected from Groww positions",
                                True,
                                added_quantity,
                            )
                            active_position["qty"] = new_quantity
                            active_position["entry_price"] = matching["entry_price"]
                            active_position["initial_qty"] = (
                                int(active_position.get("initial_qty", old_quantity))
                                + added_quantity
                            )
                            _save_live_state(config, active_position)
                elif active_position is not None:
                    quantity_to_reconcile = (
                        int(active_position["qty"])
                        + int(active_position.get("unreconciled_manual_exit_qty", 0))
                    )
                    if _record_manual_exit(
                        api, config, active_position, quantity=quantity_to_reconcile
                    ):
                        active_position = None
                        pending_state = None
                        _clear_live_state(config)
                        last_block_warning = None
                elif pending_state is not None:
                    quantity_to_reconcile = (
                        int(pending_state["qty"])
                        + int(pending_state.get("unreconciled_manual_exit_qty", 0))
                    )
                    if _record_manual_exit(
                        api, config, pending_state, quantity=quantity_to_reconcile
                    ):
                        pending_state = None
                        _clear_live_state(config)

            atm_by_side = {
                side: _atm_strike(index_price, int(config["strike_step"]), side)
                for side in ("CE", "PE")
            }
            contracts = {
                side: _make_contract(config, expiry, order_expiry, strike, side)
                for side, strike in atm_by_side.items()
            }
            option_frames = {}
            for side, contract in contracts.items():
                try:
                    raw_option_frame = _fetch_candles(
                        api, config, contract["candle_symbol"], "SEGMENT_FNO"
                    )
                except Exception as exc:
                    _log(
                        config,
                        f"[OPTION ERROR] Side={side} Symbol={contract['candle_symbol']} "
                        f"Type={type(exc).__name__} Message={exc}",
                    )
                    continue
                if len(raw_option_frame) < int(config["atr_period"]) + 5:
                    _log(
                        config,
                        f"[OPTION] {contract['candle_symbol']} Price=NA Open=NA Low=NA "
                        f"ST=NA Signal=NO_SIGNAL Direction=NA "
                        f"CandleCount={len(raw_option_frame)} "
                        f"Reason=insufficient_candles Required={int(config['atr_period']) + 5}",
                    )
                    continue
                frame = raw_option_frame.iloc[:-1].reset_index(drop=True)
                option_frames[side] = frame
                _log_option_snapshot(config, contract, frame)

            if active_position and active_position.get("monitorable"):
                side = active_position["side"]
                frame = option_frames.get(side)
                if frame is None or active_position["candle_symbol"] != contracts[side]["candle_symbol"]:
                    raw = _fetch_candles(api, config, active_position["candle_symbol"], "SEGMENT_FNO")
                    frame = raw.iloc[:-1].reset_index(drop=True)
                if len(frame) >= int(config["atr_period"]) + 2:
                    closed = _monitor_position(api, config, active_position, frame, dry_run)
                    if closed:
                        active_position = None
                        pending_state = None
                        if config["enable_actual_trade"] and not dry_run:
                            _clear_live_state(config)
                else:
                    _log(
                        config,
                        f"[POSITION TRACK UNAVAILABLE] Symbol={active_position['order_symbol']} "
                        f"Reason=insufficient closed option candles ({len(frame)})",
                    )
            elif active_position and not active_position.get("monitorable"):
                if last_unknown_position_warning != active_position["order_symbol"]:
                    _log(config, f"[POSITION WARNING] Cannot map {active_position['order_symbol']} to configured expiry; new buys blocked")
                    last_unknown_position_warning = active_position["order_symbol"]

            if active_position and any(
                _entry_candidate(frame, config) is not None for frame in option_frames.values()
            ):
                if last_block_warning != active_position["order_symbol"]:
                    _log(config, f"[BUY BLOCKED] {active_position['order_symbol']} remains open; manual exit required; opposite signal discarded")
                    last_block_warning = active_position["order_symbol"]

            allow_entries = now.time() < entry_end_time
            if not active_position and allow_entries and (
                not (config["enable_actual_trade"] and not dry_run and live_positions)
            ):
                selected = None
                for side in ("CE", "PE"):
                    frame = option_frames.get(side)
                    candidate = _entry_candidate(frame, config) if frame is not None else None
                    if candidate is not None:
                        selected = (contracts[side], frame, candidate)
                        break
                if selected:
                    contract, frame, candidate = selected
                    price = candidate["price"]
                    paper = dry_run or not config["enable_actual_trade"]
                    if paper:
                        lot_size = int(config["lot_size"])
                        unit_cost = price * lot_size
                        budget = float(config["allocation_per_trade"])
                        if budget < unit_cost:
                            _log(
                                config,
                                f"[BUY BLOCKED] {contract['candle_symbol']}: allocation "
                                f"{budget:.2f} is below one-lot premium cost {unit_cost:.2f}",
                            )
                            _wait_for_next_candle(config)
                            continue
                        lots = min(
                            int(budget // unit_cost),
                            int(config.get("max_lots_per_trade", 1)),
                        )
                        quantity = lots * lot_size
                        fill_price, order_id, order_placed = price, None, False
                    else:
                        quantity, fill_price, order_id = _place_live_buy(api, config, contract, price)
                        order_placed = True
                    active_position = {
                        **contract,
                        "qty": quantity,
                        "entry_price": fill_price,
                        "next_milestone": _milestone_step(config, contract["side"]),
                        "entry_time": _now_ist(config).isoformat(),
                        "sell_alert_sent": False,
                        "monitorable": True,
                        "paper_only": paper,
                    }
                    if not paper:
                        active_position["initial_qty"] = quantity
                        active_position["manual_exit_qty"] = 0
                        active_position["unreconciled_manual_exit_qty"] = 0
                        _save_live_state(config, active_position)
                    _emit_trade_signal(
                        config,
                        "BUY",
                        contract,
                        fill_price,
                        "2-minute option Supertrend BUY confirmed on two closed candles"
                        + ("; PAPER" if paper else f"; OrderID={order_id}"),
                        order_placed,
                        quantity,
                    )

        except Exception as exc:
            _log(config, f"[ERROR] {type(exc).__name__}: {exc}")
        _wait_for_next_candle(config)


def _apply_scheduler_overrides(config):
    config_path = Path(__file__).with_name("scheduler_config.json")
    try:
        scheduler_config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load strategy settings from {config_path}: {exc}") from exc

    script_settings = scheduler_config.get("scripts", {}).get(config["script_name"])
    if not isinstance(script_settings, dict):
        raise RuntimeError(f"Missing scripts.{config['script_name']} settings in {config_path}")

    configurable_keys = (
        "atr_period",
        "factor",
        "strike_step",
        "lot_size",
        "expiry_fs",
        "order_expiry",
        "entry_window_start",
        "entry_window_end",
        "run_end_time",
        "milestone_points",
        "milestone_points_by_side",
        "enable_actual_trade",
        "api_rate_limit_retry_delay",
        "api_rate_limit_max_retries",
    )
    config.update({key: script_settings[key] for key in configurable_keys if key in script_settings})

    for key in ("atr_period", "strike_step", "lot_size", "max_lots_per_trade"):
        if not isinstance(config[key], int) or isinstance(config[key], bool) or config[key] < 1:
            raise ValueError(f"{key} must be a positive integer in scripts.{config['script_name']}")
    for key in ("factor", "allocation_per_trade"):
        if not isinstance(config[key], (int, float)) or isinstance(config[key], bool) or config[key] <= 0:
            raise ValueError(f"{key} must be a positive number in scripts.{config['script_name']}")
    if not isinstance(config["enable_actual_trade"], bool):
        raise ValueError(f"enable_actual_trade must be true or false in scripts.{config['script_name']}")
    if (
        not isinstance(config["milestone_points_by_side"], dict)
        or any(
            not isinstance(config["milestone_points_by_side"].get(side), int)
            or config["milestone_points_by_side"][side] < 1
            for side in ("CE", "PE")
        )
    ):
        raise ValueError(
            f"milestone_points_by_side must define positive integer CE and PE values "
            f"in scripts.{config['script_name']}"
        )
    if (
        not isinstance(config["expiry_fs"], list)
        or not isinstance(config["order_expiry"], list)
        or not config["expiry_fs"]
        or len(config["expiry_fs"]) != len(config["order_expiry"])
    ):
        raise ValueError(f"expiry_fs and order_expiry must be non-empty matching lists for {config['script_name']}")
    return config


CONFIG = _apply_scheduler_overrides(CONFIG)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Track paper buys without submitting orders")
    args = parser.parse_args()
    run_strategy(CONFIG, dry_run=args.dry_run)
