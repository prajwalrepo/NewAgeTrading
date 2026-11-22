#!/usr/bin/env python3
"""Supertrend stock scanner using Yahoo Finance daily candles.

Uses the SupperDayScan watchlist and prints the same concise [SCAN] format.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

ATR_PERIOD = 10
RSI_PERIOD = 14
RSI_BUY_THRESHOLD = 50.0
FACTOR = 3.0
REQUEST_DELAY_SECONDS = 0.25
DEFAULT_PERIOD = "daily"
MAX_HISTORY_DAYS = 90

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


def wilder_rma(series: pd.Series, period: int) -> pd.Series:
    values = series.to_numpy(dtype=float)
    result = np.full(len(values), np.nan)
    if period < 1 or len(values) < period:
        return pd.Series(result, index=series.index)

    initial_average = np.nanmean(values[:period])
    result[period - 1] = initial_average
    for index in range(period, len(values)):
        previous = result[index - 1]
        if not np.isnan(previous):
            result[index] = (previous * (period - 1) + values[index]) / period
        else:
            result[index] = initial_average
    return pd.Series(result, index=series.index)


def calculate_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Calculate Wilder RSI(14) with initial SMA and subsequent Wilder smoothing."""
    values = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    result = np.full(len(values), np.nan)
    if period < 1 or len(values) <= period:
        return pd.Series(result, index=close.index)

    delta = np.diff(values)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for index in range(period, len(values)):
        gain = gains[index - 1]
        loss = losses[index - 1]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            result[index] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[index] = 100.0 - (100.0 / (1.0 + rs))

    return pd.Series(result, index=close.index)


def calculate_supertrend(df: pd.DataFrame):
    """Use the same Wilder ATR and Supertrend calculation as SupperWeekScan.py."""
    if df is None or df.empty:
        return None, None

    work = df.copy()
    work = work.rename(columns={column: column.title() for column in work.columns})
    required = {"High", "Low", "Close", "Date"}
    if not required.issubset(set(work.columns)):
        return None, None

    work = work.dropna(subset=["High", "Low", "Close", "Date"]).reset_index(drop=True)
    if len(work) < ATR_PERIOD + 2:
        return None, None

    hl2 = (work["High"] + work["Low"] ) / 2
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
    supertrend_values = np.full(len(work), np.nan)
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
            supertrend_values[index] = final_upperband[index]
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
        supertrend_values[index] = (
            final_lowerband[index] if direction[index] == 1 else final_upperband[index]
        )

    work["Supertrend"] = supertrend_values
    work["Direction"] = direction
    if len(work) < 2:
        return None, None

    last = work.iloc[-1]
    previous = work.iloc[-2]
    signal = None
    if pd.notna(last["Supertrend"]) and pd.notna(previous["Supertrend"]):
        if last["Direction"] == 1 and previous["Direction"] == -1:
            signal = "BUY"
        elif last["Direction"] == -1 and previous["Direction"] == 1:
            signal = "SELL"
    return signal, last


def yahoo_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol
    return f"{symbol}.NS"


def fetch_history(symbol: str, period: str, retries: int = 2):
    interval = "1wk" if period == "weekly" else "1d"
    history_days = MAX_HISTORY_DAYS if period == "daily" else 180
    end = datetime.now()
    start = end - timedelta(days=history_days)

    for attempt in range(retries + 1):
        try:
            df = yf.download(
                yahoo_symbol(symbol),
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            if df is None or df.empty:
                return None

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            if "Date" not in df.columns:
                return None

            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            })
            df["Date"] = pd.to_datetime(df["Date"])
            for column in ("open", "high", "low", "close"):
                df[column] = pd.to_numeric(df[column], errors="coerce")
            df = df.dropna(subset=["Date", "open", "high", "low", "close"])
            df = df.rename(columns={
                "open": "Open", "high": "High", "low": "Low", "close": "Close",
            })
            df = df.sort_values("Date").tail(400).reset_index(drop=True)
            return df
        except Exception as exc:
            if attempt == retries:
                print(f"[WARN] {symbol} Yahoo Finance fetch failed: {exc}", flush=True)
                return None
            time.sleep(1.0)
    return None


