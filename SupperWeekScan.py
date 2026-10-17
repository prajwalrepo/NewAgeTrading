#!/usr/bin/env python3
"""Weekly stock scanner for selected Indian stocks.

This scanner reads weekly OHLC candles through the Groww API, applies a weekly
Supertrend-style proxy, and emits a scheduler-readable trade signal when the
weekly trend flips. The main scheduler runs the script every Friday at 11:00 PM IST.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from growwapi import GrowwAPI


def load_groww_credentials():
    """Load Groww creds from environment or a local .env file."""
    env_file = Path(__file__).resolve().with_name(".env")
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
        except Exception:
            pass

    api_key = os.getenv("GROWW_API_KEY", "").strip()
    api_secret = os.getenv("GROWW_API_SECRET", "").strip()
    return api_key, api_secret


WATCHLIST = [
        '360ONE','3MINDIA','ABB','ACC','ACMESOLAR','AIAENG','APLAPOLLO','AUBANK','AWL','AADHARHFC','AARTIIND','AAVAS','ABBOTINDIA','ACE','ACUTAAS',
        'ADANIENSOL','ADANIENT','ADANIGREEN','ADANIPORTS','ADANIPOWER','ATGL','ABCAPITAL','ABFRL','ABLBL','ABREL','ABSLAMC','CPPLUS','AEGISLOG','AEGISVOPAK',
        'AFCONS','AFFLE','AJANTPHARM','ALKEM','ABDL','ARE&M','AMBER','AMBUJACEM','ANANDRATHI','ANANTRAJ','ANGELONE','ANTHEM','ANURAS','APARINDS','APOLLOHOSP',
        'APOLLOTYRE','APTUS','ASAHIINDIA','ASHOKLEY','ASIANPAINT','ASTERDM','ASTRAL','ATHERENERG','ATUL','AUROPHARMA','AIIL','DMART','AXISBANK','BEML','BLS',
        'BSE','BAJAJ-AUTO','BAJFINANCE','BAJAJFINSV','BAJAJHLDNG','BAJAJHFL','BALKRISIND','BALRAMCHIN','BANDHANBNK','BANKBARODA','BANKINDIA','MAHABANK',
        'BATAINDIA','BAYERCROP','BELRISE','BERGEPAINT','BDL','BEL','BHARATFORG','BHEL','BPCL','BHARTIARTL','BHARTIHEXA','BIKAJI','GROWW','BIOCON','BSOFT',
        'BLUEDART','BLUEJET','BLUESTARCO','BBTC','BOSCHLTD','FIRSTCRY','BRIGADE','BRITANNIA','MAPMYINDIA','CCL','CESC','CGPOWER','CIEINDIA','CRISIL',
        'CANFINHOME','CANBK','CANHLIFE','CAPLIPOINT','CGCL','CARBORUNIV','CARTRADE','CASTROLIND','CEATLTD','CEMPRO','CENTRALBK','CDSL','CHALET','CHAMBLFERT',
        'CHENNPETRO','CHOICEIN','CHOLAHLDNG','CHOLAFIN','CIPLA','CUB','CLEAN','COALINDIA','COCHINSHIP','COFORGE','COHANCE','COLPAL','CAMS','CONCORDBIO',
        'CONCOR','COROMANDEL','CRAFTSMAN','CREDITACC','CROMPTON','CUMMINSIND','CYIENT','DCMSHRIRAM','DLF','DOMS','DABUR','DALBHARAT','DATAPATTNS','DEEPAKFERT',
        'DEEPAKNTR','DELHIVERY','DEVYANI','DIVISLAB','DIXON','LALPATHLAB','DRREDDY','EIDPARRY','EIHOTEL','EICHERMOT','ELECON','ELGIEQUIP','EMAMILTD','EMCURE',
        'EMMVEE','ENDURANCE','ENGINERSIN','ERIS','ESCORTS','ETERNAL','EXIDEIND','NYKAA','FEDERALBNK','FACT','FINCABLES','FSL','FIVESTAR','FORCEMOT','FORTIS',
        'GAIL','GVT&D','GMRAIRPORT','GABRIEL','GALLANTT','GRSE','GICRE','GILLETTE','GLAND','GLAXO','GLENMARK','MEDANTA','GODIGIT','GPIL','GODFRYPHLP','GODREJCP',
        'GODREJIND','GODREJPROP','GRANULES','GRAPHITE','GRASIM','GRAVITA','GESHIP','FLUOROCHEM','GMDCLTD','HEG','HBLENGINE','HCLTECH','HDBFS','HDFCAMC',
        'HDFCBANK','HDFCLIFE','HFCL','HAVELLS','HEROMOTOCO','HEXT','HSCL','HINDALCO','HAL','HINDCOPPER','HINDPETRO','HINDUNILVR','HINDZINC','POWERINDIA',
        'HOMEFIRST','HONASA','HONAUT','HUDCO','HYUNDAI','ICICIBANK','ICICIGI','ICICIAMC','ICICIPRULI','IDBI','IDFCFIRSTB','IFCI','IIFL','IRB','IRCON',
        'ITCHOTELS','ITC','ITI','INDGN','INDIACEM','INDIAMART','INDIANB','IEX','INDHOTEL','IOC','IOB','IRCTC','IRFC','IREDA','IGL','INDUSTOWER','INDUSINDBK',
        'NAUKRI','INFY','INOXWIND','INTELLECT','INDIGO','IGIL','IKS','IPCALAB','JBCHEPHARM','JKCEMENT','JBMA','JKTYRE','JMFINANCIL','JSWCEMENT','JSWDULUX',
        'JSWENERGY','JSWINFRA','JSWSTEEL','JAINREC','JPPOWER','J&KBANK','JINDALSAW','JSL','JINDALSTEL','JIOFIN','JUBLFOOD','JUBLINGREA','JUBLPHARMA','JWL',
        'JYOTICNC','KPRMILL','KEI','KPITTECH','KAJARIACER','KPIL','KALYANKJIL','KARURVYSYA','KAYNES','KEC','KFINTECH','KIRLOSENG','KOTAKBANK','KIMS','LTF',
        'LTTS','LGEINDIA','LICHSGFIN','LTFOODS','LTM','LT','LATENTVIEW','LAURUSLABS','THELEELA','LEMONTREE','LENSKART','LICI','LINDEINDIA','LLOYDSME','LODHA',
        'LUPIN','MMTC','MRF','MGL','M&MFIN','M&M','MANAPPURAM','MRPL','MANKIND','MARICO','MARUTI','MFSL','MAXHEALTH','MAZDOCK','MEESHO','MINDACORP','MSUMI',
        'MOTILALOFS','MPHASIS','MCX','MUTHOOTFIN','NATCOPHARM','NBCC','NCC','NHPC','NLCINDIA','NMDC','NSLNISP','NTPCGREEN','NTPC','NH','NATIONALUM','NAVA',
        'NAVINFLUOR','NESTLEIND','NETWEB','NEULANDLAB','NEWGEN','NAM-INDIA','NIVABUPA','NUVAMA','NUVOCO','OBEROIRLTY','ONGC','OIL','OLAELEC','OLECTRA','PAYTM',
        'ONESOURCE','OFSS','POLICYBZR','PCBL','PGEL','PIIND','PNBHOUSING','PTCIL','PVRINOX','PAGEIND','PARADEEP','PATANJALI','PERSISTENT','PETRONET','PFIZER',
        'PHOENIXLTD','PWL','PIDILITIND','PINELABS','PIRAMALFIN','PPLPHARMA','POLYMED','POLYCAB','POONAWALLA','PFC','POWERGRID','PREMIERENE','PRESTIGE','PNB',
        'RRKABEL','RBLBANK','RECLTD','RHIM','RITES','RADICO','RVNL','RAILTEL','RAINBOW','RKFORGE','REDINGTON','RELIANCE','RPOWER','SBFC','SBICARD','SBILIFE',
        'SJVN','SRF','SAGILITY','SAILIFE','SAMMAANCAP','MOTHERSON','SAPPHIRE','SARDAEN','SAREGAMA','SCHAEFFLER','SCHNEIDER','SCI','SHREECEM','SHRIRAMFIN',
        'SHYAMMETL','ENRIN','SIEMENS','SIGNATURE','SOBHA','SOLARINDS','SONACOMS','SONATSOFTW','STARHEALTH','SBIN','SAIL','SUMICHEM','SUNPHARMA','SUNTV',
        'SUNDARMFIN','SUPREMEIND','SPLPETRO','SUZLON','SWANCORP','SWIGGY','SYNGENE','SYRMA','TBOTEK','TVSMOTOR','TATACAP','TATACHEM','TATACOMM','TCS',
        'TATACONSUM','TATAELXSI','TATAINVEST','TMCV','TMPV','TATAPOWER','TATASTEEL','TATATECH','TTML','TECHM','TECHNOE','TEGA','TEJASNET','TENNIND','NIACL',
        'RAMCOCEM','THERMAX','TIMKEN','TITAGARH','TITAN','TORNTPHARM','TORNTPOWER','TARIL','TRAVELFOOD','TRENT','TRIDENT','TRITURBINE','TIINDIA','UCOBANK',
        'UNOMINDA','UPL','UTIAMC','ULTRACEMCO','UNIONBANK','UBL','UNITDSPR','URBANCO','USHAMART','VTL','VBL','VEDL','VIJAYA','VMM','IDEA','VOLTAS','WAAREEENER',
        'WELCORP','WELSPUNLIV','WHIRLPOOL','WIPRO','WOCKPHARMA','YESBANK','ZFCVINDIA','ZEEL','ZENTEC','ZENSARTECH','ZYDUSLIFE','ZYDUSWELL','ECLERX'
      
]

MAX_WEEKLY_HISTORY_DAYS = 180
MULTIPLIER = 3.0
ATR_PERIOD = 7
SMA_PERIOD = 20


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
    raise ValueError("Could not locate an access token in the Groww response")


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
    if not candles:
        return pd.DataFrame()

    n = len(candles[0])
    if n == 7:
        cols = ["date", "open", "high", "low", "close", "volume", "oi"]
    elif n == 6:
        cols = ["date", "open", "high", "low", "close", "volume"]
    else:
        raise ValueError(f"Unexpected weekly candle column count: {n}")

    df = pd.DataFrame(candles, columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)


api_key, api_secret = load_groww_credentials()
if not api_key or not api_secret:
    print("[ERROR] Missing Groww credentials. Set GROWW_API_KEY and GROWW_API_SECRET in the environment or create a .env file in the project root.")
    raise SystemExit(1)

try:
    raw_access_token = GrowwAPI.get_access_token(
        api_key=api_key,
        secret=api_secret,
    )
    access_token = _normalize_access_token(raw_access_token)
    growwapi = GrowwAPI(access_token)
except Exception as exc:  # pragma: no cover - credentials / runtime bootstrap path
    print(f"[ERROR] Groww authentication failed for weekly scanner: {exc}")
    raise SystemExit(1)


def safe_get_weekly_data(symbol: str):
    """Fetch just enough recent weekly OHLC data for the Supertrend signal."""
    try:
        exchange = getattr(growwapi, "EXCHANGE_NSE")
        segment = getattr(growwapi, "SEGMENT_CASH")
        groww_symbol = symbol if symbol.startswith("NSE-") else f"NSE-{symbol}"

        end_time = datetime.now()
        start_time = end_time - timedelta(days=MAX_WEEKLY_HISTORY_DAYS)

        raw = growwapi.get_historical_candles(
            groww_symbol=groww_symbol,
            exchange=exchange,
            segment=segment,
            candle_interval="1week",
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        candles = _extract_candle_rows(raw)
        if not candles:
            print(f"[WARN] No weekly candle data returned for {symbol}")
            return None

        df = _build_dataframe(candles)
        if df.empty:
            print(f"[WARN] Weekly data frame empty for {symbol}")
            return None

        # Groww enforces a maximum lookback for 1-week candles. Keep only the recent bars
        # needed by the weekly Supertrend calculation to avoid extra stale history.
        return df.tail(max(30, ATR_PERIOD + SMA_PERIOD + 5))
    except Exception as exc:  # pragma: no cover - network / API failure path
        print(f"[ERROR] Failed to fetch weekly data for {symbol}: {exc}")
        return None


def calculate_supertrend_proxy(df: pd.DataFrame):
    """Simplified weekly Supertrend signal using only the recent required candles."""
    if df is None or df.empty:
        return None, None

    work = df.copy()
    work["High"] = pd.to_numeric(work["high"], errors="coerce")
    work["Low"] = pd.to_numeric(work["low"], errors="coerce")
    work["Close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["High", "Low", "Close"]).reset_index(drop=True)
    if work.empty:
        return None, None

    effective_atr = min(ATR_PERIOD, max(3, len(work) - 1))
    effective_sma = min(SMA_PERIOD, max(5, len(work) - 1))
    if len(work) < max(5, effective_atr):
        return None, work.iloc[-1] if not work.empty else None

    work["TR"] = np.maximum(
        work["High"] - work["Low"],
        np.maximum(
            (work["High"] - work["Close"].shift(1)).abs(),
            (work["Low"] - work["Close"].shift(1)).abs(),
        ),
    )
    work["ATR"] = work["TR"].rolling(window=effective_atr, min_periods=effective_atr).mean()
    work["Band"] = ((work["High"] + work["Low"]) / 2.0) + MULTIPLIER * work["ATR"]
    work["SMA"] = work["Close"].rolling(window=effective_sma, min_periods=effective_sma).mean()

    if len(work) < 2:
        return None, work.iloc[-1]

    last = work.iloc[-1]
    prev = work.iloc[-2]
    signal = None

    if pd.notna(last["Close"]) and pd.notna(last["Band"]) and pd.notna(last["SMA"]):
        if last["Close"] > prev["Band"] and last["Close"] > last["SMA"]:
            signal = "BUY"
        elif last["Close"] < prev["Band"] and last["Close"] < last["SMA"]:
            signal = "SELL"

    return signal, last


def emit_signal(symbol: str, side: str, price: float, reason: str, time_value: datetime | str):
    """Emit a scheduler-readable trade signal."""
    if isinstance(time_value, datetime):
        signal_time = time_value.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        signal_time = str(time_value)

    print(
        "[TRADE SIGNAL] "
        f"Script=SupperWeekScan Side={side} Time={signal_time} Symbol={symbol} "
        f"Strike=NA Price={float(price):.2f} "
        f"Reason={reason} Lots=1 LotSize=1 Qty=1 OrderPlaced=YES"
    )


def scan_weekly_supertrend():
    """Scan the watchlist and return any weekly BUY/SELL signals."""
    results = []
    for symbol in WATCHLIST:
        data = safe_get_weekly_data(symbol)
        if data is None or data.empty:
            continue

        signal, latest = calculate_supertrend_proxy(data)
        if signal is None or latest is None:
            continue

        reason = f"Weekly Supertrend {signal} on {symbol}"
        price = float(latest["Close"])
        signal_time = pd.Timestamp(latest["date"]).to_pydatetime()
        results.append((symbol, signal, price, reason, signal_time))

    return results


def main():
    parser = argparse.ArgumentParser(description="Weekly supertrend stock scanner")
    parser.add_argument("--dry-run", action="store_true", help="Print signals only without trading logic")
    args = parser.parse_args()

    print(f"[SCAN] Starting weekly supertrend scan for {len(WATCHLIST)} symbols")
    triggers = scan_weekly_supertrend()

    if not triggers:
        print("[SCAN] No weekly BUY/SELL supertrend trigger found for the current watchlist.")
        return 0

    for symbol, side, price, reason, signal_time in triggers:
        emit_signal(symbol, side, price, reason, signal_time)

    print(f"[SCAN] Found {len(triggers)} weekly signal(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[SCAN] Weekly scanner interrupted by user.")
        raise SystemExit(130)
