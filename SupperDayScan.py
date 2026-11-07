#!/usr/bin/env python3
"""Scan a configurable stock list for daily Supertrend BUY signals."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from growwapi import GrowwAPI


# Edit this list to choose which NSE stocks the daily scanner checks.
WATCHLIST = [
    "360ONE", "APLAPOLLO", "AUBANK", "ATGL", "ABCAPITAL", "APARINDS",
    "ASHOKLEY", "ASTRAL", "AUROPHARMA", "BANKINDIA", "MAHABANK", "BDL",
    "BHARATFORG", "BHEL", "GROWW", "BIOCON", "BLUESTARCO", "COCHINSHIP",
    "COFORGE", "COLPAL", "CONCOR", "DABUR", "DIXON", "EXIDEIND",
    "NYKAA", "FEDERALBNK", "FORTIS", "GVT&D", "GMRAIRPORT", "GLENMARK",
    "GODFRYPHLP", "GODREJPROP", "HAVELLS", "HEROMOTOCO", "HINDCOPPER",
    "HINDPETRO", "ICICIGI", "ICICIAMC", "IDFCFIRSTB", "INDIANB", "INDHOTEL",
    "IRCTC", "IREDA", "INDUSTOWER", "INDUSINDBK", "NAUKRI", "JSWENERGY",
    "JUBLFOOD", "KEI", "KALYANKJIL", "LTF", "LGEINDIA", "LICHSGFIN",
    "LAURUSLABS", "LENSKART", "LICI", "LODHA", "LUPIN", "MRF", "M&MFIN",
    "MANKIND", "MARICO", "MFSL", "MEESHO", "MOTILALOFS", "MPHASIS", "MCX",
    "NHPC", "NLCINDIA", "NMDC", "NATIONALUM", "OBEROIRLTY", "OIL", "PAYTM",
    "OFSS", "POLICYBZR", "PIIND", "PAGEIND", "PATANJALI", "PERSISTENT",
    "PHOENIXLTD", "PREMIERENE", "PRESTIGE", "RECLTD", "RADICO", "RVNL",
    "SBICARD", "SRF", "SAIL", "SUPREMEIND", "SUZLON", "SWIGGY", "TATACOMM",
    "TIINDIA", "UPL", "UNITDSPR", "VMM", "VOLTAS", "WAAREEENER", "YESBANK",
    "ACC", "ACMESOLAR", "AWL", "AADHARHFC", "AARTIIND", "AAVAS", "ACE",
    "ACUTAAS", "ABREL", "ABSLAMC", "CPPLUS", "AEGISLOG", "AEGISVOPAK",
    "AETHER", "AFCONS", "AFFLE", "ABDL", "ARE&M", "AMBER", "ANANDRATHI",
    "ANANTRAJ", "ANGELONE", "ANURAS", "APOLLOTYRE", "APTUS", "ASAHIINDIA",
    "ATHERENERG", "ATUL", "AVANTIFEED", "AZAD", "BEML", "BLS", "BAGMANE",
    "BALRAMCHIN", "BANDHANBNK", "BATAINDIA", "BELRISE", "BHARATCOAL",
    "BSOFT", "BBOX", "FIRSTCRY", "BRIGADE", "BIRET", "CCL", "CESC",
    "CIEINDIA", "CANFINHOME", "CANHLIFE", "CAPLIPOINT", "CGCL", "CARBORUNIV",
    "CARTRADE", "CASTROLIND", "CEATLTD", "CEMPRO", "CENTRALBK", "CDSL",
    "CMPDI", "CHAMBLFERT", "CHENNPETRO", "CHOICEIN", "CHOLAHLDNG", "CUB",
    "CLEANMAX", "CLEAN", "COHANCE", "CAMS", "CONCORDBIO", "CRAFTSMAN",
    "CREDITACC", "CROMPTON", "CUPID", "CYIENT", "DOMS", "DATAPATTNS",
    "DEEPAKFERT", "DEEPAKNTR", "DELHIVERY", "DEVYANI", "LALPATHLAB", "DUMMYHE",
    "EIDPARRY", "EIHOTEL", "ELECON", "ELGIEQUIP", "EMAMILTD", "EMCURE",
    "EMMVEE", "ENGINEERSIN", "FINCABLES", "FSL", "FIVESTAR", "FORCEMOT",
    "FRACTAL", "GABRIEL", "GALLANTT", "GRSE", "GILLETTE", "GLAND", "GPIL",
    "GRANULES", "GRAPHITE", "GRAVITA", "GESHIP", "GMDCLTD", "HBLENGINE",
    "HEG", "HFCL", "HSCL", "HOMEFIRST", "HONASA", "HONAUT", "IDBI", "IFCI",
    "IIFL", "INOXINDIA", "IRB", "IRCON", "ITI", "INDIACEM", "INDIAMART",
    "IEX", "IGL", "INOXWIND", "INTELLECT", "IGIL", "IKS", "JBMA", "JKTYRE",
    "JMFINANCIL", "JSWCEMENT", "JAINREC", "JPPOWER", "J&KBANK", "JINDALSAW",
    "JUBLINGREA", "JWL", "JYOTICNC", "KPITTECH", "KSB", "KAJARIACER", "KPIL",
    "KARURVYSYA", "KAYNES", "KEC", "KFINTECH", "KIRLOSBROS", "KIRLOSENG",
    "KIMS", "LTFOODS", "LEMONTREE", "MMTC", "MTARTECH", "MGL", "MANAPPURAM",
    "MRPL", "MINDACORP", "MSUMI", "NATCOPHARM", "NBCC", "NCC", "NSLNISP",
    "NAVA", "NAVINFLUOR", "NETWEB", "NEULANDLAB", "NUVAMA", "NUVOCO",
    "OLAELEC", "OLECTRA", "ONESOURCE", "PCBL", "PGEL", "PNBHOUSING", "PTCIL",
    "PVRINOX", "PARADEEP", "PWL", "PINELABS", "PPLPHARMA", "POLYMED",
    "POONAWALLA", "PFOCUS", "PRIVISCL", "RRKABEL", "RBLBANK", "RHIM",
    "RITES", "RAILTEL", "RAINBOW", "RKFORGE", "REDINGTON", "RPOWER", "RUBICON",
    "SJVN", "SHRIPISTON", "SAGILITY", "SAILIFE", "SAMMAANCAP", "SANSERA",
    "SARDAEN", "SCHNEIDER", "SCI", "SHYAMMETL", "SIGNATURE", "SOBHA",
    "SONACOMS", "STARHEALTH", "STLTECH", "SUMICHEM", "SUNTV", "SWANCORP",
    "SYNGENE", "SYRMA", "TBOTEK", "TDPOWERSYS", "TATACHEM", "TATAELXSI",
    "TATATECH", "TTML", "TECHNOE", "TEGA", "TEJASNET", "TENNIND",
    "THANGAMAYL", "NIACL", "RAMCOCEM", "TIMKEN", "TITAGARH", "TARIL",
    "TRIDENT", "TRITURBINE", "UCOBANK", "UTIAMC", "URBANCO", "USHAMART",
    "VTL", "VISL", "VOGL", "VEDPOWER", "VIJAYA", "WELCORP", "WELSPUNLIV",
    "WHIRLPOOL", "WOCKPHARMA", "ZFCVINDIA", "ZEEL", "ZENTEC", "ZENSARTECH",
    "ZYDUSWELL",
]
MAX_DAILY_HISTORY_DAYS = 18
ATR_PERIOD = 10
FACTOR = 3.0


def load_groww_credentials():
    """Load Groww credentials from the environment or project .env file."""
    env_file = Path(__file__).resolve().with_name(".env")
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        except OSError as exc:
            print(f"[ERROR] Could not read .env file: {exc}", flush=True)
            raise

    return (
        os.getenv("GROWW_API_KEY", "").strip(),
        os.getenv("GROWW_API_SECRET", "").strip(),
    )


def normalize_access_token(token_value):
    if isinstance(token_value, str) and token_value.strip():
        return token_value.strip()
    if isinstance(token_value, dict):
        for key in ("access_token", "token", "jwt", "value"):
            value = token_value.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("data", "payload"):
            nested = token_value.get(key)
            if isinstance(nested, dict):
                return normalize_access_token(nested)
    raise ValueError("Could not locate an access token in the Groww response")


def extract_candle_rows(raw_response):
    if isinstance(raw_response, dict):
        for key in ("candles", "data", "payload"):
            value = raw_response.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = extract_candle_rows(value)
                if nested is not None:
                    return nested
    elif isinstance(raw_response, list):
        return raw_response
    return None


def build_dataframe(candles):
    if not candles:
        return pd.DataFrame()
    if len(candles[0]) == 7:
        columns = ["date", "open", "high", "low", "close", "volume", "oi"]
    elif len(candles[0]) == 6:
        columns = ["date", "open", "high", "low", "close", "volume"]
    else:
        raise ValueError(f"Unexpected daily candle column count: {len(candles[0])}")

    data = pd.DataFrame(candles, columns=columns)
    data["date"] = pd.to_datetime(data["date"])
    for column in ("open", "high", "low", "close"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return (
        data.dropna(subset=["date", "open", "high", "low", "close"])
        .sort_values("date")
        .reset_index(drop=True)
    )


def create_groww_client():
    api_key, api_secret = load_groww_credentials()
    if not api_key or not api_secret:
        raise RuntimeError(
            "Missing Groww credentials. Set GROWW_API_KEY and GROWW_API_SECRET "
            "in the environment or project .env file."
        )
    token = GrowwAPI.get_access_token(api_key=api_key, secret=api_secret)
    return GrowwAPI(normalize_access_token(token))


def safe_get_daily_data(growwapi, symbol: str):
    """Fetch recent daily OHLC candles for Supertrend."""
    try:
        groww_symbol = symbol if symbol.startswith("NSE-") else f"NSE-{symbol}"
        end_time = datetime.now()
        start_time = end_time - timedelta(days=MAX_DAILY_HISTORY_DAYS)
        raw = growwapi.get_historical_candles(
            groww_symbol=groww_symbol,
            exchange=getattr(growwapi, "EXCHANGE_NSE"),
            segment=getattr(growwapi, "SEGMENT_CASH"),
            candle_interval="1day",
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        candles = extract_candle_rows(raw)
        if not candles:
            print(f"[WARN] No daily candle data returned for {symbol}", flush=True)
            return None

        data = build_dataframe(candles)
        if data.empty:
            print(f"[WARN] Daily data frame empty for {symbol}", flush=True)
            return None
        return data.tail(max(30, ATR_PERIOD + 5))
    except Exception as exc:
        print(f"[ERROR] Failed to fetch daily data for {symbol}: {exc}", flush=True)
        return None


def wilder_rma(series: pd.Series, period: int):
    values = series.to_numpy()
    result = [np.nan] * len(values)
    if period < 1 or len(values) < period:
        return pd.Series(result, index=series.index)

    initial_average = np.nanmean(values[:period])
    result[period - 1] = initial_average
    for index in range(period, len(values)):
        previous = result[index - 1] if not np.isnan(result[index - 1]) else initial_average
        result[index] = (previous * (period - 1) + values[index]) / period
    return pd.Series(result, index=series.index)


def calculate_supertrend(data: pd.DataFrame):
    """Return a BUY/SELL direction flip and latest daily candle."""
    if data is None or data.empty:
        return None, None

    work = data.copy()
    work["High"] = pd.to_numeric(work["high"], errors="coerce")
    work["Low"] = pd.to_numeric(work["low"], errors="coerce")
    work["Close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["High", "Low", "Close"]).reset_index(drop=True)
    if len(work) < ATR_PERIOD + 2:
        return None, None

    hl2 = (work["High"] + work["Low"]) / 2
    true_range = pd.concat(
        [
            work["High"] - work["Low"],
            (work["High"] - work["Close"].shift()).abs(),
            (work["Low"] - work["Close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = wilder_rma(true_range, ATR_PERIOD)
    basic_upperband = hl2 + FACTOR * atr
    basic_lowerband = hl2 - FACTOR * atr
    final_upperband = np.full(len(work), np.nan)
    final_lowerband = np.full(len(work), np.nan)
    direction = np.full(len(work), -1)
    close = work["Close"].to_numpy()
    atr_values = atr.to_numpy()
    upper_values = basic_upperband.to_numpy()
    lower_values = basic_lowerband.to_numpy()

    for index in range(len(work)):
        if np.isnan(atr_values[index]):
            continue
        if index == 0 or np.isnan(atr_values[index - 1]):
            final_upperband[index] = upper_values[index]
            final_lowerband[index] = lower_values[index]
            continue

        previous_close = close[index - 1]
        final_upperband[index] = (
            upper_values[index]
            if upper_values[index] < final_upperband[index - 1]
            or previous_close > final_upperband[index - 1]
            else final_upperband[index - 1]
        )
        final_lowerband[index] = (
            lower_values[index]
            if lower_values[index] > final_lowerband[index - 1]
            or previous_close < final_lowerband[index - 1]
            else final_lowerband[index - 1]
        )
        if direction[index - 1] == -1:
            direction[index] = 1 if close[index] > final_upperband[index] else -1
        else:
            direction[index] = -1 if close[index] < final_lowerband[index] else 1

    work["Direction"] = direction
    last, previous = work.iloc[-1], work.iloc[-2]
    signal = None
    if last["Direction"] == 1 and previous["Direction"] == -1:
        signal = "BUY"
    elif last["Direction"] == -1 and previous["Direction"] == 1:
        signal = "SELL"
    return signal, last


def scan_daily_buy_signals(growwapi):
    """Return stocks whose daily Supertrend flipped to BUY."""
    buy_signals = []
    total = len(WATCHLIST)
    for index, symbol in enumerate(WATCHLIST, start=1):
        print(f"[SCAN] {index}/{total} {symbol} | FETCHING", flush=True)
        data = safe_get_daily_data(growwapi, symbol)
        if data is None or data.empty:
            print(f"[SCAN] {index}/{total} {symbol} | SIGNAL=NO_DATA", flush=True)
            continue

        signal, latest = calculate_supertrend(data)
        if latest is None:
            print(
                f"[SCAN] {index}/{total} {symbol} | SIGNAL=INSUFFICIENT_DATA",
                flush=True,
            )
            continue

        price = latest.get("Close")
        price_text = f"{float(price):.2f}" if pd.notna(price) else "NA"
        signal_text = signal or "NO_SIGNAL"
        print(
            f"[SCAN] {index}/{total} {symbol} | PRICE={price_text} | "
            f"SIGNAL={signal_text}",
            flush=True,
        )
        if signal == "BUY":
            buy_signals.append(symbol)
    return buy_signals


def main():
    parser = argparse.ArgumentParser(description="Daily-candle Supertrend BUY scanner")
    parser.add_argument("--dry-run", action="store_true", help="Print results without sending email")
    args = parser.parse_args()

    if not WATCHLIST:
        print("[ERROR] Add NSE symbols to WATCHLIST in SupperDayScan.py.", flush=True)
        return 2

    try:
        growwapi = create_groww_client()
    except Exception as exc:
        print(f"[ERROR] Groww authentication failed for daily scanner: {exc}", flush=True)
        return 1

    print(f"[SCAN] Starting daily Supertrend scan for {len(WATCHLIST)} symbols", flush=True)
    buy_signals = scan_daily_buy_signals(growwapi)
    trading_date = datetime.now().date().isoformat()
    stocks = ",".join(buy_signals) if buy_signals else "NONE"
    if args.dry_run:
        print(f"[DRY RUN] SupperDayScan {trading_date} BUY stocks: {stocks}", flush=True)
    else:
        print(
            f"[BUY SCAN RESULTS] Script=SupperDayScan Date={trading_date} Stocks={stocks}",
            flush=True,
        )
    print(
        f"[SCAN COMPLETE] SupperDayScan found {len(buy_signals)} daily BUY signal(s).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[SCAN] Daily scanner interrupted by user.", flush=True)
        raise SystemExit(130)
