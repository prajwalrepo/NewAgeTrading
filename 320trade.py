# =============================================================================
# 320trade.py - Expiry-day ATM CE+PE straddle on Groww API
#
# Flow:
# 1) Check whether today matches a configured NIFTY or SENSEX expiry.
# 2) Prefer the first instrument in expiry_priority that has a today expiry.
# 3) At 15:20 IST, buy both ATM CE and ATM PE for that instrument.
# 4) Exit any leg immediately if profit reaches +200% or loss reaches -75%.
# 5) Square off all remaining legs exactly at 15:30 IST.
# =============================================================================

from datetime import datetime, timedelta, time as dt_time, timezone
import math
import re
import threading
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from growwapi import GrowwAPI

warnings.filterwarnings("ignore", category=FutureWarning)


LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
TRADE_LOG_FILE = LOG_DIR / f"320trade_{datetime.now().strftime('%Y%m%d')}.log"


CONFIG = {
   "api_key": "eyJraWQiOiJaTUtjVXciLCJhbGciOiJFUzI1NiJ9.eyJleHAiOjI1Njg5NDU3MzIsImlhdCI6MTc4MDU0NTczMiwibmJmIjoxNzgwNTQ1NzMyLCJzdWIiOiJ7XCJ0b2tlblJlZklkXCI6XCI5MjM4NTRkYS0xODFmLTRmNmYtOGFkMi00M2MzMzdlM2ZhZWJcIixcInZlbmRvckludGVncmF0aW9uS2V5XCI6XCJlMzFmZjIzYjA4NmI0MDZjODg3NGIyZjZkODQ5NTMxM1wiLFwidXNlckFjY291bnRJZFwiOlwiY2QxYjY2MjMtY2MzOS00N2Q0LTkxNWYtZmVlZTE3ZjFkNTRmXCIsXCJkZXZpY2VJZFwiOlwiNjA5NWQ4NTYtZGE1My01Y2Q2LWJlZGUtMGFmZWI4NzI1N2U0XCIsXCJzZXNzaW9uSWRcIjpcIjA4MGQ0YjQ0LWJjZWEtNGI3ZC05MjcyLTMxYmJmNDI5MzUwOFwiLFwiYWRkaXRpb25hbERhdGFcIjpcIno1NC9NZzltdjE2WXdmb0gvS0EwYlAveUhIeXlXNVZKSmNyMno5V0JqMlpSTkczdTlLa2pWZDNoWjU1ZStNZERhWXBOVi9UOUxIRmtQejFFQisybTdRPT1cIixcInJvbGVcIjpcImF1dGgtdG90cFwiLFwic291cmNlSXBBZGRyZXNzXCI6XCIxMzYuMjI2LjI1My45MCwxNzIuNjkuMTIyLjE3NCwzNS4yNDEuMjMuMTIzXCIsXCJ0d29GYUV4cGlyeVRzXCI6MjU2ODk0NTczMjkzNSxcInZlbmRvck5hbWVcIjpcImdyb3d3QXBpXCJ9IiwiaXNzIjoiYXBleC1hdXRoLXByb2QtYXBwIn0.5lRo2nlPb4v5xdAp038NbKL7F5vkFFijwMNXrV4vLghxAAWrMQkAZx3CVx8zqIyySd5AQXzHTdT_-lAWB2lJLg",
           "api_secret": "nwS5I3ex3!AB7jIPuOrFLYDXXAq!X&O)",

    # Which instrument to trade when both have expiry today.
    "expiry_priority": ["NIFTY", "SENSEX"],

    # Entry / exit schedule in IST.
    "trading_timezone_offset_minutes": 330,
    "entry_window_start": "15:19",
    "entry_window_end": "15:20",
    "strike_decision_time": "15:14",
    "fixed_strike_price": 315,
    "force_exit_time": "15:30",
    "trading_weekdays": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"],

    # Profit / loss exits on the option premium itself.
    # +200% means the option premium reaches 3x the entry price.
    # -75% means the option premium falls to 25% of the entry price.
    "take_profit_percent": 200.0,
    "stop_loss_percent": 75.0,

    # Order sizing.
    # Fallback lot size used only if instrument lot size is not configured.
    "lot_size": 1,
    "lots_per_leg": 1,
    # Keep True to place real broker orders.
    "enable_actual_trade": True,
    "check_margin_before_order": True,
    # Keep True to continue paper logging when real orders fail.
    "debug_paper_tracking_enabled": True,
    "paper_trade_fallback_enabled": True,
    "api_rate_limit_retry_delay": 5,
    "api_rate_limit_max_retries": 3,

    # Instrument configuration.
    "nifty": {
        "root": "NIFTY",
        "lot_size": 65,
        "exchange": "NSE",
        "segment": "SEGMENT_FNO",
        "index_segment": "SEGMENT_CASH",
        "candle_interval": "1M",
        "atr_period": 10,
        "strike_step": 50,
        "strike_start": 20000,
        "strike_end": 28000,
        "expiry_fs": ["06Oct26", "13Oct26", "19Oct26", "27Oct26", "03Nov26"],
        "order_expiry": ["26O06", "26O13", "26O19", "26O27", "26N03"],
    },
    "sensex": {
        "root": "SENSEX",
        "lot_size": 10,
        "exchange": "BSE",
        "segment": "SEGMENT_FNO",
        "index_segment": "SEGMENT_CASH",
        "candle_interval": "1M",
        "atr_period": 10,
        "strike_step": 100,
        "strike_start": 60000,
        "strike_end": 80000,
        "expiry_fs": ["08Oct26", "15Oct26", "22Oct26", "29Oct26", "05Nov26"],
        "order_expiry": ["26O08", "26O15", "26O22", "26O29", "26N05"],
    },
}


