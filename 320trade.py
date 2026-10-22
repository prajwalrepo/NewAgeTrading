# =============================================================================
# 320trade.py - Expiry-day ITM CE+PE strategy on Groww API
#
# Flow:
# 1) Check whether today matches a configured NIFTY or SENSEX expiry.
# 2) Prefer the first instrument in expiry_priority that has a today expiry.
# 3) At 15:20 IST, buy CE and PE strikes 100 points ITM from ATM.
# 4) Exit any leg immediately if profit reaches the configured take-profit or loss reaches the configured stop-loss.
# 5) Square off all remaining legs exactly at 15:30 IST.
# =============================================================================

from datetime import datetime, timedelta, time as dt_time, timezone
import math
import os
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
    # Keep Groww credentials in the private environment file instead of source.
    "api_key": os.getenv("GROWW_API_KEY", ""),
    "api_secret": os.getenv("GROWW_API_SECRET", ""),

    # Which instrument to trade when both have expiry today.
    "expiry_priority": ["NIFTY", "SENSEX"],

    # Entry / exit schedule in IST.
    "trading_timezone_offset_minutes": 330,
    "entry_window_start": "15:20",
    "entry_window_end": "15:21",
    "strike_decision_time": "15:15",
    "margin_precheck_time": "15:17",
    "monitor_interval_seconds": 30,
    "itm_offset_points": 100,
    "force_exit_time": "15:30",
    "trading_weekdays": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"],

    # Order sizing.
    # Fallback lot size used only if instrument lot size is not configured.
    "lot_size": 2,
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
        "expiry_fs": ["19Oct26", "27Oct26", "03Nov26","09Nov26", "17Nov26", "23Nov26"],
        "order_expiry": ["26O19", "26OCT", "26N03","26N09", "26N17", "26NOV"],
        # Per-leg profit and loss limits as a percentage of premium cost.
        "take_profit_percent": 150.0,
        "stop_loss_percent": 40.0,
        "max_lots_per_trade": 2,
    },
    "sensex": {
        "root": "SENSEX",
        "lot_size": 20,
        "exchange": "BSE",
        "segment": "SEGMENT_FNO",
        "index_segment": "SEGMENT_CASH",
        "candle_interval": "1M",
        "atr_period": 10,
        "strike_step": 100,
        "strike_start": 60000,
        "strike_end": 80000,
        "expiry_fs": ["22Oct26", "29Oct26", "05Nov26","12Nov26", "19Nov26", "26Nov26"],
        "order_expiry": ["26O22", "26OCT", "26N05","26N12", "26N19", "26NOV"],
        # Per-leg profit and loss limits as a percentage of premium cost.
        "take_profit_percent": 200.0,
        "stop_loss_percent": 50,
        "max_lots_per_trade": 5,
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


def _make_paper_leg(order_symbol, price, reason="PAPER", qty=None, strike=None, lot_size=None):
    lot_size = int(lot_size or CONFIG.get("lot_size", 1) or 1)
    return {
        "order_symbol": order_symbol,
        "qty": int(qty or lot_size),
        "entry_price": float(price),
        "entry_time": get_ist_now().strftime("%Y-%m-%d %H:%M:%S"),
        "broker_order_placed": False,
        "paper_only": True,
        "exit_reason": None,
        "paper_reason": reason,
        "strike": strike,
    }


def _log_trade_signal(side, order_symbol, strike, price, reason, order_placed,
                      lots=None, lot_size=None, qty=None, trade_time=None,
                      buy_price=None, sell_price=None, pnl=None):
    signal_time = trade_time or get_ist_now().strftime("%Y-%m-%dT%H:%M:%S")
    log_event(
        f"[TRADE SIGNAL] Script=320trade Side={side} Time={signal_time} Symbol={order_symbol} "
        f"Strike={strike if strike is not None else 'NA'} Price={float(price):.2f} "
        f"Reason={reason} "
        f"{f'Lots={lots} LotSize={lot_size} ' if lots is not None else ''}"
        f"{f'Qty={qty} ' if qty is not None else ''}"
        f"{f'BuyAt={float(buy_price):.2f} ' if buy_price is not None else ''}"
        f"{f'SellAt={float(sell_price):.2f} ' if sell_price is not None else ''}"
        f"{f'PnL={float(pnl):.2f} ' if pnl is not None else ''}"
        f"OrderPlaced={'YES' if order_placed else 'NO'}"
    )


def _log_exit_signal(order_symbol, strike, buy_price, sell_price, pnl, reason,
                     order_placed, qty=None, trade_time=None):
    _log_trade_signal(
        "SELL", order_symbol, strike, sell_price, reason, order_placed,
        qty=qty, trade_time=trade_time, buy_price=buy_price,
        sell_price=sell_price, pnl=pnl,
    )


def _get_risk_limits(entry_price, qty, instrument_cfg):
    cost = float(entry_price) * int(qty)
    take_profit_percent = float(
        instrument_cfg.get("take_profit_percent", CONFIG.get("take_profit_percent", 150.0)) or 0
    )
    stop_loss_percent = float(
        instrument_cfg.get("stop_loss_percent", CONFIG.get("stop_loss_percent", 30.0)) or 0
    )
    return cost * take_profit_percent / 100.0, cost * stop_loss_percent / 100.0


def _apply_risk_limits(leg, instrument_cfg):
    max_profit_amount, max_loss_amount = _get_risk_limits(
        leg["entry_price"], leg["qty"], instrument_cfg
    )
    leg["max_profit_amount"] = max_profit_amount
    leg["max_loss_amount"] = max_loss_amount
    log_event(
        f"[RISK LIMIT] {leg['order_symbol']} Qty={leg['qty']} "
        f"MaxLoss={max_loss_amount:.2f} MaxProfit={max_profit_amount:.2f}"
    )
    return leg


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


def _resolve_lots_per_trade(instrument_cfg):
    try:
        lots_per_trade = int((instrument_cfg or {}).get("max_lots_per_trade", 1) or 1)
    except Exception:
        lots_per_trade = 1
    return max(lots_per_trade, 1)


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
    strike_decision = _parse_hhmm(CONFIG.get("strike_decision_time", "15:15"))
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


def place_buy_order(
    order_symbol, price, exchange, segment, entry_price_label,
    instrument_cfg=None, strike=None, available_margin=None,
    margin_prechecked=False, defer_confirmation=False, lots_per_trade=None,
):
    lot_size = _resolve_lot_size(instrument_cfg)
    lots_per_trade = max(1, int(lots_per_trade or _resolve_lots_per_trade(instrument_cfg)))
    qty = lot_size * lots_per_trade

    log_event(f"[BUY ATTEMPT] {order_symbol} Qty={qty} EstEntry={float(price):.2f} Source={entry_price_label}")

    def make_paper_entry(reason):
        leg = _make_paper_leg(
            order_symbol, price, reason=reason, qty=qty, strike=strike, lot_size=lot_size
        )
        _apply_risk_limits(leg, instrument_cfg or {})
        _log_trade_signal(
            "BUY", order_symbol, strike, leg["entry_price"],
            f"Expiry straddle entry ({reason})", False,
            lots=lots_per_trade, lot_size=lot_size, qty=qty,
            trade_time=leg["entry_time"].replace(" ", "T"),
        )
        return leg

    if not bool(CONFIG.get("enable_actual_trade", True)):
        log_event(f"[LIVE ORDER DISABLED] {order_symbol} running in paper tracking mode")
        if _paper_tracking_enabled():
            return make_paper_entry("LIVE_ORDER_DISABLED")
        return None

    margin = available_margin if margin_prechecked else get_available_fno_margin()
    if CONFIG.get("check_margin_before_order", True) and margin is None:
        log_event(f"[SKIP] Margin unavailable for {order_symbol}")
        if _paper_tracking_enabled():
            log_event(f"[PAPER ENTRY] {order_symbol} using estimated entry price because broker margin is unavailable")
            return make_paper_entry("MARGIN_UNAVAILABLE")
        return None
    if margin is not None and margin <= 0:
        log_event(f"[BUY BLOCKED] {order_symbol}: no usable margin budget ({margin:.2f})")
        if _paper_tracking_enabled():
            log_event(f"[PAPER ENTRY] {order_symbol} using estimated entry price because usable margin is zero")
            return make_paper_entry("NO_MARGIN")
        return None

    estimated_profit, estimated_loss = _get_risk_limits(
        price, qty, instrument_cfg or {}
    )
    log_event(
        f"[RISK LIMIT ESTIMATE] {order_symbol} Qty={qty} "
        f"MaxLoss={estimated_loss:.2f} MaxProfit={estimated_profit:.2f}"
    )
    try:
        resp = place_order_with_retry(order_symbol, qty, growwapi.TRANSACTION_TYPE_BUY, growwapi.PRODUCT_MIS)
    except Exception as exc:
        log_event(f"[ERROR] BUY {order_symbol}: {exc}")
        if _paper_tracking_enabled():
            log_event(f"[PAPER ENTRY] {order_symbol} recorded because broker BUY request failed")
            return make_paper_entry("BUY_REQUEST_FAILED")
        return None

    order_id = resp.get("groww_order_id") if isinstance(resp, dict) else None
    status = resp.get("order_status", "UNKNOWN") if isinstance(resp, dict) else "UNKNOWN"
    if status in {"REJECTED", "CANCELLED", "FAILED"} or not order_id:
        log_event(f"[BUY FAIL] {order_symbol} status={status} order_id={order_id or 'NA'}")
        if _paper_tracking_enabled():
            log_event(f"[PAPER ENTRY] {order_symbol} recorded because broker buy did not fill")
            return make_paper_entry(f"BROKER_{status}")
        return None

    pending_order = {
        "pending_confirmation": True,
        "order_id": order_id,
        "status": status,
        "order_symbol": order_symbol,
        "qty": qty,
        "reference_price": float(price),
        "entry_price_label": entry_price_label,
        "strike": strike,
        "lots": lots_per_trade,
        "lot_size": lot_size,
        "instrument_cfg": instrument_cfg or {},
    }
    if defer_confirmation:
        return pending_order
    return confirm_buy_order(pending_order)


def confirm_buy_order(pending_order):
    if not pending_order or not pending_order.get("pending_confirmation"):
        return pending_order

    order_id = pending_order["order_id"]
    order_symbol = pending_order["order_symbol"]
    qty = pending_order["qty"]
    reference_price = pending_order["reference_price"]
    entry_price_label = pending_order["entry_price_label"]
    strike = pending_order["strike"]
    lots_per_trade = pending_order["lots"]
    lot_size = pending_order["lot_size"]
    if pending_order["status"] in {"NEW", "PENDING", "OPEN"}:
        time.sleep(2)
    final_status, avg_price, _ = verify_order_execution(
        order_id, fallback_price=reference_price
    )
    if final_status in {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}:
        exec_price = avg_price if avg_price and avg_price > 0 else reference_price
        entry_time = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
        log_event(
            f"[BUY CONFIRMED] {order_symbol} Qty={qty} Entry={exec_price:.2f} "
            f"{entry_price_label}={reference_price:.2f} OrderID={order_id}"
        )
        _log_trade_signal(
            "BUY", order_symbol, strike, exec_price, "Expiry straddle entry", True,
            lots=lots_per_trade, lot_size=lot_size, qty=qty,
            trade_time=entry_time.replace(" ", "T"),
        )
        return _apply_risk_limits({
            "order_symbol": order_symbol,
            "qty": qty,
            "entry_price": exec_price,
            "entry_time": entry_time,
            "broker_order_placed": True,
            "paper_only": False,
            "strike": strike,
        }, pending_order.get("instrument_cfg", {}))
    log_event(f"[BUY FAIL] {order_symbol} final status={final_status}")
    if _paper_tracking_enabled():
        log_event(f"[PAPER ENTRY] {order_symbol} recorded because broker buy execution was not confirmed")
        leg = _make_paper_leg(
            order_symbol, reference_price, reason=f"UNCONFIRMED_{final_status}",
            qty=qty, strike=strike, lot_size=lot_size,
        )
        _apply_risk_limits(leg, pending_order.get("instrument_cfg", {}))
        _log_trade_signal(
            "BUY", order_symbol, strike, leg["entry_price"],
            f"Expiry straddle entry (UNCONFIRMED_{final_status})", False,
            lots=lots_per_trade, lot_size=lot_size, qty=qty,
            trade_time=leg["entry_time"].replace(" ", "T"),
        )
        return leg
    return None


def place_sell_order(order_symbol, price, reason, qty, strike=None, entry_price=None):
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
            entry_value = float(entry_price if entry_price is not None else price)
            pnl = (exec_price - entry_value) * qty
            signal_time = get_ist_now().strftime("%Y-%m-%dT%H:%M:%S")
            _log_trade_signal(
                "SELL", order_symbol, strike, exec_price, reason, True,
                qty=qty, trade_time=signal_time, buy_price=entry_value,
                sell_price=exec_price, pnl=pnl,
            )
            log_event(f"[EXIT CONFIRMED] {order_symbol} x{qty} | Reason={reason} | Exit={exec_price:.2f} OrderID={order_id}")
            log_event(f"[TRADE RESULT] Script=320trade Side=SELL Symbol={order_symbol} Strike={strike if strike is not None else 'NA'} BuyAt={entry_value:.2f} SellAt={exec_price:.2f} PnL={pnl:.0f}")
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


def select_atm_strike(instrument_cfg, index_price):
    strike_step = int(instrument_cfg["strike_step"])
    strike = int(math.floor(float(index_price) / strike_step + 0.5)) * strike_step
    strike_start = int(instrument_cfg["strike_start"])
    strike_end = int(instrument_cfg["strike_end"])
    if strike < strike_start or strike > strike_end:
        raise ValueError(
            f"ATM strike {strike} outside configured range "
            f"{strike_start}-{strike_end} for {instrument_cfg['root']}"
        )
    return strike


def build_entry_contracts(instrument_key, instrument_cfg, expiry_entry, strike):
    itm_offset = int(instrument_cfg.get("itm_offset_points", CONFIG.get("itm_offset_points", 100)))
    if itm_offset <= 0 or itm_offset % int(instrument_cfg["strike_step"]) != 0:
        raise ValueError(
            f"ITM offset {itm_offset} must be a positive multiple of "
            f"{instrument_cfg['strike_step']} for {instrument_key}"
        )
    ce_strike = int(strike) - itm_offset
    pe_strike = int(strike) + itm_offset
    strike_start = int(instrument_cfg["strike_start"])
    strike_end = int(instrument_cfg["strike_end"])
    for side, selected_strike in (("CE", ce_strike), ("PE", pe_strike)):
        if selected_strike < strike_start or selected_strike > strike_end:
            raise ValueError(
                f"ITM {side} strike {selected_strike} outside configured range "
                f"{strike_start}-{strike_end} for {instrument_key}"
            )

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
    instrument_cfg = state["instrument_cfg"]
    take_profit_percent = float(instrument_cfg.get("take_profit_percent", CONFIG.get("take_profit_percent", 150.0)) or 0)
    stop_loss_percent = float(instrument_cfg.get("stop_loss_percent", CONFIG.get("stop_loss_percent", 30.0)) or 0)
    for leg_key in ("ce", "pe"):
        leg = state.get(leg_key)
        if not leg or leg.get("closed"):
            continue
        order_symbol = leg["order_symbol"]
        current_price = fetch_latest_price_1m(order_symbol, state["instrument_cfg"]["exchange"], state["instrument_cfg"]["segment"])
        if current_price is None:
            continue

        entry_price = float(leg["entry_price"])
        est_pnl = _estimated_pnl(entry_price, current_price, leg["qty"])
        configured_profit, configured_loss = _get_risk_limits(
            entry_price, leg["qty"], instrument_cfg
        )
        max_profit_amount = float(leg.get("max_profit_amount", configured_profit))
        max_loss_amount = float(leg.get("max_loss_amount", configured_loss))
        take_profit = entry_price + max_profit_amount / leg["qty"]
        stop_loss = entry_price - max_loss_amount / leg["qty"]
        mode_text = "BROKER" if leg.get("broker_order_placed", True) else "PAPER"
        log_event(
            f"[EST PNL] {order_symbol} Mode={mode_text} Entry={entry_price:.2f} Now={current_price:.2f} "
            f"Qty={leg['qty']} EstPnL={est_pnl:.2f} MaxProfit={max_profit_amount:.2f} "
            f"MaxLoss={max_loss_amount:.2f} TP={take_profit:.2f} SL={stop_loss:.2f}"
        )

        if est_pnl >= max_profit_amount:
            reason = f"TP +{take_profit_percent:g}%"
            log_event(f"[MAX PROFIT EXIT] {order_symbol}: PnL={est_pnl:.2f} Target={max_profit_amount:.2f}")
            if leg.get("paper_only"):
                signal_time = get_ist_now().strftime("%Y-%m-%dT%H:%M:%S")
                _log_exit_signal(
                    order_symbol, leg.get("strike"), entry_price, current_price,
                    est_pnl, f"{reason} (paper exit)", False, qty=leg["qty"],
                    trade_time=signal_time,
                )
                leg["closed"] = True
                leg["exit_reason"] = "TP"
                log_event(f"[PAPER EXIT] {order_symbol} closed on TP | EstPnL={est_pnl:.0f}")
            elif place_sell_order(order_symbol, current_price, reason, leg["qty"], strike=leg.get("strike"), entry_price=leg["entry_price"]):
                leg["closed"] = True
                leg["exit_reason"] = "TP"
            elif not leg.get("failed_exit_signal_sent"):
                _log_exit_signal(
                    order_symbol, leg.get("strike"), entry_price, current_price,
                    est_pnl, f"{reason} (broker exit failed; reference only)",
                    False, qty=leg["qty"],
                )
                leg["failed_exit_signal_sent"] = True
        elif est_pnl <= -max_loss_amount:
            reason = f"SL -{stop_loss_percent:g}%"
            log_event(f"[MAX LOSS EXIT] {order_symbol}: PnL={est_pnl:.2f} Limit=-{max_loss_amount:.2f}")
            if leg.get("paper_only"):
                signal_time = get_ist_now().strftime("%Y-%m-%dT%H:%M:%S")
                _log_exit_signal(
                    order_symbol, leg.get("strike"), entry_price, current_price,
                    est_pnl, f"{reason} (paper exit)", False, qty=leg["qty"],
                    trade_time=signal_time,
                )
                leg["closed"] = True
                leg["exit_reason"] = "SL"
                log_event(f"[PAPER EXIT] {order_symbol} closed on SL | EstPnL={est_pnl:.0f}")
            elif place_sell_order(order_symbol, current_price, reason, leg["qty"], strike=leg.get("strike"), entry_price=leg["entry_price"]):
                leg["closed"] = True
                leg["exit_reason"] = "SL"
            elif not leg.get("failed_exit_signal_sent"):
                _log_exit_signal(
                    order_symbol, leg.get("strike"), entry_price, current_price,
                    est_pnl, f"{reason} (broker exit failed; reference only)",
                    False, qty=leg["qty"],
                )
                leg["failed_exit_signal_sent"] = True


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
            signal_time = get_ist_now().strftime("%Y-%m-%dT%H:%M:%S")
            _log_exit_signal(
                order_symbol, leg.get("strike"), leg["entry_price"], current_price,
                est_pnl, f"{reason} (paper/reference exit)", False,
                qty=leg["qty"], trade_time=signal_time,
            )
            log_event(
                f"[TRADE RESULT] Script=320trade Side=SELL Symbol={order_symbol} Strike={leg.get('strike', 'NA')} "
                f"BuyAt={float(leg['entry_price']):.2f} SellAt={float(current_price):.2f} PnL={est_pnl:.0f}"
            )
            leg["closed"] = True
            leg["exit_reason"] = reason
            log_event(f"[PAPER FINAL EXIT] {order_symbol} closed without broker order | Reason={reason}")
        elif place_sell_order(order_symbol, current_price, reason, leg["qty"], strike=leg.get("strike"), entry_price=leg["entry_price"]):
            leg["closed"] = True
            leg["exit_reason"] = reason
        elif not leg.get("failed_exit_signal_sent"):
            _log_exit_signal(
                order_symbol, leg.get("strike"), leg["entry_price"], current_price,
                est_pnl, f"{reason} (broker exit failed; reference only)",
                False, qty=leg["qty"],
            )
            leg["failed_exit_signal_sent"] = True


def try_open_320trade(state):
    if state.get("entered"):
        return

    now_ist = get_ist_now()
    entry_start = _parse_hhmm(CONFIG.get("entry_window_start", "15:20"))
    strike_decision = _parse_hhmm(CONFIG.get("strike_decision_time", "15:15"))
    if not entry_window_open(now_ist):
        current_time = dt_time(now_ist.hour, now_ist.minute, now_ist.second)
        if current_time < strike_decision or current_time >= entry_start:
            return
        if state.get("selected_strike") is None:
            index_price, candle_time, _ = get_latest_index_price(
                state["instrument_cfg"], use_previous_close=True
            )
            if index_price is None:
                return
            strike = select_atm_strike(state["instrument_cfg"], index_price)
            state["selected_strike"] = strike
            state["selected_index_price"] = index_price
            state["selected_candle_time"] = candle_time
            log_event(
                f"[STRIKE SELECTED] {state['instrument']} Strike={strike} "
                f"index_ref={index_price:.2f} closed_candle={candle_time}"
            )
        return

    strike = state.get("selected_strike")
    if strike is None:
        index_price, candle_time, _ = get_latest_index_price(
            state["instrument_cfg"], use_previous_close=True
        )
        if index_price is None:
            return
        strike = select_atm_strike(state["instrument_cfg"], index_price)
        state["selected_strike"] = strike
        state["selected_index_price"] = index_price
        state["selected_candle_time"] = candle_time
        log_event(
            f"[WARN] Strike was not selected at {CONFIG['strike_decision_time']} IST; "
            f"using latest closed candle {candle_time} at entry"
        )
    index_price = state["selected_index_price"]
    candle_time = state["selected_candle_time"]
    contracts = build_entry_contracts(
        state["instrument"], state["instrument_cfg"], state["expiry_entry"], strike
    )
    log_event(
        f"[ENTRY] {state['instrument']} expiry={contracts['expiry_fs']} order={contracts['order_expiry']} "
        f"ITM CE={contracts['ce_strike']} PE={contracts['pe_strike']} "
        f"ATM={strike} index_ref={index_price:.2f} used_from_closed_candle={candle_time}"
    )

    exchange = state["instrument_cfg"]["exchange"]
    segment = state["instrument_cfg"]["segment"]
    ce_price = fetch_latest_price_1m(contracts["ce"]["candle_symbol"], exchange, segment)
    pe_price = fetch_latest_price_1m(contracts["pe"]["candle_symbol"], exchange, segment)
    if ce_price is None or pe_price is None or ce_price <= 0 or pe_price <= 0:
        log_event(
            f"[ENTRY WAIT] Option premium unavailable: CE={ce_price} PE={pe_price}; "
            "will retry while the entry window is open"
        )
        return

    margin_check_enabled = state["instrument_cfg"].get(
        "check_margin_before_order", CONFIG.get("check_margin_before_order", True)
    )
    if margin_check_enabled and CONFIG.get("enable_actual_trade", True):
        if not state.get("margin_prechecked"):
            state["available_margin"] = get_available_fno_margin()
            state["margin_prechecked"] = True
        available_margin = state.get("available_margin")
    else:
        available_margin = None
    lot_size = _resolve_lot_size(state["instrument_cfg"])
    requested_lots = _resolve_lots_per_trade(state["instrument_cfg"])
    lots_to_trade = requested_lots
    paper_reason = None
    if margin_check_enabled and CONFIG.get("enable_actual_trade", True):
        if available_margin is None or available_margin <= 0:
            paper_reason = "MARGIN_UNAVAILABLE"
        else:
            per_leg_budget = float(available_margin) / 2.0
            ce_affordable_lots = math.floor(per_leg_budget / (ce_price * lot_size))
            pe_affordable_lots = math.floor(per_leg_budget / (pe_price * lot_size))
            lots_to_trade = min(requested_lots, ce_affordable_lots, pe_affordable_lots)
            log_event(
                f"[MARGIN ALLOCATION] Available={available_margin:.2f} "
                f"PerLeg={per_leg_budget:.2f} CE1LotCost={ce_price * lot_size:.2f} "
                f"PE1LotCost={pe_price * lot_size:.2f} AffordableLots={lots_to_trade}"
            )
            if lots_to_trade < 1:
                paper_reason = "INSUFFICIENT_MARGIN_FOR_BOTH_LEGS"

    if paper_reason:
        log_event(f"[LIVE ORDER BLOCKED] {paper_reason}; tracking both legs at option premiums")
        ce_leg = _make_paper_leg(
            contracts["ce"]["order_symbol"], ce_price, reason=paper_reason,
            qty=lot_size, strike=contracts["ce_strike"], lot_size=lot_size,
        )
        pe_leg = _make_paper_leg(
            contracts["pe"]["order_symbol"], pe_price, reason=paper_reason,
            qty=lot_size, strike=contracts["pe_strike"], lot_size=lot_size,
        )
        _apply_risk_limits(ce_leg, state["instrument_cfg"])
        _apply_risk_limits(pe_leg, state["instrument_cfg"])
        for leg, contract, option_price in (
            (ce_leg, contracts["ce"], ce_price),
            (pe_leg, contracts["pe"], pe_price),
        ):
            _log_trade_signal(
                "BUY", contract["order_symbol"], leg["strike"], option_price,
                f"Expiry straddle entry ({paper_reason})", False,
                lots=1, lot_size=lot_size, qty=lot_size,
                trade_time=leg["entry_time"].replace(" ", "T"),
            )
    else:
        ce_order = place_buy_order(
            contracts["ce"]["order_symbol"],
            price=ce_price,
            exchange=exchange,
            segment=segment,
            entry_price_label="OPTION",
            instrument_cfg=state["instrument_cfg"],
            strike=contracts["ce_strike"],
            available_margin=(float(available_margin) / 2.0 if available_margin is not None else None),
            margin_prechecked=True,
            defer_confirmation=True,
            lots_per_trade=lots_to_trade,
        )
        if ce_order is None:
            log_event("[ENTRY FAIL] CE leg failed")
            return

        pe_order = place_buy_order(
            contracts["pe"]["order_symbol"],
            price=pe_price,
            exchange=exchange,
            segment=segment,
            entry_price_label="OPTION",
            instrument_cfg=state["instrument_cfg"],
            strike=contracts["pe_strike"],
            available_margin=(float(available_margin) / 2.0 if available_margin is not None else None),
            margin_prechecked=True,
            defer_confirmation=True,
            lots_per_trade=lots_to_trade,
        )
        ce_leg = confirm_buy_order(ce_order)
        pe_leg = confirm_buy_order(pe_order) if pe_order is not None else None
    if ce_leg is None:
        log_event("[ENTRY FAIL] CE leg execution was not confirmed")
        if pe_leg is not None and not pe_leg.get("paper_only"):
            place_sell_order(
                pe_leg["order_symbol"], pe_leg["entry_price"], "Entry rollback",
                pe_leg["qty"], strike=contracts["pe_strike"],
                entry_price=pe_leg["entry_price"],
            )
        return
    if pe_leg is None:
        log_event("[ENTRY FAIL] PE leg failed, flattening CE leg")
        if not ce_leg.get("paper_only"):
            place_sell_order(ce_leg["order_symbol"], ce_leg["entry_price"], "Entry rollback", ce_leg["qty"], strike=contracts["ce_strike"], entry_price=ce_leg["entry_price"])
        return

    state["entered"] = True
    ce_leg["strike"] = contracts["ce_strike"]
    pe_leg["strike"] = contracts["pe_strike"]
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
        "selected_strike": None,
        "margin_prechecked": False,
        "available_margin": None,
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
            current_time = dt_time(now_ist.hour, now_ist.minute, now_ist.second)
            margin_precheck_time = _parse_hhmm(CONFIG.get("margin_precheck_time", "15:17"))
            if (
                session_state == "select_strike"
                and current_time >= margin_precheck_time
                and current_time < _parse_hhmm(CONFIG["entry_window_start"])
                and not state.get("margin_prechecked")
                and CONFIG.get("enable_actual_trade", True)
                and state["instrument_cfg"].get(
                    "check_margin_before_order",
                    CONFIG.get("check_margin_before_order", True),
                )
            ):
                state["available_margin"] = get_available_fno_margin()
                state["margin_prechecked"] = True
                log_event(
                    f"[MARGIN PRECHECK] Completed at {now_ist.strftime('%H:%M:%S')} IST "
                    f"Available={state['available_margin']}"
                )

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
                if state.get("entered") and all(
                    state.get(leg_key) and state[leg_key].get("closed")
                    for leg_key in ("ce", "pe")
                ):
                    log_event("[STRATEGY COMPLETE] Both CE and PE legs closed")
                break

            if not state["entered"] and session_state in {"select_strike", "entry"}:
                try_open_320trade(state)
            elif state["entered"]:
                monitor_open_positions(state)
                if all(
                    state.get(leg_key) and state[leg_key].get("closed")
                    for leg_key in ("ce", "pe")
                ):
                    log_event("[STRATEGY COMPLETE] Both CE and PE legs closed")
                    break

            if not state["entered"] and session_state == "select_strike":
                sleep_now = get_ist_now()
                seconds_until_entry = (
                    datetime.combine(sleep_now.date(), _parse_hhmm(CONFIG["entry_window_start"]))
                    - sleep_now.replace(tzinfo=None)
                ).total_seconds()
                time.sleep(max(0.05, min(5.0, seconds_until_entry)))
            elif state["entered"]:
                time.sleep(max(1, int(CONFIG.get("monitor_interval_seconds", 30))))
            else:
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
