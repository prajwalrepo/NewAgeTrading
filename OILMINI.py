# =============================================================================
# OILMINI.PY - Intraday CRUDEOILM option strategy for Groww API
#
# This is a dedicated CRUDEOILM version based on the pattern used in the
# existing NaturalGas scripts, but simplified and organized for intraday use.
#
# Flow:
# 1) Resolve the nearest upcoming expiry from the configured array.
# 2) Select ATM strike using a live/near-live underlying CRUDEOILM price.
# 3) Fetch CE/PE candle data for the selected expiry.
# 4) Run Supertrend on the option candles.
# 5) Buy only on clean flip-up signal + momentum + gap filter.
# 6) Exit on LSL / trailing stop / max-profit logic.
# =============================================================================

from datetime import datetime, time as dt_time, timedelta, timezone
import os
import re
import threading
import time
import warnings

import numpy as np
import pandas as pd
from growwapi import GrowwAPI

warnings.filterwarnings("ignore", category=FutureWarning)


CONFIG = {
    # Fill these before running live.
   "api_key": "eyJraWQiOiJaTUtjVXciLCJhbGciOiJFUzI1NiJ9.eyJleHAiOjI1Njg5NDU3MzIsImlhdCI6MTc4MDU0NTczMiwibmJmIjoxNzgwNTQ1NzMyLCJzdWIiOiJ7XCJ0b2tlblJlZklkXCI6XCI5MjM4NTRkYS0xODFmLTRmNmYtOGFkMi00M2MzMzdlM2ZhZWJcIixcInZlbmRvckludGVncmF0aW9uS2V5XCI6XCJlMzFmZjIzYjA4NmI0MDZjODg3NGIyZjZkODQ5NTMxM1wiLFwidXNlckFjY291bnRJZFwiOlwiY2QxYjY2MjMtY2MzOS00N2Q0LTkxNWYtZmVlZTE3ZjFkNTRmXCIsXCJkZXZpY2VJZFwiOlwiNjA5NWQ4NTYtZGE1My01Y2Q2LWJlZGUtMGFmZWI4NzI1N2U0XCIsXCJzZXNzaW9uSWRcIjpcIjA4MGQ0YjQ0LWJjZWEtNGI3ZC05MjcyLTMxYmJmNDI5MzUwOFwiLFwiYWRkaXRpb25hbERhdGFcIjpcIno1NC9NZzltdjE2WXdmb0gvS0EwYlAveUhIeXlXNVZKSmNyMno5V0JqMlpSTkczdTlLa2pWZDNoWjU1ZStNZERhWXBOVi9UOUxIRmtQejFFQisybTdRPT1cIixcInJvbGVcIjpcImF1dGgtdG90cFwiLFwic291cmNlSXBBZGRyZXNzXCI6XCIxMzYuMjI2LjI1My45MCwxNzIuNjkuMTIyLjE3NCwzNS4yNDEuMjMuMTIzXCIsXCJ0d29GYUV4cGlyeVRzXCI6MjU2ODk0NTczMjkzNSxcInZlbmRvck5hbWVcIjpcImdyb3d3QXBpXCJ9IiwiaXNzIjoiYXBleC1hdXRoLXByb2QtYXBwIn0.5lRo2nlPb4v5xdAp038NbKL7F5vkFFijwMNXrV4vLghxAAWrMQkAZx3CVx8zqIyySd5AQXzHTdT_-lAWB2lJLg",
    "api_secret": "nwS5I3ex3!AB7jIPuOrFLYDXXAq!X&O)",
   

    "commodity_root": "CRUDEOILM",
    "exchange": "MCX",
    "segment": "SEGMENT_COMMODITY",

    # Preferred configuration: pass the expiry windows in arrays and let the script
    # choose the nearest active expiry automatically, as in the NIFTY flow.
    # Future series: exact Groww futures contract token list.
    "future_symbols": [],
    # Option series: keep the expiry list dynamic; do not hardcode a single option symbol.
    "option_symbols": [],

    "future_expiry_fs": [
        "26AUG26",
        "25SEP26",
        "27OCT26",
        "24NOV26",
        "28DEC26",
        "25JAN27",
    ],
    "future_order_expiry": [
        "26AUG",
        "25SEP",
        "27OCT",
        "24NOV",
        "28DEC",
        "25JAN",
    ],
    "option_expiry_fs": [
        "18Sep26",
        "19Oct26",
        "19Nov26",
    ],
    "option_order_expiry": [
        "18SEP",
        "19OCT",
        "19NOV",
    ],
    "expiry_fs": ["18Sep26", "19Oct26", "19Nov26"],
    "order_expiry": ["18SEP", "19OCT", "19NOV"],

    "strike_step": 50,
    "strike_start": 5000,
    "strike_end": 8000,

    "candle_interval": "1M",
    "atr_period": 10,
    "factor": 3.0,

    # Entry quality rules
    "require_close_above_prev_close": True,
    "entry_open_gap_limit_points": 10.0,
    "use_momentum_filter": True,
    "flip_confirmation_window_candles": 1,
    # When enabled, only Supertrend direction flips control entries and exits.
    "only_supertrend": False,

    # Trading schedule in IST
    "trading_timezone_offset_minutes": 330,
    "trading_weekdays": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"],
    "entry_window_start": "17:30",
    "entry_window_end": "22:30",
    "force_exit_time": "22:30",
    "expiry_day_force_exit_time": "22:00",

    # Risk
    "lot_size": 10,
    "max_lots_per_trade": 1,
    "allocation_per_trade": 20000,
    "check_margin_before_order": True,
    "max_trades_per_day_ce": 2,
    "max_trades_per_day_pe": 2,

    # Stops
    # Decimal values are supported, for example 0.1, 0.5, or 1.2.
    "loss_stop_points": 10.0,
    "trailing_stop_enabled": True,
    "trailing_stop_loss_points": 0.5,
    "trailing_stop_trigger_points": 0.5,
    "max_profit_booking_enabled": True,
    "max_profit_booking_points": 3.0,
    "max_profit_check_interval_sec": 60,
    "buy_cooldown_candles": 2,
    "exit_order_cooldown_sec": 90,

    "DEBUG": False,
    "debug_signal_tracking_enabled": True,
}

CANDLE_INTERVAL_MAP = {
    "1M": "1minute", "2M": "2minute", "3M": "3minute",
    "5M": "5minute", "10M": "10minute", "15M": "15minute",
    "30M": "30minute", "1H": "1hour", "4H": "4hour",
    "1D": "1day", "1W": "1week", "1MO": "1month",
}


