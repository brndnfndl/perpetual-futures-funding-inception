"""
Kalshi Perps Data Collector
===========================
Pulls perpetual futures data from Kalshi for empirical research:
  1. List of perp markets (tickers, launch dates, volume, open interest)
  2. Candlestick price history per market
  3. Historical funding rates  <-- key series for basis/carry analysis

Auth
----
Kalshi signs requests with an RSA private key. Set two environment
variables before running (see .env.example):

    KALSHI_ACCESS_KEY_ID     the access-key UUID from your Kalshi account
    KALSHI_PRIVATE_KEY_PATH  path to the PEM private-key file

Nothing secret is stored in this file. The PEM file and any .env are
gitignored.
"""

import base64
import datetime as dt
import os
import time
from pathlib import Path

import requests
import pandas as pd
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ----------------------------------------------------------------------
# Config -- read entirely from the environment
# ----------------------------------------------------------------------
try:  # optional: load a local .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ACCESS_KEY_ID = os.environ.get("KALSHI_ACCESS_KEY_ID")
PRIVATE_KEY_PATH = os.environ.get("KALSHI_PRIVATE_KEY_PATH")

if not ACCESS_KEY_ID or not PRIVATE_KEY_PATH:
    raise SystemExit(
        "Set KALSHI_ACCESS_KEY_ID and KALSHI_PRIVATE_KEY_PATH "
        "(see .env.example) before running."
    )

BASE_HOST = "https://api.elections.kalshi.com"
API_PREFIX = "/trade-api/v2"                   # signing includes this prefix
OUTPUT_DIR = Path("kalshi_data")
OUTPUT_DIR.mkdir(exist_ok=True)

# ----------------------------------------------------------------------
# Auth: RSA-PSS request signing
# Message = timestamp_ms + METHOD + path (path includes /trade-api/v2,
# excludes the query string). SHA-256, MGF1-SHA256, salt = 32 bytes.
# ----------------------------------------------------------------------
_private_key = None

def load_private_key():
    global _private_key
    if _private_key is None:
        pem = Path(PRIVATE_KEY_PATH).read_bytes()
        _private_key = serialization.load_pem_private_key(pem, password=None)
    return _private_key

def auth_headers(method: str, path: str) -> dict:
    ts_ms = str(int(time.time() * 1000))
    message = (ts_ms + method.upper() + path).encode("utf-8")
    signature = load_private_key().sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=hashes.SHA256().digest_size,  # 32 bytes
        ),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": ACCESS_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }

def get(path: str, params: dict | None = None) -> dict:
    """Signed GET. `path` starts after the prefix, e.g. '/margin/markets'."""
    full_path = API_PREFIX + path
    url = BASE_HOST + full_path
    r = requests.get(url, params=params, headers=auth_headers("GET", full_path), timeout=30)
    r.raise_for_status()
    return r.json()

def get_paginated(path: str, list_key: str, params: dict | None = None) -> list:
    """Follow cursor pagination until exhausted."""
    params = dict(params or {})
    out = []
    while True:
        data = get(path, params)
        out.extend(data.get(list_key, []))
        cursor = data.get("cursor")
        if not cursor:
            break
        params["cursor"] = cursor
        time.sleep(0.1)
    return out

# ----------------------------------------------------------------------
# 1. Perp markets: what's listed, when it launched, current stats
# ----------------------------------------------------------------------
def fetch_perp_markets() -> pd.DataFrame:
    markets = get_paginated("/margin/markets", "markets")
    df = pd.json_normalize(markets)
    df.to_csv(OUTPUT_DIR / "perp_markets.csv", index=False)
    print(f"[markets] {len(df)} perp markets saved")
    # Launch dates
    for col in ("created_time", "open_time"):
        if col in df.columns:
            print(df[["ticker", col]].to_string(index=False))
            break
    return df

# ----------------------------------------------------------------------
# 2. Candlesticks: perp price history per market
#    Adjust period_interval as needed (minutes: 1, 60, 1440 typical).
# ----------------------------------------------------------------------
def fetch_candlesticks(ticker: str, start: dt.datetime, end: dt.datetime,
                       period_minutes: int = 60) -> pd.DataFrame:
    params = {
        "start_ts": int(start.timestamp()),
        "end_ts": int(end.timestamp()),
        "period_interval": period_minutes,
    }
    data = get(f"/margin/markets/{ticker}/candlesticks", params)
    if "candlesticks" not in data:
        raise ValueError(f"{ticker}: response missing 'candlesticks' key: {data!r}")
    df = pd.json_normalize(data["candlesticks"])
    fname = OUTPUT_DIR / f"candles_{ticker}_{period_minutes}m.csv"
    df.to_csv(fname, index=False)
    print(f"[candles] {ticker}: {len(df)} rows -> {fname.name}")
    return df

# ----------------------------------------------------------------------
# 3. Historical funding rates:
#    Funding accrues on an 8-hour cycle -> ~3 observations per day.
# ----------------------------------------------------------------------
def fetch_funding_history(ticker: str | None = None) -> pd.DataFrame:
    params = {}
    if ticker:
        params["ticker"] = ticker
    rates = get_paginated("/margin/funding_rates/historical",
                          "funding_rates", params)
    df = pd.json_normalize(rates)
    suffix = ticker or "all"
    fname = OUTPUT_DIR / f"funding_rates_{suffix}.csv"
    df.to_csv(fname, index=False)
    print(f"[funding] {suffix}: {len(df)} rows -> {fname.name}")
    return df

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Step 1: discover what's listed and when each market opened
    try:
        markets_df = fetch_perp_markets()
    except requests.HTTPError as e:
        print(f"[markets] FAILED ({e}) -- aborting, nothing to iterate over")
        raise SystemExit(1)

    # Step 2: pull funding-rate history for everything
    try:
        funding_df = fetch_funding_history()
    except requests.HTTPError as e:
        print(f"[funding] FAILED ({e}) -- continuing without funding data")

    # Step 3: candles for each market, from its first funding
    #   timestamp (a good proxy for launch) to now.
    now = dt.datetime.now(dt.timezone.utc)

    # Earliest funding_time per ticker = approximate launch date
    launch_dates = (
        funding_df.assign(funding_time=pd.to_datetime(funding_df["funding_time"], utc=True))
        .groupby("market_ticker")["funding_time"]
        .min()
    )

    for ticker, start_ts in launch_dates.items():
        # pad back one day so we don't clip the true first trades
        start = (start_ts - pd.Timedelta(days=1)).to_pydatetime()
        try:
            fetch_candlesticks(ticker, start, now, period_minutes=1440)
        except requests.HTTPError as e:
            print(f"[candles] {ticker}: FAILED ({e}) -- check endpoint/params")
        time.sleep(0.2)

    print("\nDone. CSVs are in ./kalshi_data/")
