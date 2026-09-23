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

from datetime import datetime, timedelta
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
    "strike_start": 76500,
    "strike_end": 78500,

    # Entry rules
    "use_momentum_filter": True,
    "require_close_above_prev_close": True,
    "entry_open_gap_limit_points": 10.0,
    "flip_confirmation_window_candles": 1,

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

    # Daily log email (enable for VM deployments)
    "daily_log_email_enabled": False,
    "notification_email_enabled": False,
    "EMAIL_USER": "gencinvestor@gmail.com",
    "EMAIL_PASSWORD": "powg jovk hxry eoaq",
    "EMAIL_TO": ["gencinvestor@gmail.com"],
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_use_tls": True,
    "smtp_username": "",
    "smtp_password": "",
    "email_from": "gencinvestor@gmail.com",
    "email_to": ["gencinvestor@gmail.com"],
    "log_dir": "logs",
    "log_file_prefix": "SENSEXINTRA",
}


CANDLE_INTERVAL_MAP = {
    "1M": "1minute", "2M": "2minute", "3M": "3minute",
    "5M": "5minute", "10M": "10minute", "15M": "15minute",
    "30M": "30minute", "1H": "1hour", "4H": "4hour",
    "1D": "1day", "1W": "1week", "1MO": "1month",
}


try:
    access_token = GrowwAPI.get_access_token(
        api_key=CONFIG["api_key"],
        secret=CONFIG["api_secret"],
    )
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
side_cooldown_tracker = {"CE": 0, "PE": 0}
no_data_warn_tracker = {}
contract_universe = {"CE": [], "PE": []}
order_to_candle = {}
candle_to_order = {}
candle_to_side = {}
order_expiry_to_candle_expiry = {}


class DailyTeeStdout:
    def __init__(self, base_stdout, log_dir, file_prefix):
        self.base_stdout = base_stdout
        self.log_dir = log_dir
        self.file_prefix = file_prefix
        self._lock = threading.Lock()
        self._current_date = None
        self._fh = None
        os.makedirs(self.log_dir, exist_ok=True)

    def _get_date_text(self):
        return datetime.now().strftime("%Y-%m-%d")

    def get_log_path_for_date(self, date_text):
        return os.path.join(self.log_dir, f"{self.file_prefix}-{date_text}.txt")

    def _ensure_file(self):
        today = self._get_date_text()
        if self._current_date != today:
            if self._fh is not None:
                self._fh.flush()
                self._fh.close()
            self._current_date = today
            self._fh = open(self.get_log_path_for_date(today), "a", encoding="utf-8")

    def write(self, data):
        with self._lock:
            self._ensure_file()
            self.base_stdout.write(data)
            self._fh.write(data)

    def flush(self):
        with self._lock:
            self.base_stdout.flush()
            if self._fh is not None:
                self._fh.flush()


tee_stdout = None


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


def _get_email_recipients():
    recipients = CONFIG.get("EMAIL_TO")
    if recipients is None:
        recipients = CONFIG.get("email_to") or []
    if isinstance(recipients, str):
        recipients = [recipients]
    return [str(x).strip() for x in recipients if str(x).strip()]


def _get_smtp_identity():
    username = str(CONFIG.get("EMAIL_USER") or CONFIG.get("smtp_username") or "").strip()
    password = str(CONFIG.get("EMAIL_PASSWORD") or CONFIG.get("smtp_password") or "").strip()
    sender = str(CONFIG.get("email_from") or CONFIG.get("EMAIL_FROM") or username).strip()
    recipients = _get_email_recipients()
    return username, password, sender, recipients