def _normalize_access_token(token_value):
    if isinstance(token_value, str):
        token = token_value.strip()
        if token:
            return token
        raise ValueError("Empty access token")
    if isinstance(token_value, dict):
        for key in ("access_token", "token", "jwt", "value"):
            val = token_value.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for nested in (token_value.get("data"), token_value.get("payload")):
            if isinstance(nested, dict):
                return _normalize_access_token(nested)
    if isinstance(token_value, (list, tuple)):
        for item in token_value:
            try:
                return _normalize_access_token(item)
            except Exception:
                continue
    raise ValueError(f"Unsupported access token format: {type(token_value).__name__}")


def _extract_candle_rows(raw_response):
    if isinstance(raw_response, dict):
        for key in ("candles", "data", "payload"):
            value = raw_response.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _extract_candle_rows(value)
                if nested is not None:
                    return nested
    elif isinstance(raw_response, list):
        return raw_response
    return None


def _extract_margin_value(raw_response):
    if isinstance(raw_response, dict):
        for key in (
            "available_margin",
            "margin_available",
            "mis_balance_available",
            "future_balance_available",
            "net_margin",
            "balance",
            "available_cash",
        ):
            if key in raw_response:
                try:
                    return float(raw_response[key])
                except (TypeError, ValueError):
                    continue
        for value in raw_response.values():
            nested = _extract_margin_value(value)
            if nested is not None:
                return nested
    elif isinstance(raw_response, list):
        for item in raw_response:
            nested = _extract_margin_value(item)
            if nested is not None:
                return nested
    elif isinstance(raw_response, (int, float, str)):
        try:
            return float(raw_response)
        except (TypeError, ValueError):
            return None
    return None


def _init_groww_api():
    if not CONFIG.get("api_key") or not CONFIG.get("api_secret"):
        print("[WARN] API keys are empty. Fill CONFIG['api_key'] and CONFIG['api_secret'] before live trading.")
        return None
    try:
        raw_access_token = GrowwAPI.get_access_token(
            api_key=CONFIG["api_key"],
            secret=CONFIG["api_secret"],
        )
        access_token = _normalize_access_token(raw_access_token)
        return GrowwAPI(access_token)
    except Exception as exc:
        print(f"[ERROR] Authentication failed: {exc}")
        return None


growwapi = _init_groww_api()


pnl_log_stop_event = threading.Event()
positions = {}
contract_universe = {"CE": [], "PE": []}
order_to_candle = {}
candle_to_order = {}
candle_to_side = {}
order_expiry_to_candle_expiry = {}
trades_today_ce = 0
trades_today_pe = 0
exit_attempt_tracker = {}
side_cooldown_tracker = {"CE": 0, "PE": 0}
last_option_signal_candle = {}
debug_signal_positions = {}
last_max_profit_check_at = {}
loop_candle_cache = {}
loop_price_cache = {}


WEEKDAY_NAME_TO_INT = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


def clear_loop_market_cache():
    loop_candle_cache.clear()
    loop_price_cache.clear()


def _get_cached_candles(cache_key):
    cached = loop_candle_cache.get(cache_key)
    if cached is None:
        return None
    return cached.copy(), None


def _set_cached_candles(cache_key, df):
    if df is not None:
        loop_candle_cache[cache_key] = df.copy()


def _get_cached_price(symbol):
    return loop_price_cache.get(symbol)


def _set_cached_price(symbol, price):
    if price is not None:
        loop_price_cache[symbol] = float(price)


def get_trading_now():
    offset_minutes = int(CONFIG.get("trading_timezone_offset_minutes", 330) or 330)
    return datetime.now(timezone(timedelta(minutes=offset_minutes)))


def _parse_hhmm(value):
    return datetime.strptime(str(value or "00:00").strip(), "%H:%M").time()


def _get_allowed_weekdays():
    configured = CONFIG.get("trading_weekdays", []) or []
    allowed = set()
    for item in configured:
        if isinstance(item, int):
            allowed.add(int(item))
        else:
            mapped = WEEKDAY_NAME_TO_INT.get(str(item).strip().upper())
            if mapped is not None:
                allowed.add(mapped)
    return allowed


def get_selected_option_expiry_date():
    for side in ("CE", "PE"):
        contracts = contract_universe.get(side) or []
        if contracts:
            expiry_dt = _parse_expiry_fs_date(contracts[0].get("expiry_fs"))
            if expiry_dt is not None:
                return expiry_dt.date()
    return None


def get_session_state(now_dt=None):
    now_dt = now_dt or get_trading_now()
    allowed_weekdays = _get_allowed_weekdays()
    if allowed_weekdays and now_dt.weekday() not in allowed_weekdays:
        return "closed_day"

    current_time = dt_time(now_dt.hour, now_dt.minute, now_dt.second)
    entry_start = _parse_hhmm(CONFIG.get("entry_window_start", "17:30"))
    entry_end = _parse_hhmm(CONFIG.get("entry_window_end", "22:30"))
    force_exit = _parse_hhmm(CONFIG.get("force_exit_time", "22:30"))

    expiry_date = get_selected_option_expiry_date()
    if expiry_date is not None and now_dt.date() == expiry_date:
        force_exit = _parse_hhmm(CONFIG.get("expiry_day_force_exit_time", "22:00"))
        if entry_end > force_exit:
            entry_end = force_exit

    if current_time < entry_start:
        return "pre_open"
    if current_time < entry_end:
        return "entry"
    if current_time < force_exit:
        return "manage_only"
    return "squareoff"


def close_all_open_positions(reason):
    for symbol, pos in list(positions.items()):
        if pos.get("status") != "OPEN":
            continue
        exit_price = fetch_latest_price_1m(symbol) or pos.get("entry_price", 0)
        if exit_price is None:
            continue
        place_sell_order(symbol, pos.get("order_symbol"), float(exit_price), reason=reason, qty=pos.get("qty"))

    debug_signal_positions.clear()