def log_event(message):
    text = f"[{get_ist_now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(text)
    try:
        with TRADE_LOG_FILE.open("a", encoding="utf-8") as log_handle:
            log_handle.write(text + "\n")
    except Exception:
        pass


def _make_paper_leg(order_symbol, price, reason="PAPER", qty=None):
    lot_size = int(CONFIG.get("lot_size", 1) or 1)
    lots_per_leg = int(CONFIG.get("lots_per_leg", 1) or 1)
    return {
        "order_symbol": order_symbol,
        "qty": int(qty or lot_size * lots_per_leg),
        "entry_price": float(price),
        "entry_time": get_ist_now().strftime("%Y-%m-%d %H:%M:%S"),
        "broker_order_placed": False,
        "paper_only": True,
        "exit_reason": None,
        "paper_reason": reason,
    }


def _paper_tracking_enabled():
    return bool(
        CONFIG.get("debug_paper_tracking_enabled", False)
        or CONFIG.get("paper_trade_fallback_enabled", False)
    )


def _resolve_lot_size(instrument_cfg):
    try:
        lot_size = int((instrument_cfg or {}).get("lot_size", CONFIG.get("lot_size", 1)) or 1)
    except Exception:
        lot_size = int(CONFIG.get("lot_size", 1) or 1)
    return max(lot_size, 1)


def _estimated_pnl(entry_price, current_price, qty):
    return (float(current_price) - float(entry_price)) * int(qty)


CANDLE_INTERVAL_MAP = {
    "1M": "1minute", "2M": "2minute", "3M": "3minute",
    "5M": "5minute", "10M": "10minute", "15M": "15minute",
    "30M": "30minute", "1H": "1hour", "4H": "4hour",
    "1D": "1day", "1W": "1week", "1MO": "1month",
}

WEEKDAY_NAME_TO_INT = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


def _normalize_access_token(token_value):
    if isinstance(token_value, str):
        token = token_value.strip()
        if token:
            return token
        raise ValueError("Empty access token")
    if isinstance(token_value, dict):
        for key in ("access_token", "token", "jwt", "value"):
            value = token_value.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
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


