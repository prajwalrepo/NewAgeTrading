# =============================================================================
# SENSEXINTRA.py - SENSEX intraday FNO Supertrend strategy on Groww API
#
# Flow:
# 1) Read SENSEX index signal first.
# 2) Use current index price to select the nearest ATM strike.
# 3) Build CE/PE candle symbols in the form:
#       SENSEX-{FetchingExpiry}-78000-CE
#    and order symbols in the form:
#       SENSEX{orderexpiry}78000CE
# 4) Enter only on a closed candle when Supertrend flips from down to up.
# 5) Require the entry candle to close above the previous candle.
# 6) Avoid entries when the entry candle opens more than 10 points away from
#    the previous close.
# 7) Stop loss uses the entry candle open price.
# 8) Includes max profit booking, money allocation per trade, and side cooldown.
# =============================================================================

from datetime import datetime, time as dt_time, timedelta, timezone
from email.message import EmailMessage
import os
import re
import smtplib
import ssl
import sys
import threading
import time
import warnings

import numpy as np
import pandas as pd
from growwapi import GrowwAPI

warnings.filterwarnings("ignore", category=FutureWarning)


CONFIG = {
    "api_key": "eyJraWQiOiJaTUtjVXciLCJhbGciOiJFUzI1NiJ9.eyJleHAiOjI1Njg5NDU3MzIsImlhdCI6MTc4MDU0NTczMiwibmJmIjoxNzgwNTQ1NzMyLCJzdWIiOiJ7XCJ0b2tlblJlZklkXCI6XCI5MjM4NTRkYS0xODFmLTRmNmYtOGFkMi00M2MzMzdlM2ZhZWJcIixcInZlbmRvckludGVncmF0aW9uS2V5XCI6XCJlMzFmZjIzYjA4NmI0MDZjODg3NGIyZjZkODQ5NTMxM1wiLFwidXNlckFjY291bnRJZFwiOlwiY2QxYjY2MjMtY2MzOS00N2Q0LTkxNWYtZmVlZTE3ZjFkNTRmXCIsXCJkZXZpY2VJZFwiOlwiNjA5NWQ4NTYtZGE1My01Y2Q2LWJlZGUtMGFmZWI4NzI1N2U0XCIsXCJzZXNzaW9uSWRcIjpcIjA4MGQ0YjQ0LWJjZWEtNGI3ZC05MjcyLTMxYmJmNDI5MzUwOFwiLFwiYWRkaXRpb25hbERhdGFcIjpcIno1NC9NZzltdjE2WXdmb0gvS0EwYlAveUhIeXlXNVZKSmNyMno5V0JqMlpSTkczdTlLa2pWZDNoWjU1ZStNZERhWXBOVi9UOUxIRmtQejFFQisybTdRPT1cIixcInJvbGVcIjpcImF1dGgtdG90cFwiLFwic291cmNlSXBBZGRyZXNzXCI6XCIxMzYuMjI2LjI1My45MCwxNzIuNjkuMTIyLjE3NCwzNS4yNDEuMjMuMTIzXCIsXCJ0d29GYUV4cGlyeVRzXCI6MjU2ODk0NTczMjkzNSxcInZlbmRvck5hbWVcIjpcImdyb3d3QXBpXCJ9IiwiaXNzIjoiYXBleC1hdXRoLXByb2QtYXBwIn0.5lRo2nlPb4v5xdAp038NbKL7F5vkFFijwMNXrV4vLghxAAWrMQkAZx3CVx8zqIyySd5AQXzHTdT_-lAWB2lJLg",
    "api_secret": "nwS5I3ex3!AB7jIPuOrFLYDXXAq!X&O)",

    # Core setup
    "index_symbol": "SENSEX",
    "exchange": "BSE",
    "candle_interval": "1M",
    "atr_period": 10,
    "factor": 3.0,

    
    # Expiry can be configured once for both CE and PE, or kept side-specific if needed.
    # System auto-selects the nearest upcoming expiry from the configured array.
    # Supported entries:
    # 1) String: "08Sep26" (uses matching order_expiry_* by index if provided as list)
    # 2) Dict: {"candle": "25Aug26", "order": "26AUG"} for monthly style order token
    "expiry_fs": ["24Sep26", "01Oct26", "08Oct26", "15Oct26"],
    # Order expiry token(s) paired with expiry_fs by index for list usage.
    # Weekly-style examples: "26908" (YYMDD)
    # Monthly-style examples: "26AUG" (YYMMM)
    "order_expiry": ["26924", "261001", "261008", "261015"],
    "strike_step": 100,
    # Keep the configured universe wide enough for normal SENSEX movement.
    # ATM selection then chooses the nearest 100-point strike to the index.
    "strike_start": 70000,
    "strike_end": 80000,

    # Entry rules
    "use_momentum_filter": True,
    "require_close_above_prev_close": True,
    "entry_open_gap_limit_points": 10.0,
    "flip_confirmation_window_candles": 1,
    "pullback_reentry_enabled": True,
    "pullback_reentry_touch_tolerance_points": 3.0,

    # Trading schedule in IST
    "trading_timezone_offset_minutes": 330,
    "trading_weekdays": ["MONDAY", "TUESDAY", "FRIDAY"],
    "entry_window_start": "09:13",
    "entry_window_end": "15:10",
    "force_exit_time": "15:30",

    # Trade controls
    # Lot sizing is single-sized across CE/PE.
    "lot_size": 10,
    "max_lots_per_trade": 1,
    "allocation_per_trade": 40000,
    "check_margin_before_order": True,
    "max_trades_per_day_ce": 2,
    "max_trades_per_day_pe": 2,

    # Risk / exits
    "special_exit_enabled": True,
    # Max profit booking (configurable points)
    "max_profit_booking_enabled": True,
    "max_profit_booking_points": 20,
    "loss_stop_points": 10,

    # Trailing stop-loss (configurable points)
    # trailing_stop_loss_points = gap kept behind the highest price
    # trailing_stop_trigger_points = minimum profit required before activation
    "trailing_stop_enabled": True,
    "trailing_stop_loss_points": 8,
    "trailing_stop_trigger_points": 8,
    "max_profit_check_interval_sec": 60,
    "buy_cooldown_candles": 2,
    "exit_order_cooldown_sec": 90,

    # API retry
    "api_rate_limit_retry_delay": 5,
    "api_rate_limit_max_retries": 3,

    "DEBUG": False,
    # Track generated signals even when Groww blocks the order for margin.
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


try:
    raw_access_token = GrowwAPI.get_access_token(
        api_key=CONFIG["api_key"],
        secret=CONFIG["api_secret"],
    )
    access_token = _normalize_access_token(raw_access_token)
    growwapi = GrowwAPI(access_token)
except Exception as exc:
    print(f"[ERROR] Authentication failed: {exc}")
    raise SystemExit(1)


# Runtime state
pnl_log_stop_event = threading.Event()
positions = {}  # {candle_symbol: {...}}
trades_today_ce = 0
trades_today_pe = 0
daily_realized_pnl = 0.0
exit_attempt_tracker = {}
exit_reason_tracker = {}
buy_signal_attempt_tracker = {}
debug_signal_positions = {}
pullback_reentry_tracker = {}
side_cooldown_tracker = {"CE": 0, "PE": 0}
no_data_warn_tracker = {}
contract_universe = {"CE": [], "PE": []}
order_to_candle = {}
candle_to_order = {}
candle_to_side = {}
order_expiry_to_candle_expiry = {}
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


def get_session_state(now_dt=None):
    now_dt = now_dt or get_trading_now()
    allowed_weekdays = _get_allowed_weekdays()
    if allowed_weekdays and now_dt.weekday() not in allowed_weekdays:
        return "closed_day"

    current_time = dt_time(now_dt.hour, now_dt.minute, now_dt.second)
    entry_start = _parse_hhmm(CONFIG.get("entry_window_start", "09:13"))
    entry_end = _parse_hhmm(CONFIG.get("entry_window_end", "15:10"))
    force_exit = _parse_hhmm(CONFIG.get("force_exit_time", "15:30"))

    if current_time < entry_start:
        return "pre_open"
    if current_time < entry_end:
        return "entry"
    if current_time < force_exit:
        return "manage_only"
    return "squareoff"


def close_all_open_positions(reason, idx_time=None):
    live_positions = get_live_open_positions()
    reconcile_positions_with_groww(live_positions)
    for symbol, pos in list(positions.items()):
        if pos.get("status") != "OPEN":
            continue
        exit_price = fetch_latest_price_1m(symbol) or pos.get("entry_price", 0)
        if exit_price is None:
            continue
        place_sell_order(symbol, pos.get("order_symbol"), float(exit_price), reason=reason, qty=pos.get("qty"))

    debug_signal_positions.clear()
    pullback_reentry_tracker.clear()


def parse_strike_from_candle_symbol(symbol):
    m = re.search(r"-(\d+)-(CE|PE)$", str(symbol).strip(), flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def get_order_expiry_text(expiry_text):
    return str(expiry_text or "").strip()


def build_candle_symbol(root, expiry_fs, strike, side):
    return f"{str(root).upper()}-{expiry_fs}-{int(strike)}-{str(side).upper()}"


def build_order_symbol(root, order_expiry_text, strike, side):
    return f"{str(root).upper()}{get_order_expiry_text(order_expiry_text)}{int(strike)}{str(side).upper()}"


def _parse_order_expiry_token(order_expiry_text):
    token = str(order_expiry_text or "").strip().upper()
    if not token:
        return None

    month_match = re.fullmatch(r"(\d{2})([A-Z]{3})", token)
    if month_match:
        year = int(month_match.group(1))
        month_code = month_match.group(2)
        try:
            return datetime.strptime(f"{year}{month_code}", "%y%b")
        except ValueError:
            pass

    if not re.fullmatch(r"\d+", token):
        return None

    if len(token) < 5:
        return None

    year = int(token[:2])
    remainder = token[2:]
    for month_len in (1, 2):
        if len(remainder) != month_len + 2:
            continue
        try:
            month = int(remainder[:month_len])
            day = int(remainder[month_len:])
            return datetime(2000 + year, month, day)
        except ValueError:
            continue
    return None


def resolve_candle_symbol_from_order_symbol(order_symbol):
    symbol = (order_symbol or "").upper()
    match = re.fullmatch(r"([A-Z]+)([0-9A-Z]+)(\d+)(CE|PE)", symbol)
    if not match:
        return None

    root, expiry_token, strike_text, side = match.groups()
    if expiry_token in order_expiry_to_candle_expiry:
        expiry_fs = order_expiry_to_candle_expiry[expiry_token]
        return f"{root}-{expiry_fs}-{int(strike_text)}-{side}"

    expiry_dt = _parse_order_expiry_token(expiry_token)
    if expiry_dt is not None:
        return f"{root}-{expiry_dt.strftime('%d%b%y')}-{int(strike_text)}-{side}"

    return None


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


def _normalize_expiry_entries(side):
    side_key = str(side).lower()
    expiry_cfg = CONFIG.get(f"expiry_fs_{side_key}", CONFIG.get("expiry_fs"))
    order_cfg = CONFIG.get(f"order_expiry_{side_key}", CONFIG.get("order_expiry"))

    expiry_list = expiry_cfg if isinstance(expiry_cfg, list) else [expiry_cfg]
    order_list = order_cfg if isinstance(order_cfg, list) else [order_cfg]

    entries = []
    for idx, item in enumerate(expiry_list):
        if isinstance(item, dict):
            candle_expiry = str(
                item.get("candle")
                or item.get("expiry_fs")
                or item.get("fetch_expiry")
                or ""
            ).strip()
            order_expiry = str(
                item.get("order")
                or item.get("order_expiry")
                or item.get("order_token")
                or ""
            ).strip().upper()
        else:
            candle_expiry = str(item or "").strip()
            order_expiry = str(order_list[idx] if idx < len(order_list) else "").strip().upper()

        if not candle_expiry:
            continue
        if not order_expiry:
            # Fallback to candle expiry token if explicit order token is not provided.
            order_expiry = candle_expiry.upper()

        expiry_dt = _parse_expiry_fs_date(candle_expiry)
        if expiry_dt is None:
            print(f"[WARN] Invalid expiry format '{candle_expiry}' for {side}. Expected DDMonYY.")
            continue

        entries.append(
            {
                "side": side.upper(),
                "candle_expiry": candle_expiry,
                "order_expiry": order_expiry,
                "expiry_dt": expiry_dt,
            }
        )
    return entries


def _select_nearest_expiry_entry(entries, now_dt=None):
    if not entries:
        return None
    now_dt = now_dt or datetime.now()
    today = now_dt.date()

    upcoming = [e for e in entries if e["expiry_dt"].date() >= today]
    target_pool = upcoming if upcoming else entries
    return min(target_pool, key=lambda e: abs((e["expiry_dt"].date() - today).days))


def _build_dataframe(candles):
    n = len(candles[0])
    if n == 7:
        cols = ["date", "open", "high", "low", "close", "volume", "oi"]
    elif n == 6:
        cols = ["date", "open", "high", "low", "close", "volume"]
    else:
        raise ValueError(f"Unexpected candle column count: {n}")

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


def fetch_recent_candles(symbol, segment, lookback_minutes=None):
    interval_str = CONFIG["candle_interval"].upper()
    if lookback_minutes is None:
        min_needed = CONFIG["atr_period"] + 10
        if "M" in interval_str:
            lookback_minutes = max(120, min_needed * int(interval_str.replace("M", "")))
        elif "H" in interval_str:
            lookback_minutes = max(240, min_needed * int(interval_str.replace("H", "")) * 60)
        else:
            lookback_minutes = 240

    exchange = CONFIG["exchange"]
    groww_symbol = symbol if symbol.startswith(f"{exchange}-") else f"{exchange}-{symbol}"
    end_time = datetime.now()

    retry_delay = int(CONFIG.get("api_rate_limit_retry_delay", 5))
    max_retries = int(CONFIG.get("api_rate_limit_max_retries", 3))
    min_candles = CONFIG["atr_period"] + 10
    interval_key = CANDLE_INTERVAL_MAP[interval_str]

    cache_key = (groww_symbol, segment, int(lookback_minutes), interval_key)
    cached_df, cached_err = _get_cached_candles(cache_key)
    if cached_df is not None:
        return cached_df, cached_err

    lookback_candidates = [
        int(lookback_minutes),
        max(int(lookback_minutes), 24 * 60),
        max(int(lookback_minutes), 3 * 24 * 60),
        max(int(lookback_minutes), 7 * 24 * 60),
    ]

    seen = set()
    lookback_windows = []
    for candidate in lookback_candidates:
        if candidate not in seen:
            lookback_windows.append(candidate)
            seen.add(candidate)

    best_df = None
    best_count = 0

    for lookback in lookback_windows:
        start_time = end_time - timedelta(minutes=lookback)
        retry_count = 0
        while retry_count <= max_retries:
            try:
                raw = growwapi.get_historical_candles(
                    groww_symbol=groww_symbol,
                    exchange=getattr(growwapi, f"EXCHANGE_{exchange}"),
                    segment=getattr(growwapi, segment),
                    candle_interval=interval_key,
                    start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
                )
                candles = _extract_candle_rows(raw)
                if candles:
                    df = _build_dataframe(candles)
                    if df is not None and not df.empty:
                        df = df[df["date"] <= pd.Timestamp(datetime.now())].reset_index(drop=True)
                        if len(df) > best_count:
                            best_df = df
                            best_count = len(df)
                        if len(df) >= min_candles:
                            _set_cached_candles(cache_key, df)
                            return df, None
                break
            except Exception as exc:
                if "rate limit" in str(exc).lower() and retry_count < max_retries:
                    retry_count += 1
                    print(f"[RATE LIMIT] {symbol} retry in {retry_delay}s ({retry_count}/{max_retries})")
                    time.sleep(retry_delay)
                    continue
                return None, f"{type(exc).__name__}: {exc}"

    return best_df, f"Insufficient data: {best_count} candles"


def fetch_latest_price_1m(symbol):
    try:
        cached_price = _get_cached_price(symbol)
        if cached_price is not None:
            return cached_price

        exchange = CONFIG["exchange"]
        groww_symbol = symbol if symbol.startswith(f"{exchange}-") else f"{exchange}-{symbol}"
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=5)
        raw = growwapi.get_historical_candles(
            groww_symbol=groww_symbol,
            exchange=getattr(growwapi, f"EXCHANGE_{exchange}"),
            segment=growwapi.SEGMENT_FNO,
            candle_interval="1minute",
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        candles = _extract_candle_rows(raw)
        if candles:
            df = _build_dataframe(candles)
            if not df.empty:
                price = float(df["close"].iloc[-1])
                _set_cached_price(symbol, price)
                return price
    except Exception as exc:
        if CONFIG.get("DEBUG"):
            print(f"[DEBUG] fetch_latest_price_1m error for {symbol}: {exc}")
    return None


def get_open_fno_positions():
    try:
        resp = growwapi.get_positions_for_user(segment=growwapi.SEGMENT_FNO)
        if isinstance(resp, dict):
            all_pos = resp.get("data") or resp.get("positions") or []
            return [p for p in all_pos if int(p.get("quantity", 0)) > 0]
        return []
    except Exception as exc:
        print(f"[WARNING] Error fetching positions: {type(exc).__name__}: {exc}")
        return []


def infer_side_from_symbol(symbol):
    s = (symbol or "").upper()
    if s.endswith("CE"):
        return "CE"
    if s.endswith("PE"):
        return "PE"
    return None


def get_live_open_positions():
    raw = get_open_fno_positions()
    if not raw:
        return {}

    live = {}
    for p in raw:
        order_symbol = p.get("trading_symbol") or p.get("symbol")
        qty = int(p.get("quantity", 0))
        entry_price = float(p.get("average_price", p.get("net_price", 0)) or 0)
        if not order_symbol or qty <= 0:
            continue

        candle_symbol = order_to_candle.get(order_symbol)
        is_manual = candle_symbol is None
        if is_manual:
            candle_symbol = resolve_candle_symbol_from_order_symbol(order_symbol) or order_symbol

        side = candle_to_side.get(candle_symbol) or infer_side_from_symbol(order_symbol)
        live[candle_symbol] = {
            "qty": qty,
            "entry_price": entry_price,
            "order_symbol": order_symbol,
            "product_type": p.get("product") or p.get("product_type", "MIS"),
            "status": "OPEN",
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "side": side,
            "is_manual": is_manual,
        }
    return live


def reconcile_positions_with_groww(live_positions):
    global positions
    now_txt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for symbol, live_data in live_positions.items():
        if symbol not in positions:
            positions[symbol] = {
                **live_data,
                "entry_candle_open": float(live_data.get("entry_price", 0)),
                "highest_price": float(live_data.get("entry_price", 0)),
                "stop_loss_price": float(live_data.get("entry_price", 0)),
                "trailing_sl_activated": False,
            }
            if live_data.get("is_manual"):
                print(f"[{now_txt}] [MANUAL POSITION DETECTED] {symbol} Qty={live_data['qty']} Entry={live_data.get('entry_price', 0):.2f}")
        else:
            positions[symbol]["qty"] = live_data["qty"]
            positions[symbol]["product_type"] = live_data.get("product_type", positions[symbol].get("product_type", "MIS"))
            positions[symbol]["status"] = "OPEN"

    for symbol in list(positions.keys()):
        if symbol not in live_positions or live_positions[symbol].get("qty", 0) <= 0:
            positions.pop(symbol, None)


def get_available_fno_margin():
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


def build_contract_universe():
    contract_universe["CE"].clear()
    contract_universe["PE"].clear()
    order_to_candle.clear()
    candle_to_order.clear()
    candle_to_side.clear()
    order_expiry_to_candle_expiry.clear()

    ce_entries = _normalize_expiry_entries("CE")
    pe_entries = _normalize_expiry_entries("PE")
    chosen_ce = _select_nearest_expiry_entry(ce_entries)
    chosen_pe = _select_nearest_expiry_entry(pe_entries)
    if not chosen_ce or not chosen_pe:
        print("[ERROR] Could not resolve nearest CE/PE expiry from config")
        return False

    expiry_fs_ce = chosen_ce["candle_expiry"]
    expiry_fs_pe = chosen_pe["candle_expiry"]
    order_expiry_ce = chosen_ce["order_expiry"]
    order_expiry_pe = chosen_pe["order_expiry"]

    order_expiry_to_candle_expiry[order_expiry_ce.upper()] = expiry_fs_ce
    order_expiry_to_candle_expiry[order_expiry_pe.upper()] = expiry_fs_pe

    print(f"[EXPIRY] Selected CE: candle={expiry_fs_ce}, order={order_expiry_ce}")
    print(f"[EXPIRY] Selected PE: candle={expiry_fs_pe}, order={order_expiry_pe}")

    strikes = list(range(int(CONFIG["strike_start"]), int(CONFIG["strike_end"]) + int(CONFIG["strike_step"]), int(CONFIG["strike_step"])))
    for side in ("CE", "PE"):
        side_expiry_fs = expiry_fs_ce if side == "CE" else expiry_fs_pe
        side_order_expiry = order_expiry_ce if side == "CE" else order_expiry_pe
        for strike in strikes:
            candle_symbol = build_candle_symbol(CONFIG["index_symbol"], side_expiry_fs, strike, side)
            order_symbol = build_order_symbol(CONFIG["index_symbol"], side_order_expiry, strike, side)
            rec = {
                "candle_symbol": candle_symbol,
                "order_symbol": order_symbol,
                "strike": int(strike),
                "side": side,
                "expiry_fs": side_expiry_fs,
                "order_expiry": side_order_expiry,
            }
            contract_universe[side].append(rec)
            order_to_candle[order_symbol] = candle_symbol
            candle_to_order[candle_symbol] = order_symbol
            candle_to_side[candle_symbol] = side

    if CONFIG.get("DEBUG"):
        print("[DEBUG] Loaded contracts:")
        for side in ("CE", "PE"):
            print(f"  {side}: {len(contract_universe[side])}")
    return True


def check_trade_limit(candle_symbol):
    side = candle_to_side.get(candle_symbol)
    if side == "CE":
        max_trades = int(CONFIG.get("max_trades_per_day_ce", 999))
        return trades_today_ce < max_trades, trades_today_ce, max_trades
    if side == "PE":
        max_trades = int(CONFIG.get("max_trades_per_day_pe", 999))
        return trades_today_pe < max_trades, trades_today_pe, max_trades
    return True, 0, 999


def increment_trade_count(candle_symbol):
    global trades_today_ce, trades_today_pe
    side = candle_to_side.get(candle_symbol)
    if side == "CE":
        trades_today_ce += 1
    elif side == "PE":
        trades_today_pe += 1


def place_order_with_retry(trading_symbol, quantity, transaction_type, product_type=None):
    if product_type is None:
        product_type = growwapi.PRODUCT_MIS

    retry_count = 0
    max_retries = int(CONFIG["api_rate_limit_max_retries"])
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    side = "BUY" if transaction_type == growwapi.TRANSACTION_TYPE_BUY else "SELL"

    while retry_count <= max_retries:
        try:
            resp = growwapi.place_order(
                trading_symbol=trading_symbol,
                quantity=int(quantity),
                validity=growwapi.VALIDITY_DAY,
                exchange=getattr(growwapi, f"EXCHANGE_{CONFIG['exchange']}"),
                segment=growwapi.SEGMENT_FNO,
                product=product_type,
                order_type=growwapi.ORDER_TYPE_MARKET,
                transaction_type=transaction_type,
            )

            if isinstance(resp, dict):
                order_id = resp.get("groww_order_id", "N/A")
                status = str(resp.get("order_status", "UNKNOWN")).upper()
                msg = resp.get("message") or resp.get("remark") or ""
                if status in {"SUCCESS", "EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
                    print(f"[{ts}] [ORDER CONFIRMED] {side} {trading_symbol} Qty={int(quantity)} | OrderID={order_id} | Status={status}")
                elif status in {"REJECTED", "CANCELLED", "FAILED"}:
                    print(f"[{ts}] [ORDER REJECTED] {trading_symbol} | Status={status} | Error: {msg}")
                else:
                    print(f"[{ts}] [ORDER SUBMITTED] {side} {trading_symbol} Qty={int(quantity)} | OrderID={order_id} | Status={status}")
            return resp
        except Exception as exc:
            if "rate limit" in str(exc).lower() and retry_count < max_retries:
                retry_count += 1
                time.sleep(int(CONFIG["api_rate_limit_retry_delay"]))
            else:
                raise


def verify_order_execution(order_id, symbol, fallback_price=None):
    try:
        details = growwapi.get_order_detail(groww_order_id=order_id, segment=growwapi.SEGMENT_FNO)
        final_status = details.get("order_status", "UNKNOWN")
        avg_price = details.get("average_fill_price") or details.get("price")
        avg_price = float(avg_price) if avg_price else (fallback_price or 0)
        if CONFIG.get("DEBUG"):
            print(f"[GROWW ORDER STATUS] Symbol={symbol} | ID={order_id} | FinalStatus={final_status} | AvgPrice={avg_price:.2f}")
        return final_status, avg_price, details
    except Exception as exc:
        if CONFIG.get("DEBUG"):
            print(f"[DEBUG] verify_order_execution failed for {symbol}: {exc}")
        return None, None, None


def resolve_product_constant(product_type):
    pt = (product_type or "MIS").upper()
    return {
        "MIS": growwapi.PRODUCT_MIS,
        "NRML": growwapi.PRODUCT_NRML,
        "CNC": growwapi.PRODUCT_CNC,
    }.get(pt, growwapi.PRODUCT_MIS)


def update_daily_pnl(pnl):
    global daily_realized_pnl
    daily_realized_pnl += float(pnl)


def select_atm_contract(side, index_price):
    contracts = contract_universe.get(side, [])
    if not contracts:
        return None
    if side == "CE":
        return min(contracts, key=lambda c: (abs(c["strike"] - index_price), -c["strike"]))
    else:
        return min(contracts, key=lambda c: (abs(c["strike"] - index_price), c["strike"]))


def get_open_symbols_by_side(side):
    result = []
    for sym, pos in positions.items():
        tracked_side = candle_to_side.get(sym) or pos.get("side")
        if pos.get("status") == "OPEN" and tracked_side == side:
            result.append(sym)
    return result


def has_open_on_side(side):
    return len(get_open_symbols_by_side(side)) > 0


def has_live_open_on_side(side, live_positions=None):
    if live_positions is None:
        live_positions = get_live_open_positions()
    for sym, pos in (live_positions or {}).items():
        tracked_side = candle_to_side.get(sym) or pos.get("side")
        if int(pos.get("qty", 0)) > 0 and tracked_side == side:
            return True
    return False


def get_tracked_candle_symbols(live_positions=None):
    tracked = []
    seen = set()

    for sym, pos in positions.items():
        if pos.get("status") == "OPEN" and sym not in seen:
            tracked.append(sym)
            seen.add(sym)

    for sym, pos in (live_positions or {}).items():
        if int(pos.get("qty", 0)) > 0 and sym not in seen:
            tracked.append(sym)
            seen.add(sym)

    if CONFIG.get("debug_signal_tracking_enabled", False):
        for sym in debug_signal_positions.keys():
            if sym not in seen:
                tracked.append(sym)
                seen.add(sym)

    if CONFIG.get("pullback_reentry_enabled", False):
        for sym in pullback_reentry_tracker.keys():
            if sym not in seen:
                tracked.append(sym)
                seen.add(sym)

    return tracked


def arm_pullback_reentry(candle_symbol, reason, exit_price, idx_time=None):
    if not CONFIG.get("pullback_reentry_enabled", False):
        return
    reason_text = str(reason or "").upper()
    if not (reason_text.startswith("MX") or reason_text.startswith("TSL")):
        pullback_reentry_tracker.pop(candle_symbol, None)
        return

    side = candle_to_side.get(candle_symbol)
    pullback_reentry_tracker[candle_symbol] = {
        "side": side,
        "strike": next((rec.get("strike") for rec in contract_universe.get(side, []) if rec.get("candle_symbol") == candle_symbol), None),
        "order_symbol": candle_to_order.get(candle_symbol),
        "armed_at": idx_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_exit_price": float(exit_price),
        "reason": reason,
    }
    print(f"[{idx_time or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [REENTRY ARMED] {candle_symbol} Reason={reason} ExitPrice={float(exit_price):.2f}")


def clear_pullback_reentry(candle_symbol):
    pullback_reentry_tracker.pop(candle_symbol, None)


def close_open_side(side, reason):
    for symbol in get_open_symbols_by_side(side):
        if symbol not in positions or positions[symbol].get("status") != "OPEN":
            continue
        price = fetch_latest_price_1m(symbol) or positions[symbol].get("entry_price", 0)
        if price is None:
            continue
        place_sell_order(symbol, positions[symbol].get("order_symbol"), float(price), reason=reason, qty=positions[symbol].get("qty"))


def print_flat_market_snapshot(candle_symbol, side, idx_time, strike):
    ok, last_price, entry_open, entry_low, st_value, opt_trend, signal = option_has_flip_buy_signal(candle_symbol, idx_time)
    if last_price is None:
        print(f"[OPTION] {candle_symbol} Price=NA ST=NA Signal={signal} Direction=NA")
        return {
            "ok": ok,
            "last_price": None,
            "entry_open": entry_open,
            "entry_low": entry_low,
            "st_value": st_value,
            "opt_trend": opt_trend,
            "signal": signal,
            "candle_symbol": candle_symbol,
            "side": side,
            "strike": strike,
        }

    st_text = f"{st_value:.2f}" if st_value is not None else "NA"
    dir_text = opt_trend if opt_trend is not None else "NA"
    open_text = f"{entry_open:.2f}" if entry_open is not None else "NA"
    low_text = f"{entry_low:.2f}" if entry_low is not None else "NA"
    print(
        f"[OPTION] {candle_symbol} Price={last_price:.2f} Open={open_text} Low={low_text} ST={st_text} "
        f"Signal={signal} Direction={dir_text}"
    )
    return {
        "ok": ok,
        "last_price": last_price,
        "entry_open": entry_open,
        "entry_low": entry_low,
        "st_value": st_value,
        "opt_trend": opt_trend,
        "signal": signal,
        "candle_symbol": candle_symbol,
        "side": side,
        "strike": strike,
    }


def check_lsl_exit():
    if not CONFIG.get("special_exit_enabled", True):
        return

    live_positions = get_live_open_positions()
    for symbol, pos in list(positions.items()):
        if pos.get("status") != "OPEN" or symbol not in live_positions:
            continue

        entry_low = float(pos.get("entry_candle_low", pos.get("entry_candle_open", pos.get("entry_price", 0))) or 0)
        if entry_low <= 0:
            continue

        loss_stop_points = float(CONFIG.get("loss_stop_points", CONFIG.get("stop_loss_points", 0)) or 0)
        loss_stop_level = entry_low + loss_stop_points

        cur = fetch_latest_price_1m(symbol)
        if cur is None:
            continue

        if cur <= loss_stop_level:
            place_sell_order(
                symbol,
                pos.get("order_symbol"),
                float(cur),
                reason=f"LSL: entry candle low + {loss_stop_points} pts",
                qty=pos.get("qty"),
            )


def _update_trailing_stop_for_record(pos, current_price, symbol=None):
    entry = float(pos.get("entry_price", 0) or 0)
    if entry <= 0:
        return False

    stop_gap = float(
        CONFIG.get(
            "trailing_stop_loss_points",
            CONFIG.get("trailing_stop_gap_points", 8),
        ) or 8
    )
    trigger_points = float(
        CONFIG.get(
            "trailing_stop_trigger_points",
            CONFIG.get("trailing_stop_min_profit_points", stop_gap),
        ) or stop_gap
    )

    highest = float(pos.get("highest_price", entry) or entry)
    if current_price > highest:
        highest = float(current_price)
        pos["highest_price"] = highest

    if not bool(pos.get("trailing_sl_activated", False)) and (highest - entry) >= trigger_points:
        pos["trailing_sl_activated"] = True
        pos["trailing_stop_price"] = highest - stop_gap
        if CONFIG.get("DEBUG"):
            print(f"[DEBUG] TRAILING ACTIVATED {symbol or 'UNKNOWN'} | High={highest:.2f} TSL={pos['trailing_stop_price']:.2f}")

    if bool(pos.get("trailing_sl_activated", False)):
        current_tsl = float(pos.get("trailing_stop_price", entry - stop_gap))
        new_tsl = max(current_tsl, highest - stop_gap)
        pos["trailing_stop_price"] = new_tsl
        if current_price <= new_tsl:
            return True
    return False


def update_trailing_stop_and_check(symbol, current_price):
    if symbol not in positions or positions[symbol].get("status") != "OPEN":
        return False
    return _update_trailing_stop_for_record(positions[symbol], current_price, symbol=symbol)


def update_debug_trailing_stop_and_check(symbol, current_price):
    pos = debug_signal_positions.get(symbol)
    if not pos:
        return False
    return _update_trailing_stop_for_record(pos, current_price, symbol=symbol)


def check_trailing_stop_exit():
    if not CONFIG.get("trailing_stop_enabled", True):
        return

    live_positions = get_live_open_positions()
    for symbol, pos in list(positions.items()):
        if pos.get("status") != "OPEN" or symbol not in live_positions:
            continue

        cur = fetch_latest_price_1m(symbol)
        if cur is None:
            continue

        if update_trailing_stop_and_check(symbol, float(cur)):
            place_sell_order(
                symbol,
                pos.get("order_symbol"),
                float(cur),
                reason="TSL: trailing stop hit",
                qty=pos.get("qty"),
            )


def place_buy_order(candle_symbol, order_symbol, price, entry_candle_open, qty=None, entry_candle_low=None):
    try:
        can_trade, cnt, max_allowed = check_trade_limit(candle_symbol)
        if not can_trade:
            print(f"[BLOCKED] Trade limit {candle_symbol}: {cnt}/{max_allowed}")
            return None

        if side_cooldown_tracker.get(candle_to_side.get(candle_symbol), 0) > 0:
            return None

        lot_size = int(CONFIG.get("lot_size", 1) or 1)
        max_lots = int(CONFIG.get("max_lots_per_trade", 0) or 0)

        fno_margin = get_available_fno_margin()
        if CONFIG.get("check_margin_before_order", True) and fno_margin is None:
            print("[SKIP] Margin unavailable")
            return None

        allocation = float(CONFIG.get("allocation_per_trade", 0) or 0)
        if fno_margin is not None and fno_margin > 0:
            use_margin = min(allocation, fno_margin)
        else:
            use_margin = allocation

        if use_margin <= 0:
            print(f"[BUY BLOCKED] {candle_symbol}: no usable margin budget ({use_margin:.2f})")
            return None

        min_cost = lot_size * price
        if use_margin < min_cost:
            print(
                f"[BUY BLOCKED] {candle_symbol}: insufficient margin. "
                f"Need {min_cost:.2f}, available {use_margin:.2f}"
            )
            return None

        if qty is None:
            num_lots = max(int(use_margin / (lot_size * price)), 1)
            if max_lots > 0:
                num_lots = min(num_lots, max_lots)
            qty = num_lots * lot_size

        if max_lots > 0:
            qty = min(int(qty), max_lots * lot_size)

        resp = place_order_with_retry(order_symbol, qty, growwapi.TRANSACTION_TYPE_BUY, growwapi.PRODUCT_MIS)
        order_id = resp.get("groww_order_id")
        status = resp.get("order_status", "UNKNOWN")
        if status in {"REJECTED", "CANCELLED", "FAILED"}:
            print(f"[REJECTED] BUY {candle_symbol}")
            return None
        if status in {"NEW", "PENDING", "OPEN"}:
            time.sleep(2)

        final_status, avg_price, _ = verify_order_execution(order_id, candle_symbol, fallback_price=price)
        if final_status in {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
            exec_price = avg_price if avg_price and avg_price > 0 else price
            candle_low = float(entry_candle_low if entry_candle_low is not None else entry_candle_open)
            loss_stop_pips = float(CONFIG.get("loss_stop_points", CONFIG.get("stop_loss_points", 0)) or 0)
            positions[candle_symbol] = {
                "status": "OPEN",
                "entry_price": exec_price,
                "entry_candle_open": float(entry_candle_open),
                "entry_candle_low": candle_low,
                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "qty": int(qty),
                "order_symbol": order_symbol,
                "product_type": "MIS",
                "side": candle_to_side.get(candle_symbol),
                "highest_price": exec_price,
                "stop_loss_price": candle_low + loss_stop_pips,
                "trailing_sl_activated": False,
                "trailing_stop_price": exec_price - float(
                    CONFIG.get(
                        "trailing_stop_loss_points",
                        CONFIG.get("trailing_stop_gap_points", 8),
                    )
                ),
            }
            increment_trade_count(candle_symbol)
            clear_pullback_reentry(candle_symbol)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] [BUY CONFIRMED] {candle_symbol} Qty={int(qty)} Price={exec_price:.2f} EntryCandleOpen={float(entry_candle_open):.2f} EntryCandleLow={candle_low:.2f} OrderID={order_id}")
            print(f"[{ts}] [SL LEVELS] {candle_symbol} | LSL@{(candle_low + loss_stop_pips):.2f} | MX@{exec_price + CONFIG['max_profit_booking_points']:.2f}")
            return order_id
        print(f"[BUY FAIL] {candle_symbol} final status={final_status}")
        return None
    except Exception as exc:
        print(f"[ERROR] BUY {candle_symbol}: {exc}")
        return None


def place_sell_order(candle_symbol, order_symbol, price, reason="Manual", qty=None):
    try:
        cooldown_sec = int(CONFIG.get("exit_order_cooldown_sec", 90))
        now_ts = datetime.now()
        last_ts = exit_attempt_tracker.get(candle_symbol)
        if last_ts and (now_ts - last_ts).total_seconds() < cooldown_sec:
            return None

        if candle_symbol not in positions or positions[candle_symbol].get("status") != "OPEN":
            return None

        live_positions = get_live_open_positions()
        if candle_symbol not in live_positions:
            positions[candle_symbol]["status"] = "CLOSED"
            exit_attempt_tracker.pop(candle_symbol, None)
            return None

        pos = positions[candle_symbol]
        live_qty = int(live_positions[candle_symbol].get("qty", 0))
        live_product_type = live_positions[candle_symbol].get("product_type", "MIS")
        qty_units = live_qty if qty is None else min(int(qty), live_qty)
        if qty_units <= 0:
            return None

        actual_symbol = live_positions[candle_symbol].get("order_symbol", order_symbol)
        product_constant = resolve_product_constant(live_product_type)
        exit_attempt_tracker[candle_symbol] = now_ts
        exit_reason_tracker[candle_symbol] = reason

        resp = place_order_with_retry(actual_symbol, qty_units, growwapi.TRANSACTION_TYPE_SELL, product_constant)
        order_id = resp.get("groww_order_id")
        status = resp.get("order_status", "UNKNOWN")
        if status in {"REJECTED", "CANCELLED", "FAILED"}:
            print(f"[ORDER REJECTED] SELL {candle_symbol} Qty={qty_units} | Status={status}")
            exit_attempt_tracker.pop(candle_symbol, None)
            exit_reason_tracker.pop(candle_symbol, None)
            return None
        if status in {"NEW", "PENDING", "OPEN"}:
            time.sleep(2)

        final_status, avg_price, _ = verify_order_execution(order_id, candle_symbol, fallback_price=price)
        if final_status in {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
            exec_price = avg_price if avg_price and avg_price > 0 else price
            entry_price = float(pos.get("entry_price", exec_price))
            pnl = (exec_price - entry_price) * qty_units
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] [EXIT CONFIRMED] {candle_symbol} x{qty_units} | Reason={reason} | PnL {pnl:.0f}")
            update_daily_pnl(pnl)
            positions[candle_symbol]["status"] = "CLOSED"
            positions[candle_symbol]["exit_price"] = exec_price
            positions[candle_symbol]["exit_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            positions[candle_symbol]["pnl"] = pnl
            positions[candle_symbol]["qty"] = 0
            arm_pullback_reentry(candle_symbol, reason, exec_price, ts)

            side = candle_to_side.get(candle_symbol)
            if side in side_cooldown_tracker:
                side_cooldown_tracker[side] = int(CONFIG.get("buy_cooldown_candles", 0) or 0)
            return order_id
        return None
    except Exception as exc:
        exit_attempt_tracker.pop(candle_symbol, None)
        exit_reason_tracker.pop(candle_symbol, None)
        print(f"[ERROR] SELL {candle_symbol}: {exc}")
        return None


def option_has_flip_down_exit_signal(candle_symbol, idx_time, allow_debug_tracking=False):
    df, err = fetch_recent_candles(candle_symbol, "SEGMENT_FNO")
    if df is None or df.empty or len(df) < CONFIG["atr_period"] + 2:
        return False, None, None, "NO_SIGNAL"

    closed_idx = len(df) - 2
    prev_idx = closed_idx - 1
    if prev_idx < 0:
        return False, None, None, "NO_SIGNAL"

    st_arr, dir_arr = supertrend(df, CONFIG["atr_period"], CONFIG["factor"])
    close_price = float(df["close"].iloc[closed_idx])
    st_value = float(st_arr[closed_idx]) if not np.isnan(st_arr[closed_idx]) else None
    trend = "up" if dir_arr[closed_idx] == 1 else "down"
    fresh_flip_down = dir_arr[closed_idx] == -1 and dir_arr[prev_idx] == 1
    position_open = bool(positions.get(candle_symbol, {}).get("status") == "OPEN")
    debug_position_open = bool(
        allow_debug_tracking
        and CONFIG.get("debug_signal_tracking_enabled", False)
        and candle_symbol in debug_signal_positions
    )
    tracked_open = position_open or debug_position_open
    signal = "SELL" if fresh_flip_down and tracked_open else "NO_SIGNAL"
    return fresh_flip_down and tracked_open, close_price, st_value, signal


def track_debug_buy_signal(candle_symbol, order_symbol, price, idx_time, entry_candle_open=None, entry_candle_low=None, strike=None):
    if not CONFIG.get("debug_signal_tracking_enabled", False):
        return
    side = candle_to_side.get(candle_symbol) or infer_side_from_symbol(candle_symbol)
    trail_gap = float(
        CONFIG.get(
            "trailing_stop_loss_points",
            CONFIG.get("trailing_stop_gap_points", 8),
        ) or 8
    )
    entry_open = float(entry_candle_open if entry_candle_open is not None else price)
    entry_low = float(entry_candle_low if entry_candle_low is not None else entry_open)
    debug_signal_positions[candle_symbol] = {
        "order_symbol": order_symbol,
        "side": side,
        "entry_price": float(price),
        "entry_time": idx_time,
        "entry_candle_open": entry_open,
        "entry_candle_low": entry_low,
        "highest_price": float(price),
        "trailing_sl_activated": False,
        "trailing_stop_price": float(price) - trail_gap,
        "strike": strike,
    }
    clear_pullback_reentry(candle_symbol)
    print(f"[{idx_time}] [DEBUG BUY SIGNAL TRACKED] {candle_symbol} OrderSymbol={order_symbol} Price={float(price):.2f} Side={side}")


def check_debug_sell_signals(idx_time):
    if not CONFIG.get("debug_signal_tracking_enabled", False):
        return
    for candle_symbol, debug_pos in list(debug_signal_positions.items()):
        if candle_symbol in positions and positions[candle_symbol].get("status") == "OPEN":
            debug_signal_positions.pop(candle_symbol, None)
            continue

        current_price = fetch_latest_price_1m(candle_symbol)
        if current_price is not None:
            entry_price = float(debug_pos.get("entry_price", 0) or 0)
            max_profit_points = float(CONFIG.get("max_profit_booking_points", 0) or 0)
            if (
                bool(CONFIG.get("max_profit_booking_enabled", True))
                and entry_price > 0
                and max_profit_points > 0
                and float(current_price) >= entry_price + max_profit_points
            ):
                arm_pullback_reentry(candle_symbol, f"MX full profit: +{max_profit_points:g}", float(current_price), idx_time)
                print(
                    f"[{idx_time}] [DEBUG SELL SIGNAL] {candle_symbol} "
                    f"TrackedBuy={entry_price:.2f} ExitPrice={float(current_price):.2f} "
                    f"Reason=MX +{max_profit_points:g}"
                )
                debug_signal_positions.pop(candle_symbol, None)
                continue

            entry_low = float(debug_pos.get("entry_candle_low", debug_pos.get("entry_candle_open", entry_price)) or 0)
            loss_stop_points = float(CONFIG.get("loss_stop_points", CONFIG.get("stop_loss_points", 0)) or 0)
            if entry_low > 0 and float(current_price) <= entry_low + loss_stop_points:
                clear_pullback_reentry(candle_symbol)
                print(
                    f"[{idx_time}] [DEBUG SELL SIGNAL] {candle_symbol} "
                    f"TrackedBuy={entry_price:.2f} ExitPrice={float(current_price):.2f} "
                    f"Reason=LSL"
                )
                debug_signal_positions.pop(candle_symbol, None)
                continue

            if CONFIG.get("trailing_stop_enabled", True) and update_debug_trailing_stop_and_check(candle_symbol, float(current_price)):
                clear_pullback_reentry(candle_symbol)
                print(
                    f"[{idx_time}] [DEBUG SELL SIGNAL] {candle_symbol} "
                    f"TrackedBuy={entry_price:.2f} ExitPrice={float(current_price):.2f} "
                    f"Reason=TSL"
                )
                debug_signal_positions.pop(candle_symbol, None)
                continue

        should_exit, exit_price, st_value, exit_signal = option_has_flip_down_exit_signal(candle_symbol, idx_time, allow_debug_tracking=True)
        if should_exit and exit_signal == "SELL":
            clear_pullback_reentry(candle_symbol)
            st_txt = f"{st_value:.2f}" if st_value is not None else "NA"
            print(f"[{idx_time}] [DEBUG SELL SIGNAL] {candle_symbol} TrackedBuy={debug_pos['entry_price']:.2f} ExitPrice={exit_price:.2f} ST={st_txt}")
            debug_signal_positions.pop(candle_symbol, None)


def option_has_flip_buy_signal(candle_symbol, idx_time):
    df, err = fetch_recent_candles(candle_symbol, "SEGMENT_FNO")
    if df is None or df.empty or len(df) < CONFIG["atr_period"] + 2:
        return False, None, None, None, None, None, "NO_SIGNAL"

    closed_idx = len(df) - 2
    prev_idx = closed_idx - 1
    if prev_idx < 0:
        return False, None, None, None, None, None, "NO_SIGNAL"

    st_arr, dir_arr = supertrend(df, CONFIG["atr_period"], CONFIG["factor"])
    entry_open = float(df["open"].iloc[closed_idx])
    entry_low = float(df["low"].iloc[closed_idx])
    close_price = float(df["close"].iloc[closed_idx])
    prev_close = float(df["close"].iloc[prev_idx])
    prev_high = float(df["high"].iloc[prev_idx])
    st_value = float(st_arr[closed_idx]) if not np.isnan(st_arr[closed_idx]) else None
    trend = "up" if dir_arr[closed_idx] == 1 else "down"

    fresh_flip = dir_arr[closed_idx] == 1 and dir_arr[prev_idx] == -1

    window_left = int(CONFIG.get("flip_confirmation_window_candles", 1) or 0)
    allow_flip = fresh_flip
    if window_left > 1:
        last_attempt_time = buy_signal_attempt_tracker.get(candle_symbol)
        allow_flip = last_attempt_time == idx_time or fresh_flip

    momentum_ok = True
    if CONFIG.get("use_momentum_filter", True):
        momentum_ok = (close_price > prev_close) if CONFIG.get("require_close_above_prev_close", True) else True

    open_gap_ok = abs(entry_open - prev_close) <= float(CONFIG.get("entry_open_gap_limit_points", 10.0))

    reentry_candidate = pullback_reentry_tracker.get(candle_symbol)
    reentry_ok = False
    if reentry_candidate is not None:
        if trend != "up":
            clear_pullback_reentry(candle_symbol)
        else:
            touch_tolerance = float(CONFIG.get("pullback_reentry_touch_tolerance_points", 3.0) or 0)
            touched_st = st_value is not None and entry_low <= (st_value + touch_tolerance)
            reclaimed_uptrend = st_value is not None and close_price > st_value
            continuation_ok = close_price > prev_high and close_price >= entry_open
            reentry_ok = bool(touched_st and reclaimed_uptrend and continuation_ok and momentum_ok and open_gap_ok)

    allow_buy = bool((allow_flip and momentum_ok and open_gap_ok) or reentry_ok)
    signal = "BUY_REENTRY" if reentry_ok else ("BUY" if allow_buy else "NO_SIGNAL")

    if CONFIG.get("DEBUG"):
        st_txt = f"{st_value:.2f}" if st_value is not None else "NA"
        print(
            f"[OPTION CHECK] {candle_symbol} | Flip={allow_flip} | Reentry={reentry_ok} | Momentum={momentum_ok} | "
            f"OpenGapOK={open_gap_ok} | Close={close_price:.2f} PrevClose={prev_close:.2f} Open={entry_open:.2f} ST={st_txt}"
        )

    return allow_buy, close_price, entry_open, entry_low, st_value, trend, signal


def update_cooldowns_once_per_candle():
    for side in list(side_cooldown_tracker.keys()):
        remaining = int(side_cooldown_tracker.get(side, 0)) - 1
        if remaining <= 0:
            side_cooldown_tracker[side] = 0
        else:
            side_cooldown_tracker[side] = remaining


def max_profit_booking_monitor_thread():
    while not pnl_log_stop_event.is_set():
        try:
            time.sleep(int(CONFIG.get("max_profit_check_interval_sec", 60)))
            if pnl_log_stop_event.is_set() or not CONFIG.get("max_profit_booking_enabled", True):
                continue

            live_pos = get_live_open_positions()
            for symbol, pos in list(positions.items()):
                if pos.get("status") != "OPEN" or symbol not in live_pos:
                    continue

                entry = float(pos.get("entry_price", 0))
                qty = int(pos.get("qty", 0))
                if entry <= 0 or qty <= 0:
                    continue

                cur = fetch_latest_price_1m(symbol)
                if cur is None:
                    continue

                trigger = entry + float(CONFIG.get("max_profit_booking_points", 25))
                if cur >= trigger:
                    print(f"[MX EXIT] {symbol}: Entry={entry:.2f}, Now={cur:.2f}, Trigger>={trigger:.2f}")
                    place_sell_order(symbol, pos.get("order_symbol"), cur, reason=f"MX full profit: +{CONFIG.get('max_profit_booking_points', 25)}", qty=qty)
        except Exception as exc:
            print(f"[ERROR] MX monitor: {exc}")


def start_max_profit_booking_monitor():
    thread = threading.Thread(target=max_profit_booking_monitor_thread, daemon=True)
    thread.start()
    return thread


def wait_until_next_interval():
    now = datetime.now()
    interval_str = CONFIG["candle_interval"].upper()
    interval_min = int(interval_str.replace("M", "")) if "M" in interval_str else 1

    cur_min = now.minute
    cur_sec = now.second
    since = cur_min % interval_min
    if since == 0 and cur_sec >= 2:
        next_min = cur_min + interval_min
    else:
        next_min = cur_min + (interval_min - since)
    if next_min >= 60:
        next_min -= 60

    next_time = now.replace(minute=next_min, second=2, microsecond=0)
    if next_time <= now:
        next_time += timedelta(hours=1)

    wait_sec = (next_time - now).total_seconds()
    if wait_sec > 0:
        time.sleep(wait_sec)


def live_signal_loop():
    last_printed_time = None
    # main() already builds the universe before printing startup details.
    # Treat that build as today's initialization to avoid printing it twice.
    last_reset_date = get_trading_now().strftime("%Y-%m-%d")

    while True:
        try:
            clear_loop_market_cache()
            global trades_today_ce, trades_today_pe, daily_realized_pnl, exit_attempt_tracker, exit_reason_tracker, buy_signal_attempt_tracker, debug_signal_positions, pullback_reentry_tracker, side_cooldown_tracker, no_data_warn_tracker

            now_ist = get_trading_now()
            current_date = now_ist.strftime("%Y-%m-%d")
            if last_reset_date != current_date:
                trades_today_ce = 0
                trades_today_pe = 0
                daily_realized_pnl = 0.0
                exit_attempt_tracker = {}
                exit_reason_tracker = {}
                buy_signal_attempt_tracker = {}
                debug_signal_positions = {}
                pullback_reentry_tracker = {}
                side_cooldown_tracker = {"CE": 0, "PE": 0}
                no_data_warn_tracker = {}
                last_reset_date = current_date
                if not build_contract_universe():
                    print("[ERROR] Contract configuration invalid after daily rollover")
                    wait_until_next_interval()
                    continue

            live_positions = get_live_open_positions()
            reconcile_positions_with_groww(live_positions)
            tracked_symbols = get_tracked_candle_symbols(live_positions)
            session_state = get_session_state(now_ist)

            if session_state == "squareoff":
                close_all_open_positions("SESSION_SQUAREOFF_15_30", now_ist.strftime("%Y-%m-%d %H:%M:%S"))
                wait_until_next_interval()
                continue

            if session_state in {"closed_day", "pre_open"}:
                wait_until_next_interval()
                continue

            if session_state != "entry" and not tracked_symbols and not any(int(pos.get("qty", 0)) > 0 for pos in live_positions.values()):
                wait_until_next_interval()
                continue

            idx_df, idx_err = fetch_recent_candles(CONFIG["index_symbol"], "SEGMENT_CASH")
            if idx_df is None or idx_df.empty or len(idx_df) < CONFIG["atr_period"] + 2:
                print(f"[ERROR] No index data: {idx_err or 'unknown'}")
                wait_until_next_interval()
                continue

            idx_last = len(idx_df) - 2
            if idx_last < 1:
                wait_until_next_interval()
                continue

            idx_time = str(idx_df["date"].iloc[idx_last])
            if idx_time == last_printed_time:
                wait_until_next_interval()
                continue
            last_printed_time = idx_time

            update_cooldowns_once_per_candle()

            idx_price = float(idx_df["close"].iloc[idx_last])
            print(f"[IDX] {idx_time} {CONFIG['index_symbol']} Price={idx_price:.2f}")

            check_lsl_exit()
            check_trailing_stop_exit()

            # Exit open positions when option Supertrend flips from up to down.
            for symbol, pos in list(positions.items()):
                if pos.get("status") != "OPEN":
                    continue
                should_exit, exit_price, st_value, exit_signal = option_has_flip_down_exit_signal(symbol, idx_time)
                if should_exit and exit_signal == "SELL":
                    st_txt = f"{st_value:.2f}" if st_value is not None else "NA"
                    print(f"[{idx_time}] [SELL SIGNAL] {symbol} Direction changed to down | ST={st_txt}")
                    place_sell_order(
                        symbol,
                        pos.get("order_symbol"),
                        float(exit_price),
                        reason="ST flip up->down",
                        qty=pos.get("qty"),
                    )

            check_debug_sell_signals(idx_time)

            live_positions = get_live_open_positions()
            reconcile_positions_with_groww(live_positions)

            tracked_symbols = get_tracked_candle_symbols(live_positions)
            if tracked_symbols:
                for tracked_symbol in tracked_symbols:
                    if tracked_symbol in positions and positions[tracked_symbol].get("status") == "OPEN":
                        continue
                    debug_pos = debug_signal_positions.get(tracked_symbol)
                    if not debug_pos:
                        continue
                    tracked_side = candle_to_side.get(tracked_symbol) or debug_pos.get("side")
                    tracked_strike = debug_pos.get("strike")
                    print_flat_market_snapshot(tracked_symbol, tracked_side, idx_time, tracked_strike)
            elif session_state == "entry":
                # Use index only for ATM strike discovery, but entry is option-signal only.
                atm = select_atm_contract("CE", idx_price)
                if not atm:
                    print("[ERROR] No contracts configured")
                else:
                    strike = atm["strike"]
                    ce_contract = next((rec for rec in contract_universe["CE"] if rec["strike"] == strike), None)
                    pe_contract = next((rec for rec in contract_universe["PE"] if rec["strike"] == strike), None)

                    ce_snapshot = None
                    pe_snapshot = None
                    if ce_contract is not None:
                        ce_snapshot = print_flat_market_snapshot(ce_contract["candle_symbol"], "CE", idx_time, strike)
                    if pe_contract is not None:
                        pe_snapshot = print_flat_market_snapshot(pe_contract["candle_symbol"], "PE", idx_time, strike)

                    ce_price = ce_snapshot.get("last_price") if ce_snapshot else None
                    pe_price = pe_snapshot.get("last_price") if pe_snapshot else None
                    if ce_price is None and pe_price is None:
                        last_warn = no_data_warn_tracker.get(strike)
                        if last_warn != idx_time:
                            print(
                                f"[WARN] No option candle data for Strike={strike}. "
                                f"Check expiry_fs='{CONFIG.get('expiry_fs')}' and "
                                f"order_expiry='{CONFIG.get('order_expiry')}' or switch to current weekly/monthly expiry."
                            )
                            no_data_warn_tracker[strike] = idx_time

                    ce_ok = bool(ce_snapshot and ce_snapshot.get("ok") and ce_snapshot.get("signal") in {"BUY", "BUY_REENTRY"} and ce_snapshot.get("last_price") is not None and ce_snapshot.get("entry_open") is not None)
                    pe_ok = bool(pe_snapshot and pe_snapshot.get("ok") and pe_snapshot.get("signal") in {"BUY", "BUY_REENTRY"} and pe_snapshot.get("last_price") is not None and pe_snapshot.get("entry_open") is not None)

                    if ce_ok and int(side_cooldown_tracker.get("CE", 0)) == 0:
                        candle_symbol = ce_snapshot.get("candle_symbol")
                        order_symbol = candle_to_order.get(candle_symbol)
                        last_attempt_time = buy_signal_attempt_tracker.get(candle_symbol)
                        if order_symbol and last_attempt_time != idx_time:
                            buy_signal_attempt_tracker[candle_symbol] = idx_time
                            if ce_snapshot.get("signal") == "BUY_REENTRY":
                                print(f"[{idx_time}] [REENTRY SIGNAL] {candle_symbol} same strike pullback continuation")
                            track_debug_buy_signal(
                                candle_symbol,
                                order_symbol,
                                ce_snapshot.get("last_price"),
                                idx_time,
                                entry_candle_open=ce_snapshot.get("entry_open"),
                                entry_candle_low=ce_snapshot.get("entry_low"),
                                strike=ce_snapshot.get("strike"),
                            )
                            place_buy_order(
                                candle_symbol,
                                order_symbol,
                                ce_snapshot.get("last_price"),
                                ce_snapshot.get("entry_open"),
                                entry_candle_low=ce_snapshot.get("entry_low"),
                            )
                    elif pe_ok and int(side_cooldown_tracker.get("PE", 0)) == 0:
                        candle_symbol = pe_snapshot.get("candle_symbol")
                        order_symbol = candle_to_order.get(candle_symbol)
                        last_attempt_time = buy_signal_attempt_tracker.get(candle_symbol)
                        if order_symbol and last_attempt_time != idx_time:
                            buy_signal_attempt_tracker[candle_symbol] = idx_time
                            if pe_snapshot.get("signal") == "BUY_REENTRY":
                                print(f"[{idx_time}] [REENTRY SIGNAL] {candle_symbol} same strike pullback continuation")
                            track_debug_buy_signal(
                                candle_symbol,
                                order_symbol,
                                pe_snapshot.get("last_price"),
                                idx_time,
                                entry_candle_open=pe_snapshot.get("entry_open"),
                                entry_candle_low=pe_snapshot.get("entry_low"),
                                strike=pe_snapshot.get("strike"),
                            )
                            place_buy_order(
                                candle_symbol,
                                order_symbol,
                                pe_snapshot.get("last_price"),
                                pe_snapshot.get("entry_open"),
                                entry_candle_low=pe_snapshot.get("entry_low"),
                            )

            wait_until_next_interval()

        except Exception as exc:
            print(f"[ERROR] Signal loop: {type(exc).__name__}: {exc}")
            time.sleep(10)


def main():
    try:
        if not build_contract_universe():
            print("[ERROR] Contract configuration invalid. Fix expiry and strike range.")
            raise SystemExit(1)

        margin = get_available_fno_margin()
        if margin is None:
            print("[MARGIN] Available margin: N/A")
        else:
            print(f"[MARGIN] Available margin: {margin:.2f}")

        live_positions = get_live_open_positions()
        reconcile_positions_with_groww(live_positions)

        mx_thread = start_max_profit_booking_monitor()

        try:
            live_signal_loop()
        except KeyboardInterrupt:
            pnl_log_stop_event.set()
            mx_thread.join(timeout=2)
    except Exception:
        raise


if __name__ == "__main__":
    main()