def scan_supertrend(symbols, period: str, delay_seconds: float):
    results = []
    total = len(symbols)
    for index, symbol in enumerate(symbols, start=1):
        df = fetch_history(symbol, period)
        if df is None or df.empty:
            print(
                f"[SCAN] {index}/{total} {symbol} | PRICE=NA | SUPERTREND=NA | SIGNAL=NO_DATA",
                flush=True,
            )
        else:
            signal, latest = calculate_supertrend(df)
            if latest is None:
                print(
                    f"[SCAN] {index}/{total} {symbol} | PRICE=NA | SUPERTREND=NA | SIGNAL=INSUFFICIENT_DATA",
                    flush=True,
                )
            else:
                price = latest.get("Close")
                st_value = latest.get("Supertrend")
                rsi_series = calculate_rsi(df["Close"])
                latest_rsi = rsi_series.iloc[-1] if not rsi_series.empty else np.nan
                rsi_text = f"{float(latest_rsi):.2f}" if pd.notna(latest_rsi) else "NA"
                price_text = f"{float(price):.2f}" if pd.notna(price) else "NA"
                st_text = f"{float(st_value):.2f}" if pd.notna(st_value) else "NA"

                if signal == "BUY" and pd.isna(latest_rsi):
                    signal_text = "RSI_CONFIRMATION_FAILED"
                elif signal == "BUY" and latest_rsi <= RSI_BUY_THRESHOLD:
                    signal_text = "RSI_CONFIRMATION_FAILED"
                else:
                    signal_text = signal or "NO_SIGNAL"

                print(
                    f"[SCAN] {index}/{total} {symbol} | PRICE={price_text} | "
                    f"SUPERTREND={st_text} | RSI={rsi_text} | SIGNAL={signal_text}",
                    flush=True,
                )
                if signal == "BUY":
                    if pd.notna(latest_rsi) and latest_rsi > RSI_BUY_THRESHOLD:
                        results.append({
                            "symbol": symbol,
                            "side": "BUY",
                            "date": pd.Timestamp(latest["Date"]).date().isoformat(),
                            "price": float(price),
                            "supertrend": float(st_value),
                            "rsi": float(latest_rsi),
                        })
                elif signal == "SELL":
                    results.append({
                        "symbol": symbol,
                        "side": "SELL",
                        "date": pd.Timestamp(latest["Date"]).date().isoformat(),
                        "price": float(price),
                        "supertrend": float(st_value),
                        "rsi": float(latest_rsi) if pd.notna(latest_rsi) else None,
                    })

        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return results


def parse_symbols(raw_symbols: str | None):
    if not raw_symbols:
        return WATCHLIST
    symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
    return symbols or WATCHLIST


def main():
    parser = argparse.ArgumentParser(description="Yahoo Finance Supertrend stock scanner")
    parser.add_argument("--period", choices=("daily", "weekly"), default=DEFAULT_PERIOD)
    parser.add_argument("--symbols", help="Comma-separated NSE symbols, for example RELIANCE,TCS,INFY")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY_SECONDS, help="Delay between Yahoo requests")
    args = parser.parse_args()

    symbols = parse_symbols(args.symbols)
    print(
        f"[SCAN] Starting {args.period} Supertrend scan for {len(symbols)} symbols",
        flush=True,
    )
    results = scan_supertrend(symbols, args.period, max(args.delay, 0.0))
    trading_date = datetime.now().date().isoformat()
    print(
        f"[SCAN RESULTS] Script=yfday Period=daily "
        f"ScanDate={trading_date} Results={json.dumps(results, separators=(',', ':'))}",
        flush=True,
    )
    print(
        f"[SCAN COMPLETE] yfday found {len(results)} daily BUY/SELL signal(s).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