def get_ist_now():
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
    now_dt = now_dt or get_ist_now()
    allowed_weekdays = _get_allowed_weekdays()
    if allowed_weekdays and now_dt.weekday() not in allowed_weekdays:
        return "closed_day"

    current_time = dt_time(now_dt.hour, now_dt.minute, now_dt.second)
    strike_decision = _parse_hhmm(CONFIG.get("strike_decision_time", "15:14"))
    entry_start = _parse_hhmm(CONFIG.get("entry_window_start", "15:20"))
    entry_end = _parse_hhmm(CONFIG.get("entry_window_end", "15:21"))
    force_exit = _parse_hhmm(CONFIG.get("force_exit_time", "15:30"))

    if current_time < strike_decision:
        return "pre_open"
    if current_time < entry_start:
        return "select_strike"
    if current_time < entry_end:
        return "entry"
    if current_time < force_exit:
        return "manage_only"
    return "squareoff"


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


def _normalize_expiry_entries(instrument_key):
    cfg = CONFIG.get(instrument_key, {})
    expiry_cfg = cfg.get("expiry_fs")
    order_cfg = cfg.get("order_expiry")

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
            print(f"[WARN] Invalid expiry format '{candle_expiry}' for {instrument_key}. Expected DDMonYY.")
            continue
        entries.append(
            {
                "candle_expiry": candle_expiry,
                "order_expiry": order_expiry,
                "expiry_dt": expiry_dt,
            }
        )
    return entries


def _select_today_expiry(entries, now_dt=None):
    if not entries:
        return None
    now_dt = now_dt or get_ist_now()
    today = now_dt.date()
    for entry in entries:
        if entry["expiry_dt"].date() == today:
            return entry
    return None


def _select_instrument_for_today(now_dt=None):
    now_dt = now_dt or get_ist_now()
    selected = []
    for instrument_key in CONFIG.get("expiry_priority", ["NIFTY", "SENSEX"]):
        key = str(instrument_key or "").strip().lower()
        if key not in CONFIG:
            continue
        entries = _normalize_expiry_entries(key)
        today_entry = _select_today_expiry(entries, now_dt=now_dt)
        if today_entry is not None:
            selected.append((str(instrument_key).upper(), key, today_entry))
    if not selected:
        return None
    priority_map = {str(name).upper(): idx for idx, name in enumerate(CONFIG.get("expiry_priority", []))}
    selected.sort(key=lambda item: priority_map.get(item[0], 999))
    return selected[0]


def get_order_expiry_text(expiry_text):
    return str(expiry_text or "").strip()


def build_candle_symbol(root, expiry_fs, strike, side):
    return f"{str(root).upper()}-{expiry_fs}-{int(strike)}-{str(side).upper()}"


def build_order_symbol(root, order_expiry_text, strike, side):
    return f"{str(root).upper()}{get_order_expiry_text(order_expiry_text)}{int(strike)}{str(side).upper()}"


def fetch_recent_candles(symbol, segment, exchange, candle_interval, lookback_minutes=30):
    interval_key = CANDLE_INTERVAL_MAP[candle_interval.upper()]
    groww_symbol = symbol if symbol.startswith(f"{exchange}-") else f"{exchange}-{symbol}"
    end_time = datetime.now()
    start_time = end_time - timedelta(minutes=int(lookback_minutes))

    retry_delay = int(CONFIG.get("api_rate_limit_retry_delay", 5))
    max_retries = int(CONFIG.get("api_rate_limit_max_retries", 3))
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
                return _build_dataframe(candles), None
            return None, "No candle data"
        except Exception as exc:
            if "rate limit" in str(exc).lower() and retry_count < max_retries:
                retry_count += 1
                time.sleep(retry_delay)
                continue
            return None, f"{type(exc).__name__}: {exc}"

    return None, "Unknown candle fetch failure"


