"""
External Reference Data Collector
================================
Pulls two independent reference prices for each Kalshi perp, to test what
the Kalshi basis is measured against:

  1. OKX USDT-margined perpetual swap price (same instrument type,
     different venue -> tests regulatory/venue segmentation). Binance and
     Bybit's public market-data endpoints both refuse connections from
     this network on geo/compliance grounds, so OKX is the offshore-perp
     reference instead.
  2. CoinGecko spot price (independent of any single perp venue -> tests
     basis against "true" spot).

Reads kalshi_data/funding_rates_all.csv to anchor each pull to the market's
launch. No credentials needed -- both endpoints are public.
"""

import time
import datetime as dt
from pathlib import Path

import requests
import pandas as pd

OUTPUT_DIR = Path("kalshi_data/external")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Kalshi ticker -> (OKX perpetual swap instId, CoinGecko coin id)
TICKER_MAP = {
    "KXBTCPERP": ("BTC-USDT-SWAP", "bitcoin"),
    "KXETHPERP": ("ETH-USDT-SWAP", "ethereum"),
    "KXSOLPERP": ("SOL-USDT-SWAP", "solana"),
    "KXXRPPERP": ("XRP-USDT-SWAP", "ripple"),
    "KXHYPEPERP": ("HYPE-USDT-SWAP", "hyperliquid"),
    "KXLINKPERP": ("LINK-USDT-SWAP", "chainlink"),
    "KXBCHPERP": ("BCH-USDT-SWAP", "bitcoin-cash"),
    "KXDOGEPERP": ("DOGE-USDT-SWAP", "dogecoin"),
    "KXKSHIBPERP": ("SHIB-USDT-SWAP", "shiba-inu"),
    "KXLTCPERP": ("LTC-USDT-SWAP", "litecoin"),
    "KXSUIPERP": ("SUI-USDT-SWAP", "sui"),
    "KXNEARPERP": ("NEAR-USDT-SWAP", "near"),
    "KXZECPERP": ("ZEC-USDT-SWAP", "zcash"),
}

OKX_HOST = "https://www.okx.com"
COINGECKO_HOST = "https://api.coingecko.com/api/v3"


def get_launch_windows() -> dict:
    funding = pd.read_csv("kalshi_data/funding_rates_all.csv")
    funding["funding_time"] = pd.to_datetime(funding["funding_time"], utc=True)
    launches = funding.groupby("market_ticker")["funding_time"].min()
    return {
        ticker: (start - pd.Timedelta(days=1)).to_pydatetime()
        for ticker, start in launches.items()
    }


def fetch_okx_klines(inst_id: str, start: dt.datetime, end: dt.datetime,
                      bar: str = "1H") -> pd.DataFrame:
    """OKX history-candles: paginate backward in time via the 'after' cursor."""
    rows = []
    start_ms = int(start.timestamp() * 1000)
    after = int(end.timestamp() * 1000) + 1
    while True:
        r = requests.get(
            f"{OKX_HOST}/api/v5/market/history-candles",
            params={"instId": inst_id, "bar": bar, "after": after, "limit": 100},
            timeout=30,
        )
        data = r.json()
        if data.get("code") != "0":
            raise ValueError(f"{inst_id}: OKX error {data.get('code')} {data.get('msg')}")
        page = data["data"]
        if not page:
            break
        rows.extend(page)
        oldest_ts = int(page[-1][0])
        if oldest_ts <= start_ms:
            break
        after = oldest_ts
        time.sleep(0.15)

    df = pd.DataFrame(rows, columns=[
        "ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm",
    ])
    df = df[["ts", "open", "high", "low", "close"]].copy()
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df = df.drop(columns=["ts"]).sort_values("timestamp").reset_index(drop=True)
    return df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]


def fetch_coingecko_spot(coin_id: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    for attempt in range(2):
        r = requests.get(
            f"{COINGECKO_HOST}/coins/{coin_id}/market_chart/range",
            params={"vs_currency": "usd",
                    "from": int(start.timestamp()), "to": int(end.timestamp())},
            timeout=30,
        )
        data = r.json()
        if "prices" in data:
            df = pd.DataFrame(data["prices"], columns=["ts_ms", "price"])
            df["timestamp"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
            return df[["timestamp", "price"]]
        if data.get("status", {}).get("error_code") == 429 and attempt == 0:
            time.sleep(30)
            continue
        raise ValueError(f"{coin_id}: CoinGecko error {data}")
    raise ValueError(f"{coin_id}: CoinGecko rate-limited after retry")


if __name__ == "__main__":
    launch_windows = get_launch_windows()
    now = dt.datetime.now(dt.timezone.utc)

    for ticker, (okx_inst_id, coingecko_id) in TICKER_MAP.items():
        if ticker not in launch_windows:
            print(f"[skip] {ticker}: no funding history yet, no launch date to anchor pull")
            continue
        start = launch_windows[ticker]

        okx_fname = OUTPUT_DIR / f"okx_{ticker}.csv"
        if okx_fname.exists():
            print(f"[okx] {ticker}: already have {okx_fname.name}, skipping")
        else:
            try:
                odf = fetch_okx_klines(okx_inst_id, start, now)
                odf.to_csv(okx_fname, index=False)
                print(f"[okx] {ticker} ({okx_inst_id}): {len(odf)} rows -> {okx_fname.name}")
            except Exception as e:
                print(f"[okx] {ticker} ({okx_inst_id}): FAILED ({e})")
            time.sleep(0.3)

        cg_fname = OUTPUT_DIR / f"coingecko_{ticker}.csv"
        if cg_fname.exists():
            print(f"[coingecko] {ticker}: already have {cg_fname.name}, skipping")
        else:
            try:
                cdf = fetch_coingecko_spot(coingecko_id, start, now)
                cdf.to_csv(cg_fname, index=False)
                print(f"[coingecko] {ticker} ({coingecko_id}): {len(cdf)} rows -> {cg_fname.name}")
            except Exception as e:
                print(f"[coingecko] {ticker} ({coingecko_id}): FAILED ({e})")
            time.sleep(6)

    print("\nDone. CSVs are in ./kalshi_data/external/")