def send_notification_email(subject, body, *, to_recipients=None):
    if not CONFIG.get("notification_email_enabled", False):
        return

    username, password, sender, recipients = _get_smtp_identity()
    if to_recipients is not None:
        if isinstance(to_recipients, str):
            to_recipients = [to_recipients]
        recipients = [str(x).strip() for x in to_recipients if str(x).strip()]

    if not username or not password or not sender or not recipients:
        print("[EMAIL] Skipped: notification email config incomplete")
        return

    def _send_task():
        try:
            smtp_host = str(CONFIG.get("smtp_host") or "smtp.gmail.com").strip() or "smtp.gmail.com"
            smtp_port = int(CONFIG.get("smtp_port") or 587)
            use_tls = bool(CONFIG.get("smtp_use_tls", True))
            msg = EmailMessage()
            msg["Subject"] = str(subject or "SENSEXINTRA Notification")
            msg["From"] = sender
            msg["To"] = ", ".join(recipients)
            msg.set_content(str(body or ""))

            if use_tls:
                context = ssl.create_default_context()
                with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                    server.starttls(context=context)
                    server.login(username, password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                    server.login(username, password)
                    server.send_message(msg)

            print(f"[EMAIL] Runtime alert sent -> {', '.join(recipients)}")
        except Exception as exc:
            print(f"[EMAIL ERROR] Failed to send runtime alert: {type(exc).__name__}: {exc}")

    thread = threading.Thread(target=_send_task, daemon=True)
    thread.start()
    return thread


def send_daily_log_email(log_date_text):
    if not CONFIG.get("daily_log_email_enabled", False):
        return
    if tee_stdout is None:
        return

    smtp_host = str(CONFIG.get("smtp_host") or "").strip()
    smtp_port = int(CONFIG.get("smtp_port") or 0)
    username = str(CONFIG.get("smtp_username") or "").strip()
    password = str(CONFIG.get("smtp_password") or "").strip()
    sender = str(CONFIG.get("email_from") or username).strip()
    recipients = _get_email_recipients()
    use_tls = bool(CONFIG.get("smtp_use_tls", True))

    if not smtp_host or not smtp_port or not sender or not recipients:
        print("[EMAIL] Skipped: SMTP/email config incomplete")
        return

    log_path = tee_stdout.get_log_path_for_date(log_date_text)
    if not os.path.exists(log_path):
        print(f"[EMAIL] Skipped: log file not found for {log_date_text}")
        return

    try:
        with open(log_path, "rb") as f:
            attachment = f.read()

        msg = EmailMessage()
        msg["Subject"] = f"SENSEXINTRA Daily Log - {log_date_text}"
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        msg.set_content(
            f"Attached is the full day trading log for {log_date_text}.\n"
            f"Generated by SENSEXINTRA on VM."
        )
        msg.add_attachment(
            attachment,
            maintype="text",
            subtype="plain",
            filename=f"{log_date_text}.txt"
        )

        if use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.starttls(context=context)
                if username and password:
                    server.login(username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                if username and password:
                    server.login(username, password)
                server.send_message(msg)

        print(f"[EMAIL] Daily log sent for {log_date_text} -> {', '.join(recipients)}")
    except Exception as exc:
        print(f"[EMAIL ERROR] Failed to send daily log for {log_date_text}: {type(exc).__name__}: {exc}")


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
                    candle_interval=CANDLE_INTERVAL_MAP[interval_str],
                    start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
                )
                if isinstance(raw, dict) and raw.get("candles"):
                    df = _build_dataframe(raw["candles"])
                    if df is not None and not df.empty:
                        df = df[df["date"] <= pd.Timestamp(datetime.now())].reset_index(drop=True)
                        if len(df) > best_count:
                            best_df = df
                            best_count = len(df)
                        if len(df) >= min_candles:
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
        if isinstance(raw, dict) and raw.get("candles"):
            df = _build_dataframe(raw["candles"])
            if not df.empty:
                return float(df["close"].iloc[-1])
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
        fno = resp.get("fno_margin_details", {})
        if "future_balance_available" in fno:
            value = float(fno["future_balance_available"])
            return max(0.0, value)
        eq = resp.get("equity_margin_details", {})
        if "mis_balance_available" in eq:
            value = float(eq["mis_balance_available"])
            return max(0.0, value)
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

    print(
        f"[EXPIRY] Selected CE: candle={expiry_fs_ce}, order={order_expiry_ce} | "
        f"PE: candle={expiry_fs_pe}, order={order_expiry_pe}"
    )

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
        sorted_contracts = sorted(contracts, key=lambda c: (abs(c["strike"] - index_price), -c["strike"]))
    else:
        sorted_contracts = sorted(contracts, key=lambda c: (abs(c["strike"] - index_price), c["strike"]))
    return sorted_contracts[0]


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


def update_trailing_stop_and_check(symbol, current_price):
    if symbol not in positions or positions[symbol].get("status") != "OPEN":
        return False

    pos = positions[symbol]
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
            print(f"[DEBUG] TRAILING ACTIVATED {symbol} | High={highest:.2f} TSL={pos['trailing_stop_price']:.2f}")

    if bool(pos.get("trailing_sl_activated", False)):
        current_tsl = float(pos.get("trailing_stop_price", entry - stop_gap))
        new_tsl = max(current_tsl, highest - stop_gap)
        pos["trailing_stop_price"] = new_tsl
        if current_price <= new_tsl:
            return True
    return False


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
            print("[SKIP] No usable margin budget available for order placement")
            return None

        min_cost = lot_size * price
        if use_margin < min_cost:
            print(f"[SKIP] Insufficient margin. Need {min_cost:.2f}, available budget {use_margin:.2f}")
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


def option_has_flip_down_exit_signal(candle_symbol, idx_time):
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
    position_open = bool(
        positions.get(candle_symbol, {}).get("status") == "OPEN"
    )
    signal = "SELL" if fresh_flip_down and position_open else "NO_SIGNAL"
    return fresh_flip_down and position_open, close_price, st_value, signal


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

    allow_buy = bool(allow_flip and momentum_ok and open_gap_ok)
    signal = "BUY" if allow_buy else "NO_SIGNAL"

    if CONFIG.get("DEBUG"):
        st_txt = f"{st_value:.2f}" if st_value is not None else "NA"
        print(
            f"[OPTION CHECK] {candle_symbol} | Flip={allow_flip} | Momentum={momentum_ok} | "
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
    last_reset_date = None

    while True:
        try:
            global trades_today_ce, trades_today_pe, daily_realized_pnl, exit_attempt_tracker, exit_reason_tracker, buy_signal_attempt_tracker, side_cooldown_tracker, no_data_warn_tracker

            current_date = datetime.now().strftime("%Y-%m-%d")
            if last_reset_date != current_date:
                if last_reset_date is not None:
                    send_daily_log_email(last_reset_date)
                trades_today_ce = 0
                trades_today_pe = 0
                daily_realized_pnl = 0.0
                exit_attempt_tracker = {}
                exit_reason_tracker = {}
                buy_signal_attempt_tracker = {}
                side_cooldown_tracker = {"CE": 0, "PE": 0}
                no_data_warn_tracker = {}
                last_reset_date = current_date
                if not build_contract_universe():
                    print("[ERROR] Contract configuration invalid after daily rollover")
                    wait_until_next_interval()
                    continue

            live_positions = get_live_open_positions()
            reconcile_positions_with_groww(live_positions)

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

            live_positions = get_live_open_positions()
            reconcile_positions_with_groww(live_positions)

            if has_open_on_side("CE") or has_live_open_on_side("CE", live_positions) or has_open_on_side("PE") or has_live_open_on_side("PE", live_positions):
                pass
            else:
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

                    ce_ok = bool(ce_snapshot and ce_snapshot.get("ok") and ce_snapshot.get("signal") == "BUY" and ce_snapshot.get("last_price") is not None and ce_snapshot.get("entry_open") is not None)
                    pe_ok = bool(pe_snapshot and pe_snapshot.get("ok") and pe_snapshot.get("signal") == "BUY" and pe_snapshot.get("last_price") is not None and pe_snapshot.get("entry_open") is not None)

                    if ce_ok and int(side_cooldown_tracker.get("CE", 0)) == 0:
                        candle_symbol = ce_snapshot.get("candle_symbol")
                        order_symbol = candle_to_order.get(candle_symbol)
                        last_attempt_time = buy_signal_attempt_tracker.get(candle_symbol)
                        if order_symbol and last_attempt_time != idx_time:
                            buy_signal_attempt_tracker[candle_symbol] = idx_time
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
            send_notification_email(
                f"SENSEXINTRA runtime error: {type(exc).__name__}",
                (
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Error: {type(exc).__name__}: {exc}\n"
                    f"Path: {os.path.abspath(__file__)}\n"
                    "Loop recovered after error."
                ),
            )
            time.sleep(10)


def main():
    global tee_stdout
    tee_stdout = DailyTeeStdout(
        base_stdout=sys.stdout,
        log_dir=str(CONFIG.get("log_dir") or "logs"),
        file_prefix=str(CONFIG.get("log_file_prefix") or "SENSEXINTRA"),
    )
    sys.stdout = tee_stdout

    try:
        if not build_contract_universe():
            print("[ERROR] Contract configuration invalid. Fix expiry and strike range.")
            raise SystemExit(1)

        send_notification_email(
            "SENSEXINTRA started",
            (
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Script: {os.path.abspath(__file__)}\n"
                "Strategy startup completed."
            ),
        )

        send_notification_email(
            "SENSEXINTRA started",
            (
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Script: {os.path.abspath(__file__)}\n"
                "Strategy startup completed."
            ),
        )

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
    except Exception as exc:
        subject = f"SENSEXINTRA runtime error: {type(exc).__name__}"
        body = (
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Error: {type(exc).__name__}: {exc}\n"
            f"Path: {os.path.abspath(__file__)}\n"
            "Strategy loop stopped."
        )
        send_notification_email(subject, body)
        raise


if __name__ == "__main__":
    main()