def _parse_expiry_fs_date(expiry_fs_text):
    txt = str(expiry_fs_text or "").strip()
    formats = (
        "%d%b%y", "%d%b%Y",
        "%d-%m-%Y", "%d-%m-%y",
        "%d/%m/%Y", "%d/%m/%y",
        "%d-%b-%Y", "%d-%b-%y",
        "%d %b %Y", "%d %b %y",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(txt, fmt)
        except ValueError:
            continue
    return None


def _normalize_expiry_entries(expiry_key="expiry_fs", order_key="order_expiry"):
    expiry_cfg = CONFIG.get(expiry_key)
    order_cfg = CONFIG.get(order_key)
    expiry_list = expiry_cfg if isinstance(expiry_cfg, list) else [expiry_cfg]
    order_list = order_cfg if isinstance(order_cfg, list) else [order_cfg]

    entries = []
    for idx, item in enumerate(expiry_list):
        candle_expiry = str(item or "").strip()
        order_expiry = str(order_list[idx] if idx < len(order_list) else "").strip().upper()
        if not candle_expiry:
            continue
        if not order_expiry:
            order_expiry = candle_expiry.upper()
        expiry_dt = _parse_expiry_fs_date(candle_expiry)
        if expiry_dt is None:
            print(f"[WARN] Invalid expiry format '{candle_expiry}' for CRUDEOILM")
            continue
        entries.append({
            "candle_expiry": candle_expiry,
            "order_expiry": order_expiry,
            "expiry_dt": expiry_dt,
        })
    return entries


def _parse_order_symbol_token(symbol_text, *, kind="future"):
    txt = str(symbol_text or "").strip().upper()
    if not txt:
        return None

    if kind == "future":
        match = re.search(r"([0-9]{2}[A-Z]{3}[0-9]{2})FUT$", txt)
        if match:
            expiry_token = match.group(1)
            dt = datetime.strptime(expiry_token, "%d%b%y")
            return {
                "raw": txt,
                "expiry_token": expiry_token,
                "expiry_dt": dt,
                "symbol_type": "future",
            }
        return None

    match = re.search(r"([0-9]{2}[A-Z]{3}[0-9]{2})(\d+)(CE|PE)$", txt)
    if match:
        expiry_token = match.group(1)
        strike = int(match.group(2))
        side = match.group(3)
        dt = datetime.strptime(expiry_token, "%d%b%y")
        return {
            "raw": txt,
            "expiry_token": expiry_token,
            "expiry_dt": dt,
            "strike": strike,
            "side": side,
            "symbol_type": "option",
        }
    return None


def _normalize_symbol_array(symbols, kind):
    if not symbols:
        return []
    entries = []
    for symbol in symbols:
        parsed = _parse_order_symbol_token(symbol, kind=kind)
        if parsed is None:
            continue
        expiry_token = parsed["expiry_token"]
        expiry_dt = parsed["expiry_dt"]
        candle_expiry = expiry_dt.strftime("%d%b%y")
        candle_expiry = f"{candle_expiry[:2]}{candle_expiry[2:5].title()}{candle_expiry[5:]}"
        entry = {
            "raw": parsed["raw"],
            "expiry_token": expiry_token,
            "expiry_dt": expiry_dt,
            "candle_expiry": candle_expiry,
            "order_expiry": expiry_token,
            "symbol_type": parsed["symbol_type"],
        }
        if parsed["symbol_type"] == "option":
            entry["strike"] = parsed["strike"]
            entry["side"] = parsed["side"]
        entries.append(entry)
    return entries


def _select_nearest_expiry(entries, now_dt=None):
    if not entries:
        return None
    now_dt = now_dt or datetime.now()
    today = now_dt.date()
    upcoming = [e for e in entries if e["expiry_dt"].date() >= today]
    pool = upcoming if upcoming else entries
    return min(pool, key=lambda e: abs((e["expiry_dt"].date() - today).days))


def build_candle_symbol(expiry_fs, strike, side):
    return f"{str(CONFIG['commodity_root']).upper()}-{expiry_fs}-{int(strike)}-{str(side).upper()}"


def build_future_candle_symbol(expiry_fs):
    token = str(expiry_fs or "").strip()
    if token.upper().endswith("FUT"):
        token = token[:-3]
    if re.search(r"^[0-9]{2}[A-Z]{3}[0-9]{2}$", token.upper()):
        day = token[:2]
        mon = token[2:5]
        yr = token[5:7]
        return f"MCX-{str(CONFIG['commodity_root']).upper()}-{day}{mon.title()}{yr}-FUT"
    if re.search(r"^[0-9]{2}[A-Za-z]{3}[0-9]{2}$", token):
        day = token[:2]
        mon = token[2:5]
        yr = token[5:7]
        return f"MCX-{str(CONFIG['commodity_root']).upper()}-{day}{mon.title()}{yr}-FUT"
    return f"MCX-{str(CONFIG['commodity_root']).upper()}-{token}-FUT"


def build_future_order_symbol(order_expiry_text):
    token = str(order_expiry_text or "").strip().upper()
    if token.endswith("FUT"):
        return token
    return f"{str(CONFIG['commodity_root']).upper()}{token}FUT"


def build_order_symbol(order_expiry_text, strike, side):
    token = str(order_expiry_text or "").strip().upper()
    if token.endswith("FUT"):
        return token
    return f"{str(CONFIG['commodity_root']).upper()}{token}{int(strike)}{str(side).upper()}"


def _resolve_segment_constant(segment_name):
    if not segment_name:
        return None
    value = str(segment_name).strip().upper()
    aliases = {
        "COMMODITY": "SEGMENT_COMMODITY",
        "COMM": "SEGMENT_COMMODITY",
        "MCX": "SEGMENT_COMMODITY",
        "FNO": "SEGMENT_FNO",
        "NFO": "SEGMENT_FNO",
        "NSE_FNO": "SEGMENT_FNO",
    }
    value = aliases.get(value, value)
    if not value.startswith("SEGMENT_"):
        value = f"SEGMENT_{value}"
    return getattr(growwapi, value, None) if growwapi is not None else None


def _build_dataframe(candles):
    if not candles:
        return pd.DataFrame()
    n = len(candles[0])
    if n == 7:
        cols = ["date", "open", "high", "low", "close", "volume", "oi"]
    elif n == 6:
        cols = ["date", "open", "high", "low", "close", "volume"]
    else:
        raise ValueError(f"Unexpected candle shape: {n} columns")

    df = pd.DataFrame(candles, columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)


def wilder_rma(series, period):
    result = [np.nan] * len(series)
    values = series.values
    if len(values) < period or period < 1:
        return pd.Series(result, index=series.index)
    sma = np.nanmean(values[:period])
    result[period - 1] = sma
    for i in range(period, len(values)):
        prev = result[i - 1] if not np.isnan(result[i - 1]) else sma
        result[i] = (prev * (period - 1) + values[i]) / period
    return pd.Series(result, index=series.index)


def supertrend(df, atr_period, factor):
    hl2 = (df["high"] + df["low"]) / 2
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    atr = wilder_rma(tr, atr_period)
    upperband = hl2 + factor * atr
    lowerband = hl2 - factor * atr
    st_arr = np.full(len(df), np.nan)
    direction = np.full(len(df), 1)

    for i in range(len(df)):
        if i < atr_period:
            continue
        if i == atr_period:
            if df["close"].iloc[i] > hl2.iloc[i]:
                st_arr[i] = lowerband.iloc[i]
                direction[i] = 1
            else:
                st_arr[i] = upperband.iloc[i]
                direction[i] = -1
        else:
            if df["close"].iloc[i - 1] > st_arr[i - 1]:
                st_arr[i] = max(lowerband.iloc[i], st_arr[i - 1])
                direction[i] = 1
            else:
                st_arr[i] = min(upperband.iloc[i], st_arr[i - 1])
                direction[i] = -1
    return st_arr, direction


def fetch_recent_candles(symbol, segment_name, lookback_minutes=None):
    if growwapi is None:
        raise RuntimeError("GrowwAPI client not initialized. Fill API credentials first.")

    interval_str = str(CONFIG.get("candle_interval") or "1M").upper()
    if lookback_minutes is None:
        min_needed = int(CONFIG.get("atr_period") or 10) + 10
        if "M" in interval_str:
            lookback_minutes = max(120, min_needed * int(interval_str.replace("M", "")))
        elif "H" in interval_str:
            lookback_minutes = max(240, min_needed * int(interval_str.replace("H", "")) * 60)
        else:
            lookback_minutes = 240

    exchange = CONFIG.get("exchange", "MCX")
    groww_symbol = symbol if symbol.startswith(f"{exchange}-") else f"{exchange}-{symbol}"
    end_time = datetime.now()
    segment_const = _resolve_segment_constant(segment_name)
    exchange_const = getattr(growwapi, f"EXCHANGE_{exchange}", None)
    interval_key = CANDLE_INTERVAL_MAP.get(interval_str)
    if exchange_const is None:
        return None, f"Unknown exchange constant: EXCHANGE_{exchange}"
    if segment_const is None:
        return None, f"Unknown segment constant: {segment_name}"
    if interval_key is None:
        return None, f"Unknown candle interval: {interval_str}"

    cache_key = (groww_symbol, segment_name, int(lookback_minutes), interval_key)
    cached_df, cached_err = _get_cached_candles(cache_key)
    if cached_df is not None:
        return cached_df, cached_err

    for lookback in [int(lookback_minutes), max(int(lookback_minutes), 24 * 60), max(int(lookback_minutes), 3 * 24 * 60)]:
        start_time = end_time - timedelta(minutes=lookback)
        try:
            raw = growwapi.get_historical_candles(
                groww_symbol=groww_symbol,
                exchange=exchange_const,
                segment=segment_const,
                candle_interval=interval_key,
                start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
                timeout=int(CONFIG.get("api_timeout_seconds", 10)),
            )
            candles = _extract_candle_rows(raw)
            if candles:
                df = _build_dataframe(candles)
                if df is not None and not df.empty:
                    df = df[df["date"] <= pd.Timestamp(datetime.now())].reset_index(drop=True)
                    if len(df) >= int(CONFIG.get("atr_period") or 10) + 2:
                        _set_cached_candles(cache_key, df)
                        return df, None
        except Exception as exc:
            if "rate limit" in str(exc).lower():
                return None, f"{type(exc).__name__}: {exc}"
            return None, f"{type(exc).__name__}: {exc}"
    return None, "Insufficient commodity candle data"


def fetch_latest_price_1m(symbol):
    if growwapi is None:
        return None
    cached_price = _get_cached_price(symbol)
    if cached_price is not None:
        return cached_price
    exchange = CONFIG.get("exchange", "MCX")
    groww_symbol = symbol if symbol.startswith(f"{exchange}-") else f"{exchange}-{symbol}"
    try:
        start_time = datetime.now() - timedelta(minutes=10)
        raw = growwapi.get_historical_candles(
            groww_symbol=groww_symbol,
            exchange=getattr(growwapi, f"EXCHANGE_{exchange}"),
            segment=_resolve_segment_constant(CONFIG.get("segment", "SEGMENT_COMMODITY")),
            candle_interval="1minute",
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            timeout=int(CONFIG.get("api_timeout_seconds", 10)),
        )
        candles = _extract_candle_rows(raw)
        if candles:
            df = _build_dataframe(candles)
            if not df.empty:
                price = float(df["close"].iloc[-1])
                _set_cached_price(symbol, price)
                return price
    except Exception as exc:
        print(f"[CANDLE ERROR] {symbol}: {type(exc).__name__}: {exc}")
    return None


def build_contract_universe():
    global contract_universe, order_to_candle, candle_to_order, candle_to_side, order_expiry_to_candle_expiry

    contract_universe = {"CE": [], "PE": []}
    order_to_candle = {}
    candle_to_order = {}
    candle_to_side = {}
    order_expiry_to_candle_expiry = {}

    # Prefer the configured expiry arrays and resolve the nearest upcoming expiry,
    # matching the NIFTYFNOInatra pattern. Only fall back to exact raw symbol arrays
    # if no expiry list is supplied.
    future_entries = _normalize_expiry_entries("future_expiry_fs", "future_order_expiry")
    if not future_entries and CONFIG.get("future_symbols"):
        future_entries = _normalize_symbol_array(CONFIG["future_symbols"], "future")

    option_entries = _normalize_expiry_entries("option_expiry_fs", "option_order_expiry")
    if not option_entries and CONFIG.get("option_symbols"):
        option_entries = _normalize_symbol_array(CONFIG["option_symbols"], "option")

    future_selected = _select_nearest_expiry(future_entries) if future_entries else None
    if future_selected is None:
        print("[ERROR] No valid CRUDEOILM futures expiry configured")
        return False

    option_selected = _select_nearest_expiry(option_entries) if option_entries else None
    if option_selected is None:
        print("[ERROR] No valid CRUDEOILM options expiry configured")
        return False

    future_expiry_fs = future_selected.get("candle_expiry", future_selected.get("expiry_token"))
    future_order_expiry = future_selected.get("order_expiry", future_selected.get("expiry_token"))
    expiry_fs = option_selected.get("candle_expiry", option_selected.get("expiry_token"))
    order_expiry = option_selected.get("order_expiry", option_selected.get("expiry_token"))

    order_expiry_to_candle_expiry[order_expiry.upper()] = expiry_fs
    print(f"[FUTURE] Selected CRUDEOILM future: candle={future_expiry_fs}, order={future_order_expiry}")
    print(f"[EXPIRY] Selected CRUDEOILM options: candle={expiry_fs}, order={order_expiry}")

    strikes = list(range(int(CONFIG["strike_start"]), int(CONFIG["strike_end"]) + int(CONFIG["strike_step"]), int(CONFIG["strike_step"])))
    for side in ("CE", "PE"):
        for strike in strikes:
            candle_symbol = build_candle_symbol(expiry_fs, strike, side)
            order_symbol = build_order_symbol(order_expiry, strike, side)
            rec = {
                "candle_symbol": candle_symbol,
                "order_symbol": order_symbol,
                "strike": int(strike),
                "side": side,
                "expiry_fs": expiry_fs,
                "order_expiry": order_expiry,
            }
            contract_universe[side].append(rec)
            order_to_candle[order_symbol] = candle_symbol
            candle_to_order[candle_symbol] = order_symbol
            candle_to_side[candle_symbol] = side
    return True


def select_atm_contract(side, index_price):
    contracts = contract_universe.get(side, [])
    if not contracts:
        return None
    return min(contracts, key=lambda c: (abs(c["strike"] - index_price), -c["strike"]))


def get_contract_for_symbol(candle_symbol, fallback_side=None, fallback_order_symbol=None, fallback_strike=None):
    side = fallback_side or candle_to_side.get(candle_symbol)
    if side in contract_universe:
        for contract in contract_universe.get(side, []):
            if contract.get("candle_symbol") == candle_symbol:
                return contract
    return {
        "candle_symbol": candle_symbol,
        "order_symbol": fallback_order_symbol or candle_to_order.get(candle_symbol),
        "strike": fallback_strike,
        "side": side,
    }


def get_tracked_contracts():
    tracked = []
    seen = set()

    for candle_symbol, pos in positions.items():
        if pos.get("status") != "OPEN" or candle_symbol in seen:
            continue
        tracked.append(
            get_contract_for_symbol(
                candle_symbol,
                fallback_side=pos.get("side"),
                fallback_order_symbol=pos.get("order_symbol"),
                fallback_strike=pos.get("strike"),
            )
        )
        seen.add(candle_symbol)

    if CONFIG.get("debug_signal_tracking_enabled", False):
        for candle_symbol, debug_pos in debug_signal_positions.items():
            if candle_symbol in seen:
                continue
            tracked.append(
                get_contract_for_symbol(
                    candle_symbol,
                    fallback_side=debug_pos.get("side"),
                    fallback_order_symbol=debug_pos.get("order_symbol"),
                    fallback_strike=debug_pos.get("strike"),
                )
            )
            seen.add(candle_symbol)

    return tracked


def get_underlying_reference_price():
    future_entries = _normalize_expiry_entries("future_expiry_fs", "future_order_expiry")
    future_selected = _select_nearest_expiry(future_entries)
    if future_selected is None:
        return None

    # Use the dated futures candle symbol selected at startup. Probing several
    # aliases on every cycle quickly exhausts the Groww request limit.
    future_symbol = build_future_candle_symbol(future_selected["candle_expiry"])
    return fetch_latest_price_1m(future_symbol)


def option_has_flip_buy_signal(candle_symbol, idx_time):
    df, err = fetch_recent_candles(candle_symbol, CONFIG.get("segment", "SEGMENT_COMMODITY"))
    if df is None or df.empty or len(df) < int(CONFIG.get("atr_period") or 10) + 2:
        return False, None, None, None, None, None, "NO_SIGNAL"

    closed_idx = len(df) - 2
    prev_idx = closed_idx - 1
    if prev_idx < 0:
        return False, None, None, None, None, None, "NO_SIGNAL"

    st_arr, dir_arr = supertrend(df, int(CONFIG.get("atr_period") or 10), float(CONFIG.get("factor") or 3.0))
    candle_time = str(df["date"].iloc[closed_idx])

    entry_open = float(df["open"].iloc[closed_idx])
    entry_low = float(df["low"].iloc[closed_idx])
    close_price = float(df["close"].iloc[closed_idx])
    prev_close = float(df["close"].iloc[prev_idx])
    st_value = float(st_arr[closed_idx]) if not np.isnan(st_arr[closed_idx]) else None
    trend = "up" if dir_arr[closed_idx] == 1 else "down"
    fresh_flip = dir_arr[closed_idx] == 1 and dir_arr[prev_idx] == -1
    fresh_flip_down = dir_arr[closed_idx] == -1 and dir_arr[prev_idx] == 1
    is_new_closed_candle = last_option_signal_candle.get(candle_symbol) != candle_time
    if is_new_closed_candle:
        last_option_signal_candle[candle_symbol] = candle_time

    momentum_ok = (
        (close_price > prev_close) if CONFIG.get("require_close_above_prev_close", True) else True
    ) if CONFIG.get("use_momentum_filter", True) else True
    open_gap_ok = abs(entry_open - prev_close) <= float(CONFIG.get("entry_open_gap_limit_points", 10.0))
    if CONFIG.get("only_supertrend", False):
        allow_buy = bool(is_new_closed_candle and fresh_flip)
    else:
        allow_buy = bool(is_new_closed_candle and fresh_flip and momentum_ok and open_gap_ok)
    if allow_buy:
        signal = "BUY"
    elif (
        CONFIG.get("only_supertrend", False)
        and is_new_closed_candle
        and fresh_flip_down
        and (
            positions.get(candle_symbol, {}).get("status") == "OPEN"
            or (
                CONFIG.get("debug_signal_tracking_enabled", False)
                and candle_symbol in debug_signal_positions
            )
        )
    ):
        signal = "SELL"
    else:
        signal = "NO_SIGNAL"
    return allow_buy, close_price, entry_open, entry_low, st_value, trend, signal


def print_future_snapshot(future_symbol, idx_time=None):
    df, err = fetch_recent_candles(future_symbol, CONFIG.get("segment", "SEGMENT_COMMODITY"))
    if df is None or df.empty or len(df) < int(CONFIG.get("atr_period") or 10) + 2:
        if err:
            print(f"[CANDLE DATA] {future_symbol}: {err}")
        print(f"[{idx_time or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [FUT] {future_symbol} Price=NA ST=NA Direction=NA")
        return {"ok": False, "candle_symbol": future_symbol, "price": None, "st_value": None, "direction": "NA"}

    st_arr, dir_arr = supertrend(df, int(CONFIG.get("atr_period") or 10), float(CONFIG.get("factor") or 3.0))
    last_idx = len(df) - 1
    if last_idx < 0:
        print(f"[{idx_time or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [FUT] {future_symbol} Price=NA ST=NA Direction=NA")
        return {"ok": False, "candle_symbol": future_symbol, "price": None, "st_value": None, "direction": "NA"}

    candle_time = str(df["date"].iloc[last_idx])
    price = float(df["close"].iloc[last_idx])
    st_value = float(st_arr[last_idx]) if not np.isnan(st_arr[last_idx]) else None
    direction = "up" if dir_arr[last_idx] == 1 else "down"
    st_text = f"{st_value:.2f}" if st_value is not None else "NA"
    print(f"[FUT] {candle_time} {future_symbol} Price={price:.2f} ST={st_text} Direction={direction}")
    return {"ok": True, "candle_symbol": future_symbol, "price": price, "st_value": st_value, "direction": direction, "candle_time": candle_time}


def print_flat_market_snapshot(candle_symbol, side, idx_time, strike):
    ok, last_price, entry_open, entry_low, st_value, opt_trend, signal = option_has_flip_buy_signal(candle_symbol, idx_time)
    if last_price is None:
        print(f"[CANDLE DATA] {candle_symbol}: no usable candle data")
        print(f"[OPTION] {candle_symbol} Price=NA ST=NA Signal={signal} Direction=NA")
        return {"ok": ok, "last_price": None, "entry_open": entry_open, "entry_low": entry_low, "st_value": st_value, "opt_trend": opt_trend, "signal": signal, "candle_symbol": candle_symbol, "side": side, "strike": strike}

    st_text = f"{st_value:.2f}" if st_value is not None else "NA"
    dir_text = opt_trend if opt_trend is not None else "NA"
    open_text = f"{entry_open:.2f}" if entry_open is not None else "NA"
    low_text = f"{entry_low:.2f}" if entry_low is not None else "NA"
    print(f"[OPTION] {candle_symbol} Price={last_price:.2f} Open={open_text} Low={low_text} ST={st_text} Signal={signal} Direction={dir_text}")
    return {"ok": ok, "last_price": last_price, "entry_open": entry_open, "entry_low": entry_low, "st_value": st_value, "opt_trend": opt_trend, "signal": signal, "candle_symbol": candle_symbol, "side": side, "strike": strike}


def get_trade_limit(side):
    max_trades = int(CONFIG.get("max_trades_per_day_ce", 2) if side == "CE" else CONFIG.get("max_trades_per_day_pe", 2))
    return max_trades


def check_trade_limit(side):
    global trades_today_ce, trades_today_pe
    if side == "CE":
        return trades_today_ce < int(CONFIG.get("max_trades_per_day_ce", 2)), trades_today_ce, int(CONFIG.get("max_trades_per_day_ce", 2))
    return trades_today_pe < int(CONFIG.get("max_trades_per_day_pe", 2)), trades_today_pe, int(CONFIG.get("max_trades_per_day_pe", 2))


def increment_trade_count(side):
    global trades_today_ce, trades_today_pe
    if side == "CE":
        trades_today_ce += 1
    elif side == "PE":
        trades_today_pe += 1


def update_cooldowns_once_per_cycle():
    for side in side_cooldown_tracker:
        side_cooldown_tracker[side] = max(0, int(side_cooldown_tracker[side]) - 1)


def get_available_margin():
    if growwapi is None:
        return None
    try:
        resp = growwapi.get_available_margin_details()
        if not isinstance(resp, dict):
            return None
        margin_value = _extract_margin_value(resp)
        if margin_value is not None:
            return margin_value
    except Exception as exc:
        print(f"[ERROR] Failed to fetch margin: {type(exc).__name__}: {exc}")
    return None


def place_order_with_retry(trading_symbol, quantity, transaction_type, product_type=None):
    if growwapi is None:
        raise RuntimeError("GrowwAPI has not been initialized")
    if product_type is None:
        product_type = growwapi.PRODUCT_MIS

    while True:
        try:
            return growwapi.place_order(
                trading_symbol=trading_symbol,
                quantity=int(quantity),
                validity=growwapi.VALIDITY_DAY,
                exchange=getattr(growwapi, f"EXCHANGE_{CONFIG['exchange']}"),
                segment=getattr(growwapi, CONFIG.get("segment", "SEGMENT_COMMODITY")),
                product=product_type,
                order_type=growwapi.ORDER_TYPE_MARKET,
                transaction_type=transaction_type,
            )
        except Exception as exc:
            if "rate limit" in str(exc).lower():
                time.sleep(int(CONFIG.get("api_rate_limit_retry_delay", 5)))
                continue
            raise


def verify_order_execution(order_id, symbol, fallback_price=None):
    if growwapi is None:
        return None, None, None
    try:
        details = growwapi.get_order_detail(groww_order_id=order_id, segment=getattr(growwapi, CONFIG.get("segment", "SEGMENT_COMMODITY")))
        final_status = details.get("order_status", "UNKNOWN")
        avg_price = details.get("average_fill_price") or details.get("price")
        avg_price = float(avg_price) if avg_price else (fallback_price or 0)
        return final_status, avg_price, details
    except Exception:
        return None, None, None


def place_buy_order(candle_symbol, order_symbol, price, entry_candle_open, entry_candle_low=None):
    try:
        side = candle_to_side.get(candle_symbol, "CE")
        allowed, cnt, max_allowed = check_trade_limit(side)
        if not allowed:
            print(f"[BLOCKED] Trade limit {side}: {cnt}/{max_allowed}")
            return None

        if side_cooldown_tracker.get(side, 0) > 0:
            return None

        lot_size = int(CONFIG.get("lot_size", 1) or 1)
        max_lots = int(CONFIG.get("max_lots_per_trade", 0) or 0)
        margin = get_available_margin()
        if CONFIG.get("check_margin_before_order", True) and margin is None:
            print("[SKIP] Margin unavailable")
            return None

        allocation = float(CONFIG.get("allocation_per_trade", 0) or 0)
        available_margin = min(allocation, margin) if margin is not None else allocation
        min_cost = lot_size * price
        if available_margin < min_cost:
            print(
                f"[BUY BLOCKED] {candle_symbol}: insufficient margin. "
                f"Need {min_cost:.2f}, available {available_margin:.2f}"
            )
            return None

        qty = max(int(available_margin / (lot_size * price)), 1) * lot_size
        if max_lots > 0:
            qty = min(int(qty), max_lots * lot_size)

        resp = place_order_with_retry(order_symbol, qty, growwapi.TRANSACTION_TYPE_BUY, growwapi.PRODUCT_MIS)
        order_id = resp.get("groww_order_id")
        status = str(resp.get("order_status", "UNKNOWN")).upper()
        if status in {"REJECTED", "CANCELLED", "FAILED"}:
            print(f"[REJECTED] BUY {candle_symbol}")
            return None
        if status in {"NEW", "PENDING", "OPEN"}:
            time.sleep(2)

        final_status, avg_price, _ = verify_order_execution(order_id, candle_symbol, fallback_price=price)
        if final_status in {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
            exec_price = avg_price if avg_price and avg_price > 0 else price
            candle_low = float(entry_candle_low if entry_candle_low is not None else entry_candle_open)
            positions[candle_symbol] = {
                "status": "OPEN",
                "entry_price": exec_price,
                "entry_candle_open": float(entry_candle_open),
                "entry_candle_low": candle_low,
                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "qty": int(qty),
                "order_symbol": order_symbol,
                "product_type": "MIS",
                "side": side,
                "highest_price": exec_price,
                "trailing_sl_activated": False,
            }
            increment_trade_count(side)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [BUY CONFIRMED] {candle_symbol} Qty={int(qty)} Price={exec_price:.2f} EntryLow={candle_low:.2f}")
            return order_id
        return None
    except Exception as exc:
        print(f"[ERROR] BUY {candle_symbol}: {exc}")
        return None


def check_lsl_exit(symbol):
    if symbol not in positions or positions[symbol].get("status") != "OPEN":
        return False
    pos = positions[symbol]
    entry_low = float(pos.get("entry_candle_low", pos.get("entry_candle_open", pos.get("entry_price", 0))) or 0)
    if entry_low <= 0:
        return False
    cur = fetch_latest_price_1m(symbol)
    if cur is None:
        return False
    loss_stop_points = float(CONFIG.get("loss_stop_points", 0) or 0)
    return cur <= (entry_low + loss_stop_points)


def _update_trailing_stop_for_record(pos, current_price):
    entry = float(pos.get("entry_price", 0) or 0)
    if entry <= 0:
        return False

    stop_gap = float(CONFIG.get("trailing_stop_loss_points", 8) or 8)
    trigger_points = float(CONFIG.get("trailing_stop_trigger_points", stop_gap) or stop_gap)
    highest = float(pos.get("highest_price", entry) or entry)
    if current_price > highest:
        highest = float(current_price)
        pos["highest_price"] = highest

    if not pos.get("trailing_sl_activated", False) and (highest - entry) >= trigger_points:
        pos["trailing_sl_activated"] = True
        pos["trailing_stop_price"] = highest - stop_gap

    if pos.get("trailing_sl_activated", False):
        current_tsl = float(pos.get("trailing_stop_price", entry - stop_gap))
        new_tsl = max(current_tsl, highest - stop_gap)
        pos["trailing_stop_price"] = new_tsl
        if current_price <= new_tsl:
            return True
    return False


def update_trailing_stop_and_check(symbol, current_price):
    if symbol not in positions or positions[symbol].get("status") != "OPEN":
        return False
    return _update_trailing_stop_for_record(positions[symbol], current_price)


def update_debug_trailing_stop_and_check(symbol, current_price):
    pos = debug_signal_positions.get(symbol)
    if not pos:
        return False
    return _update_trailing_stop_for_record(pos, current_price)


def place_sell_order(candle_symbol, order_symbol, price, reason="Manual", qty=None):
    try:
        cooldown_sec = float(CONFIG.get("exit_order_cooldown_sec", 90) or 0)
        now_ts = datetime.now()
        last_exit = exit_attempt_tracker.get(candle_symbol)
        if last_exit and (now_ts - last_exit).total_seconds() < cooldown_sec:
            return None

        if candle_symbol not in positions or positions[candle_symbol].get("status") != "OPEN":
            return None

        live_qty = int(positions[candle_symbol].get("qty", 0))
        qty_units = live_qty if qty is None else min(int(qty), live_qty)
        if qty_units <= 0:
            return None

        exit_attempt_tracker[candle_symbol] = now_ts
        resp = place_order_with_retry(order_symbol, qty_units, growwapi.TRANSACTION_TYPE_SELL, growwapi.PRODUCT_MIS)
        order_id = resp.get("groww_order_id")
        status = str(resp.get("order_status", "UNKNOWN")).upper()
        if status in {"REJECTED", "CANCELLED", "FAILED"}:
            print(f"[ORDER REJECTED] SELL {candle_symbol} | Status={status}")
            exit_attempt_tracker.pop(candle_symbol, None)
            return None
        if status in {"NEW", "PENDING", "OPEN"}:
            time.sleep(2)

        final_status, avg_price, _ = verify_order_execution(order_id, candle_symbol, fallback_price=price)
        if final_status in {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
            exec_price = avg_price if avg_price and avg_price > 0 else price
            entry_price = float(positions[candle_symbol].get("entry_price", exec_price))
            pnl = (exec_price - entry_price) * qty_units
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [EXIT CONFIRMED] {candle_symbol} x{qty_units} | Reason={reason} | PnL {pnl:.0f}")
            positions[candle_symbol]["status"] = "CLOSED"
            positions[candle_symbol]["exit_price"] = exec_price
            positions[candle_symbol]["qty"] = 0
            side = candle_to_side.get(candle_symbol)
            if side in side_cooldown_tracker:
                side_cooldown_tracker[side] = int(CONFIG.get("buy_cooldown_candles", 0) or 0)
            return order_id
        return None
    except Exception as exc:
        exit_attempt_tracker.pop(candle_symbol, None)
        print(f"[ERROR] SELL {candle_symbol}: {exc}")
        return None


def live_signal_loop():
    last_printed_time = None
    last_wait_log = 0.0
    while True:
        try:
            clear_loop_market_cache()
            # Rebuild only when the contract universe is empty; otherwise keep the
            # selected expiry set fixed for the current run to avoid duplicate
            # startup logs and repeated expiry selection prints.
            if not contract_universe.get("CE") and not contract_universe.get("PE"):
                if not build_contract_universe():
                    time.sleep(30)
                    continue

            now_ist = get_trading_now()
            session_state = get_session_state(now_ist)
            tracked_contracts = get_tracked_contracts()

            if session_state == "squareoff":
                close_all_open_positions("SESSION_SQUAREOFF")
                time.sleep(30)
                continue

            if session_state in {"closed_day", "pre_open"}:
                time.sleep(30)
                continue

            if session_state != "entry" and not tracked_contracts:
                time.sleep(30)
                continue

            future_entries = _normalize_expiry_entries("future_expiry_fs", "future_order_expiry")
            future_selected = _select_nearest_expiry(future_entries) if future_entries else None
            future_symbol = build_future_candle_symbol(future_selected["candle_expiry"]) if future_selected else None

            underlying_price = get_underlying_reference_price()
            if underlying_price is None:
                if time.time() - last_wait_log >= 30:
                    print("[WARN] Underlying CRUDEOILM price unavailable; retrying in 30 seconds")
                    last_wait_log = time.time()
                time.sleep(30)
                continue

            print(f"[UNDERLYING] CRUDEOILM Price={underlying_price:.2f}")
            if future_symbol:
                print_future_snapshot(future_symbol)

            atm = select_atm_contract("CE", underlying_price)
            if atm is None and not tracked_contracts:
                time.sleep(30)
                continue

            idx_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if idx_time == last_printed_time:
                time.sleep(10)
                continue
            last_printed_time = idx_time
            update_cooldowns_once_per_cycle()

            if tracked_contracts:
                contracts_to_monitor = tracked_contracts
            else:
                contracts_to_monitor = []
                for side in ("CE", "PE"):
                    contract = next((c for c in contract_universe[side] if c["strike"] == atm["strike"]), None)
                    if contract is not None:
                        contracts_to_monitor.append(contract)

            for contract in contracts_to_monitor:
                side = contract.get("side")
                signal_symbol = contract["candle_symbol"]
                snapshot = print_flat_market_snapshot(signal_symbol, side, idx_time, contract.get("strike"))
                ok = bool(snapshot.get("ok"))

                open_position = positions.get(signal_symbol)
                debug_position = debug_signal_positions.get(signal_symbol)
                tracked_position = open_position or debug_position

                if debug_position and not open_position:
                    current_price = fetch_latest_price_1m(signal_symbol) or snapshot.get("last_price")
                    entry_price = float(debug_position.get("entry_price", 0) or 0)
                    if current_price is not None:
                        max_profit_points = float(CONFIG.get("max_profit_booking_points", 0) or 0)
                        if (
                            bool(CONFIG.get("max_profit_booking_enabled", True))
                            and entry_price > 0
                            and max_profit_points > 0
                            and float(current_price) >= entry_price + max_profit_points
                        ):
                            print(f"[{idx_time}] [DEBUG SELL SIGNAL] {signal_symbol} TrackedBuy={entry_price:.2f} ExitPrice={float(current_price):.2f} Reason=MX +{max_profit_points:g}")
                            debug_signal_positions.pop(signal_symbol, None)
                            continue

                        entry_low = float(debug_position.get("entry_candle_low", debug_position.get("entry_candle_open", entry_price)) or 0)
                        loss_stop_points = float(CONFIG.get("loss_stop_points", 0) or 0)
                        if entry_low > 0 and float(current_price) <= entry_low + loss_stop_points:
                            print(f"[{idx_time}] [DEBUG SELL SIGNAL] {signal_symbol} TrackedBuy={entry_price:.2f} ExitPrice={float(current_price):.2f} Reason=LSL")
                            debug_signal_positions.pop(signal_symbol, None)
                            continue

                        if CONFIG.get("trailing_stop_enabled", True) and update_debug_trailing_stop_and_check(signal_symbol, float(current_price)):
                            print(f"[{idx_time}] [DEBUG SELL SIGNAL] {signal_symbol} TrackedBuy={entry_price:.2f} ExitPrice={float(current_price):.2f} Reason=TSL")
                            debug_signal_positions.pop(signal_symbol, None)
                            continue

                if (
                    tracked_position
                    and (not open_position or open_position.get("status") == "OPEN")
                    and (
                        snapshot.get("signal") == "SELL"
                        if CONFIG.get("only_supertrend", False)
                        else snapshot.get("opt_trend") == "down"
                    )
                ):
                    exit_price = fetch_latest_price_1m(signal_symbol) or snapshot.get("last_price")
                    if exit_price is not None:
                        print(
                            f"[{idx_time}] [{'DEBUG ' if not open_position else ''}SELL SIGNAL] CRUDEOILM {side} "
                            f"{signal_symbol} Direction changed to down"
                        )
                        if open_position:
                            place_sell_order(signal_symbol, open_position.get("order_symbol"), float(exit_price), reason="DIRECTION_CHANGE", qty=open_position.get("qty"))
                        else:
                            print(f"[{idx_time}] [DEBUG SELL SIGNAL] {signal_symbol} TrackedBuy={debug_position['entry_price']:.2f} ExitPrice={float(exit_price):.2f}")
                            debug_signal_positions.pop(signal_symbol, None)

                if session_state == "entry" and (not tracked_position) and ok and snapshot.get("signal") == "BUY":
                    order_symbol = contract["order_symbol"]
                    close_price = snapshot.get("last_price")
                    entry_open = snapshot.get("entry_open")
                    entry_low = snapshot.get("entry_low")
                    print(f"[{idx_time}] [SIGNAL] CRUDEOILM {side} {signal_symbol} Close={close_price:.2f} ST={snapshot.get('st_value') if snapshot.get('st_value') is not None else 'NA'}")
                    if CONFIG.get("debug_signal_tracking_enabled", False):
                        debug_signal_positions[signal_symbol] = {
                            "order_symbol": order_symbol,
                            "side": side,
                            "entry_price": float(close_price),
                            "entry_time": idx_time,
                            "entry_candle_open": float(entry_open),
                            "entry_candle_low": float(entry_low),
                            "highest_price": float(close_price),
                            "trailing_sl_activated": False,
                            "trailing_stop_price": float(close_price) - float(CONFIG.get("trailing_stop_loss_points", 8) or 8),
                            "strike": contract.get("strike"),
                        }
                        print(f"[{idx_time}] [DEBUG BUY SIGNAL TRACKED] {signal_symbol} OrderSymbol={order_symbol} Price={float(close_price):.2f} Side={side}")
                    place_buy_order(signal_symbol, order_symbol, float(close_price), float(entry_open), entry_candle_low=float(entry_low))

            for symbol in list(positions.keys()):
                if positions[symbol].get("status") != "OPEN":
                    continue
                cur = fetch_latest_price_1m(symbol)
                if cur is None:
                    continue

                position = positions[symbol]
                entry_price = float(position.get("entry_price", 0) or 0)
                max_profit_points = float(CONFIG.get("max_profit_booking_points", 0) or 0)
                now_ts = datetime.now()
                last_max_check = last_max_profit_check_at.get(symbol)
                max_check_interval = float(CONFIG.get("max_profit_check_interval_sec", 0) or 0)
                max_profit_check_due = (
                    last_max_check is None
                    or max_check_interval <= 0
                    or (now_ts - last_max_check).total_seconds() >= max_check_interval
                )
                if max_profit_check_due:
                    last_max_profit_check_at[symbol] = now_ts
                max_profit_hit = (
                    bool(CONFIG.get("max_profit_booking_enabled", True))
                    and max_profit_check_due
                    and entry_price > 0
                    and max_profit_points > 0
                    and float(cur) >= entry_price + max_profit_points
                )

                if max_profit_hit:
                    order_symbol = position.get("order_symbol")
                    place_sell_order(
                        symbol,
                        order_symbol,
                        float(cur),
                        reason=f"MAX_PROFIT: +{max_profit_points:g} pts",
                        qty=position.get("qty"),
                    )
                elif not CONFIG.get("only_supertrend", False) and check_lsl_exit(symbol):
                    order_symbol = positions[symbol].get("order_symbol")
                    place_sell_order(symbol, order_symbol, float(cur), reason="LSL", qty=positions[symbol].get("qty"))
                elif (
                    not CONFIG.get("only_supertrend", False)
                    and CONFIG.get("trailing_stop_enabled", True)
                    and update_trailing_stop_and_check(symbol, float(cur))
                ):
                    order_symbol = positions[symbol].get("order_symbol")
                    place_sell_order(symbol, order_symbol, float(cur), reason="TSL", qty=positions[symbol].get("qty"))

            time.sleep(30)
        except Exception as exc:
            print(f"[ERROR] signal loop: {type(exc).__name__}: {exc}")
            time.sleep(10)


def main():
    if growwapi is None:
        print("[INFO] Script loaded in safe mode. Fill CONFIG values to enable live trading.")
        return

    print("[INIT] OILMINI started")
    if not build_contract_universe():
        raise SystemExit(1)

    margin = get_available_margin()
    if margin is None:
        print("[MARGIN] Available margin: N/A")
    else:
        print(f"[MARGIN] Available margin: {margin:.2f}")

    live_signal_loop()


if __name__ == "__main__":
    main()