def fetch_latest_price_1m(symbol, exchange, segment):
    try:
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=5)
        groww_symbol = symbol if symbol.startswith(f"{exchange}-") else f"{exchange}-{symbol}"
        raw = growwapi.get_historical_candles(
            groww_symbol=groww_symbol,
            exchange=getattr(growwapi, f"EXCHANGE_{exchange}"),
            segment=getattr(growwapi, segment),
            candle_interval="1minute",
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        candles = _extract_candle_rows(raw)
        if candles:
            df = _build_dataframe(candles)
            if not df.empty:
                return float(df["close"].iloc[-1])
    except Exception as exc:
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


def _infer_side_from_symbol(symbol):
    s = (symbol or "").upper()
    if s.endswith("CE"):
        return "CE"
    if s.endswith("PE"):
        return "PE"
    return None


def get_live_open_positions():
    raw = get_open_fno_positions()
    live = {}
    for pos in raw:
        order_symbol = pos.get("trading_symbol") or pos.get("symbol")
        qty = int(pos.get("quantity", 0))
        entry_price = float(pos.get("average_price", pos.get("net_price", 0)) or 0)
        if not order_symbol or qty <= 0:
            continue
        live[order_symbol] = {
            "qty": qty,
            "entry_price": entry_price,
            "order_symbol": order_symbol,
            "product_type": pos.get("product") or pos.get("product_type", "MIS"),
            "status": "OPEN",
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "side": _infer_side_from_symbol(order_symbol),
        }
    return live


def get_available_fno_margin():
    try:
        resp = growwapi.get_available_margin_details()
        if not isinstance(resp, dict):
            return None
        segment = resp.get("fno_margin_details")
        if isinstance(segment, dict):
            for key in ("future_balance_available", "option_buying_power", "available_margin", "margin_available"):
                try:
                    if key in segment:
                        return float(segment[key])
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        print(f"[ERROR] Failed to fetch margin: {type(exc).__name__}: {exc}")
    return None


def place_order_with_retry(trading_symbol, quantity, transaction_type, product_type=None):
    if product_type is None:
        product_type = growwapi.PRODUCT_MIS

    retry_count = 0
    max_retries = int(CONFIG.get("api_rate_limit_max_retries", 3))
    side = "BUY" if transaction_type == growwapi.TRANSACTION_TYPE_BUY else "SELL"

    while retry_count <= max_retries:
        try:
            resp = growwapi.place_order(
                trading_symbol=trading_symbol,
                quantity=int(quantity),
                validity=growwapi.VALIDITY_DAY,
                exchange=getattr(growwapi, f"EXCHANGE_{CONFIG['instrument_exchange']}"),
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
                    print(f"[ORDER CONFIRMED] {side} {trading_symbol} Qty={int(quantity)} | OrderID={order_id} | Status={status}")
                elif status in {"REJECTED", "CANCELLED", "FAILED"}:
                    print(f"[ORDER REJECTED] {trading_symbol} | Status={status} | Error: {msg}")
                else:
                    print(f"[ORDER SUBMITTED] {side} {trading_symbol} Qty={int(quantity)} | OrderID={order_id} | Status={status}")
            return resp
        except Exception as exc:
            if "rate limit" in str(exc).lower() and retry_count < max_retries:
                retry_count += 1
                time.sleep(int(CONFIG.get("api_rate_limit_retry_delay", 5)))
            else:
                raise


def verify_order_execution(order_id, fallback_price=None):
    try:
        details = growwapi.get_order_detail(groww_order_id=order_id, segment=growwapi.SEGMENT_FNO)
        final_status = details.get("order_status", "UNKNOWN")
        avg_price = details.get("average_fill_price") or details.get("price")
        avg_price = float(avg_price) if avg_price else (fallback_price or 0)
        return final_status, avg_price, details
    except Exception as exc:
        print(f"[DEBUG] verify_order_execution failed: {exc}")
        return None, None, None


def resolve_product_constant(product_type):
    pt = (product_type or "MIS").upper()
    return {
        "MIS": growwapi.PRODUCT_MIS,
        "NRML": growwapi.PRODUCT_NRML,
        "CNC": growwapi.PRODUCT_CNC,
    }.get(pt, growwapi.PRODUCT_MIS)


def place_buy_order(order_symbol, price, exchange, segment, entry_price_label, instrument_cfg=None):
    lot_size = _resolve_lot_size(instrument_cfg)
    lots_per_leg = int(CONFIG.get("lots_per_leg", 1) or 1)
    qty = lot_size * lots_per_leg

    log_event(f"[BUY ATTEMPT] {order_symbol} Qty={qty} EstEntry={float(price):.2f} Source={entry_price_label}")

    if not bool(CONFIG.get("enable_actual_trade", True)):
        log_event(f"[LIVE ORDER DISABLED] {order_symbol} running in paper tracking mode")
        if _paper_tracking_enabled():
            return _make_paper_leg(order_symbol, price, reason="LIVE_ORDER_DISABLED", qty=qty)
        return None

    margin = get_available_fno_margin()
    if CONFIG.get("check_margin_before_order", True) and margin is None:
        log_event(f"[SKIP] Margin unavailable for {order_symbol}")
        if _paper_tracking_enabled():
            log_event(f"[PAPER ENTRY] {order_symbol} using estimated entry price because broker margin is unavailable")
            return _make_paper_leg(order_symbol, price, reason="MARGIN_UNAVAILABLE", qty=qty)
        return None
    if margin is not None and margin <= 0:
        log_event(f"[BUY BLOCKED] {order_symbol}: no usable margin budget ({margin:.2f})")
        if _paper_tracking_enabled():
            log_event(f"[PAPER ENTRY] {order_symbol} using estimated entry price because usable margin is zero")
            return _make_paper_leg(order_symbol, price, reason="NO_MARGIN", qty=qty)
        return None

    try:
        resp = place_order_with_retry(order_symbol, qty, growwapi.TRANSACTION_TYPE_BUY, growwapi.PRODUCT_MIS)
    except Exception as exc:
        log_event(f"[ERROR] BUY {order_symbol}: {exc}")
        if _paper_tracking_enabled():
            log_event(f"[PAPER ENTRY] {order_symbol} recorded because broker BUY request failed")
            return _make_paper_leg(order_symbol, price, reason="BUY_REQUEST_FAILED", qty=qty)
        return None

    order_id = resp.get("groww_order_id") if isinstance(resp, dict) else None
    status = resp.get("order_status", "UNKNOWN") if isinstance(resp, dict) else "UNKNOWN"
    if status in {"REJECTED", "CANCELLED", "FAILED"} or not order_id:
        log_event(f"[BUY FAIL] {order_symbol} status={status} order_id={order_id or 'NA'}")
        if _paper_tracking_enabled():
            log_event(f"[PAPER ENTRY] {order_symbol} recorded because broker buy did not fill")
            return _make_paper_leg(order_symbol, price, reason=f"BROKER_{status}", qty=qty)
        return None
    if status in {"NEW", "PENDING", "OPEN"}:
        time.sleep(2)

    final_status, avg_price, _ = verify_order_execution(order_id, fallback_price=price)
    if final_status in {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
        exec_price = avg_price if avg_price and avg_price > 0 else price
        log_event(f"[BUY CONFIRMED] {order_symbol} Qty={qty} Entry={exec_price:.2f} {entry_price_label}={price:.2f} OrderID={order_id}")
        return {
            "order_symbol": order_symbol,
            "qty": qty,
            "entry_price": exec_price,
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "broker_order_placed": True,
            "paper_only": False,
        }
    log_event(f"[BUY FAIL] {order_symbol} final status={final_status}")
    if _paper_tracking_enabled():
        log_event(f"[PAPER ENTRY] {order_symbol} recorded because broker buy execution was not confirmed")
        return _make_paper_leg(order_symbol, price, reason=f"UNCONFIRMED_{final_status}", qty=qty)
    return None


def place_sell_order(order_symbol, price, reason, qty):
    try:
        log_event(f"[SELL ATTEMPT] {order_symbol} Qty={qty} Price={float(price):.2f} Reason={reason}")
        resp = place_order_with_retry(order_symbol, qty, growwapi.TRANSACTION_TYPE_SELL, growwapi.PRODUCT_MIS)
        order_id = resp.get("groww_order_id") if isinstance(resp, dict) else None
        status = resp.get("order_status", "UNKNOWN") if isinstance(resp, dict) else "UNKNOWN"
        if status in {"REJECTED", "CANCELLED", "FAILED"} or not order_id:
            log_event(f"[ORDER REJECTED] SELL {order_symbol} Qty={qty} | Status={status}")
            return None
        if status in {"NEW", "PENDING", "OPEN"}:
            time.sleep(2)

        final_status, avg_price, _ = verify_order_execution(order_id, fallback_price=price)
        if final_status in {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
            exec_price = avg_price if avg_price and avg_price > 0 else price
            log_event(f"[EXIT CONFIRMED] {order_symbol} x{qty} | Reason={reason} | Exit={exec_price:.2f} OrderID={order_id}")
            return order_id
        log_event(f"[SELL FAIL] {order_symbol} final status={final_status}")
        return None
    except Exception as exc:
        log_event(f"[ERROR] SELL {order_symbol}: {exc}")
        return None


def get_latest_index_price(instrument_cfg, use_previous_close=False):
    symbol = instrument_cfg["root"]
    exchange = instrument_cfg["exchange"]
    segment = instrument_cfg.get("index_segment", "SEGMENT_CASH")
    candle_interval = instrument_cfg["candle_interval"]
    df, err = fetch_recent_candles(symbol, segment, exchange, candle_interval, lookback_minutes=60)
    if df is None or df.empty:
        print(f"[ERROR] No index data for {symbol}: {err or 'unknown'}")
        return None, None, None

    idx = len(df) - 2 if use_previous_close and len(df) >= 2 else len(df) - 1
    if idx < 0:
        idx = 0

    candle_time = str(df["date"].iloc[idx])
    price = float(df["close"].iloc[idx])
    open_price = float(df["open"].iloc[idx])
    return price, candle_time, open_price


def select_fixed_strike(side):
    fixed = int(CONFIG.get("fixed_strike_price", 315) or 315)
    if side == "CE":
        return fixed
    if side == "PE":
        return fixed
    return fixed


def build_entry_contracts(instrument_key, instrument_cfg, expiry_entry, index_price):
    ce_strike = select_fixed_strike("CE")
    pe_strike = select_fixed_strike("PE")

    if ce_strike < int(instrument_cfg["strike_start"]) or ce_strike > int(instrument_cfg["strike_end"]):
        raise ValueError(f"Fixed CE strike {ce_strike} outside configured range for {instrument_key}")
    if pe_strike < int(instrument_cfg["strike_start"]) or pe_strike > int(instrument_cfg["strike_end"]):
        raise ValueError(f"Fixed PE strike {pe_strike} outside configured range for {instrument_key}")

    root = instrument_cfg["root"]
    expiry_fs = expiry_entry["candle_expiry"]
    order_expiry = expiry_entry["order_expiry"]
    ce_candle = build_candle_symbol(root, expiry_fs, ce_strike, "CE")
    pe_candle = build_candle_symbol(root, expiry_fs, pe_strike, "PE")
    ce_order = build_order_symbol(root, order_expiry, ce_strike, "CE")
    pe_order = build_order_symbol(root, order_expiry, pe_strike, "PE")
    return {
        "instrument": instrument_key,
        "root": root,
        "ce_strike": ce_strike,
        "pe_strike": pe_strike,
        "expiry_fs": expiry_fs,
        "order_expiry": order_expiry,
        "ce": {"candle_symbol": ce_candle, "order_symbol": ce_order},
        "pe": {"candle_symbol": pe_candle, "order_symbol": pe_order},
    }


def entry_window_open(now_dt=None):
    now_dt = now_dt or get_ist_now()
    current_time = dt_time(now_dt.hour, now_dt.minute, now_dt.second)
    return _parse_hhmm(CONFIG.get("entry_window_start", "15:20")) <= current_time < _parse_hhmm(CONFIG.get("entry_window_end", "15:21"))


def squareoff_window_open(now_dt=None):
    now_dt = now_dt or get_ist_now()
    current_time = dt_time(now_dt.hour, now_dt.minute, now_dt.second)
    return current_time >= _parse_hhmm(CONFIG.get("force_exit_time", "15:30"))


def monitor_open_positions(state):
    live_positions = get_live_open_positions()
    for leg_key in ("ce", "pe"):
        leg = state.get(leg_key)
        if not leg or leg.get("closed"):
            continue
        order_symbol = leg["order_symbol"]
        current_price = fetch_latest_price_1m(order_symbol, state["instrument_cfg"]["exchange"], state["instrument_cfg"]["segment"])
        if current_price is None:
            continue

        entry_price = float(leg["entry_price"])
        take_profit = entry_price * (1.0 + float(CONFIG.get("take_profit_percent", 200.0)) / 100.0)
        stop_loss = entry_price * (1.0 - float(CONFIG.get("stop_loss_percent", 75.0)) / 100.0)
        est_pnl = _estimated_pnl(entry_price, current_price, leg["qty"])
        mode_text = "BROKER" if leg.get("broker_order_placed", True) else "PAPER"
        log_event(
            f"[EST PNL] {order_symbol} Mode={mode_text} Entry={entry_price:.2f} Now={current_price:.2f} "
            f"Qty={leg['qty']} EstPnL={est_pnl:.0f} TP={take_profit:.2f} SL={stop_loss:.2f}"
        )

        if current_price >= take_profit:
            log_event(f"[MX EXIT] {order_symbol}: Entry={entry_price:.2f}, Now={current_price:.2f}, TP>={take_profit:.2f}")
            if leg.get("paper_only"):
                leg["closed"] = True
                leg["exit_reason"] = "TP"
                log_event(f"[PAPER EXIT] {order_symbol} closed on TP | EstPnL={est_pnl:.0f}")
            elif place_sell_order(order_symbol, current_price, f"TP +{CONFIG.get('take_profit_percent', 200.0)}%", leg["qty"]):
                leg["closed"] = True
                leg["exit_reason"] = "TP"
        elif current_price <= stop_loss:
            log_event(f"[SL EXIT] {order_symbol}: Entry={entry_price:.2f}, Now={current_price:.2f}, SL<={stop_loss:.2f}")
            if leg.get("paper_only"):
                leg["closed"] = True
                leg["exit_reason"] = "SL"
                log_event(f"[PAPER EXIT] {order_symbol} closed on SL | EstPnL={est_pnl:.0f}")
            elif place_sell_order(order_symbol, current_price, f"SL -{CONFIG.get('stop_loss_percent', 75.0)}%", leg["qty"]):
                leg["closed"] = True
                leg["exit_reason"] = "SL"


def close_remaining_positions(state, reason):
    for leg_key in ("ce", "pe"):
        leg = state.get(leg_key)
        if not leg or leg.get("closed"):
            continue
        order_symbol = leg["order_symbol"]
        current_price = fetch_latest_price_1m(order_symbol, state["instrument_cfg"]["exchange"], state["instrument_cfg"]["segment"])
        if current_price is None:
            current_price = float(leg["entry_price"])
        est_pnl = _estimated_pnl(float(leg["entry_price"]), float(current_price), leg["qty"])
        log_event(f"[FINAL EST PNL] {order_symbol} Entry={float(leg['entry_price']):.2f} ExitRef={float(current_price):.2f} Qty={leg['qty']} EstPnL={est_pnl:.0f} Reason={reason}")
        if leg.get("paper_only"):
            leg["closed"] = True
            leg["exit_reason"] = reason
            log_event(f"[PAPER FINAL EXIT] {order_symbol} closed without broker order | Reason={reason}")
        elif place_sell_order(order_symbol, current_price, reason, leg["qty"]):
            leg["closed"] = True
            leg["exit_reason"] = reason


def try_open_320trade(state):
    if state.get("entered"):
        return

    now_ist = get_ist_now()
    if not entry_window_open(now_ist):
        return

    index_price, candle_time, candle_open = get_latest_index_price(state["instrument_cfg"], use_previous_close=True)
    if index_price is None:
        return

    contracts = build_entry_contracts(state["instrument"], state["instrument_cfg"], state["expiry_entry"], index_price)
    log_event(
        f"[ENTRY] {state['instrument']} expiry={contracts['expiry_fs']} order={contracts['order_expiry']} "
        f"ITM CE={contracts['ce_strike']} PE={contracts['pe_strike']} index_ref={index_price:.2f} used_from_closed_candle={candle_time}"
    )

    ce_leg = place_buy_order(
        contracts["ce"]["order_symbol"],
        price=index_price,
        exchange=state["instrument_cfg"]["exchange"],
        segment=state["instrument_cfg"]["segment"],
        entry_price_label="INDEX",
        instrument_cfg=state["instrument_cfg"],
    )
    if ce_leg is None:
        log_event("[ENTRY FAIL] CE leg failed")
        return

    pe_leg = place_buy_order(
        contracts["pe"]["order_symbol"],
        price=index_price,
        exchange=state["instrument_cfg"]["exchange"],
        segment=state["instrument_cfg"]["segment"],
        entry_price_label="INDEX",
        instrument_cfg=state["instrument_cfg"],
    )
    if pe_leg is None:
        log_event("[ENTRY FAIL] PE leg failed, flattening CE leg")
        if not ce_leg.get("paper_only"):
            place_sell_order(ce_leg["order_symbol"], ce_leg["entry_price"], "Entry rollback", ce_leg["qty"])
        return

    state["entered"] = True
    state["ce"] = {**ce_leg, "closed": False, "candle_symbol": contracts["ce"]["candle_symbol"]}
    state["pe"] = {**pe_leg, "closed": False, "candle_symbol": contracts["pe"]["candle_symbol"]}
    state["contracts"] = contracts
    state["entry_candle_time"] = candle_time
    log_event(f"[ENTRY CONFIRMED] CE={contracts['ce']['order_symbol']} PE={contracts['pe']['order_symbol']} Qty={ce_leg['qty']}")


def main_loop():
    selected = _select_instrument_for_today()
    if not selected:
        log_event("[INFO] No configured NIFTY or SENSEX expiry matches today. Script will stay idle.")
        return

    instrument_name, instrument_key, expiry_entry = selected
    instrument_cfg = CONFIG[instrument_key]
    CONFIG["instrument_exchange"] = instrument_cfg["exchange"]

    log_event(
        f"[CONFIG] Selected {instrument_name} today expiry: candle={expiry_entry['candle_expiry']} "
        f"order={expiry_entry['order_expiry']}"
    )

    state = {
        "instrument": instrument_name,
        "instrument_key": instrument_key,
        "instrument_cfg": instrument_cfg,
        "expiry_entry": expiry_entry,
        "entered": False,
        "ce": None,
        "pe": None,
        "contracts": None,
        "entry_candle_time": None,
    }

    last_logged_state = None
    while True:
        try:
            now_ist = get_ist_now()
            session_state = get_session_state(now_ist)

            if session_state != last_logged_state:
                if session_state == "closed_day":
                    log_event(f"[SESSION] Market closed today at {now_ist.strftime('%Y-%m-%d %H:%M:%S')}")
                elif session_state == "pre_open":
                    log_event(f"[SESSION] Waiting for strike selection before {CONFIG['strike_decision_time']} IST")
                elif session_state == "select_strike":
                    log_event(f"[SESSION] Strike selection active: using previous closed candle before {CONFIG['strike_decision_time']} IST")
                elif session_state == "entry":
                    log_event(f"[SESSION] Entry window active: {CONFIG['entry_window_start']} - {CONFIG['entry_window_end']} IST")
                elif session_state == "manage_only":
                    log_event(f"[SESSION] Managing open legs until {CONFIG['force_exit_time']} IST")
                elif session_state == "squareoff":
                    log_event(f"[SESSION] Square-off window active at {CONFIG['force_exit_time']} IST")
                last_logged_state = session_state

            if session_state == "closed_day":
                time.sleep(5)
                continue

            if session_state == "squareoff":
                close_remaining_positions(state, "TIME_SQUAREOFF_15_30")
                break

            if not state["entered"] and session_state in {"select_strike", "entry"}:
                try_open_320trade(state)
            elif state["entered"]:
                monitor_open_positions(state)

            time.sleep(5)
        except KeyboardInterrupt:
            close_remaining_positions(state, "MANUAL_STOP")
            break
        except Exception as exc:
            print(f"[ERROR] main_loop: {type(exc).__name__}: {exc}")
            time.sleep(10)


try:
    raw_access_token = GrowwAPI.get_access_token(
        api_key=CONFIG["api_key"],
        secret=CONFIG["api_secret"],
    )
    access_token = _normalize_access_token(raw_access_token)
    growwapi = GrowwAPI(access_token)
except Exception as exc:
    log_event(f"[ERROR] Authentication failed: {exc}")
    raise SystemExit(1)


if __name__ == "__main__":
    main_loop()