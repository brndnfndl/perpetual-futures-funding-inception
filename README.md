# perpetual-futures-funding-inception

An FE800 (master's research) project. It uses the staggered launch of Kalshi's
crypto perpetual-futures markets as a natural panel to ask what funding rates do
in the first weeks of a perp's life, when the market has no history and no
established arbitrage community. The full write-up is in [`docs/writeup.pdf`](docs/writeup.pdf).

## What it does

Kalshi launched 13 crypto perps over about seven waves in mid-2026. Because each
market has funding data from hour zero, the launches line up as independent
"inception events" on a common *days-since-launch* axis.

The project:

1. **collects** the data: Kalshi funding rates, candles and market metadata
   (`kalshi_perps_data.py`), plus OKX perp and CoinGecko spot as reference
   prices (`external_data.py`);
2. **the main finding** (`main_finding.ipynb`): pooled regression of
   `|funding rate|` on days-since-launch across all 13 markets, HC1 robust
   standard errors;
3. **robustness** (`further_statistical_tests.ipynb`): cluster-robust SEs,
   leave-one-market-out, a sign test, a LOWESS shape check, and an exponential
   decay fit;
4. **exploratory extensions** (`exploratory_extensions.ipynb`): basis vs.
   offshore venues, a mark-price artifact check, open-interest ramp-up,
   cross-market comovement, and a look at Kalshi's funding clamp. Several flagged as nulls.

## Finding

Pooled across the 13 markets (2,539 eight-hour readings), funding magnitude
declines with market age: the slope on days-since-launch is **−0.0117 bps/day**
(HC1 SE 0.001, z ≈ −11, p < 0.001) off a **~0.70 bps** intercept. Funding is
disproportionately negative (perp below reference, shorts paid) in the first
~10–15 days and decays toward zero as each market matures. An exponential decay
fits about as well as a line and implies a half-life of roughly two weeks.

The cross-sectional questions: does prior offshore liquidity predict faster
settling, is there a common funding factor; come back **inconclusive**, and are
reported as such: at n = 13 markets over ~2 months the panel is underpowered for
a cross-market split, not evidence of no effect.

## Data & methods

| Piece | Source | Notes |
|---|---|---|
| Kalshi funding rates, candles, market list | Kalshi trade API (`/margin/...`), RSA-signed | 8-hour funding cycle; `mark_price` is a modeled index |
| Offshore perp reference | OKX `history-candles`, public | Binance/Bybit refuse this network on geo grounds |
| Spot reference | CoinGecko `market_chart/range`, public | multi-exchange aggregate |
| Main model | pooled OLS, `|funding_bps| ~ days_since_launch`, HC1 | clustered-by-market SEs shown as a robustness check |

## Repo structure

```
perpetual-futures-funding-inception/
├── README.md   requirements.txt   .gitignore   .env.example
├── kalshi_perps_data.py            # collector 1: Kalshi API (needs credentials)
├── external_data.py                # collector 2: OKX + CoinGecko (public)
├── main_finding.ipynb              # the headline regression
├── further_statistical_tests.ipynb # robustness checks
├── exploratory_extensions.ipynb    # basis / microstructure appendix, incl. nulls
├── docs/
│   └── writeup.pdf                 # full research write-up
└── kalshi_data/                    # cached CSVs the notebooks read
    ├── funding_rates_all.csv, perp_markets.csv, candles_*.csv
    └── external/  okx_*.csv, coingecko_*.csv
```

## How to run

The notebooks read the cached CSVs in `kalshi_data/` and need no credentials:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
jupyter notebook main_finding.ipynb
```

To refresh the data, set the two Kalshi variables (copy `.env.example` to
`.env`) and run `python kalshi_perps_data.py` then `python external_data.py`.

## Notes

- **Credentials** live only in the environment (`KALSHI_ACCESS_KEY_ID`,
  `KALSHI_PRIVATE_KEY_PATH`). No keys are in the code; `.env` and `*.pem` are
  gitignored.
- The `kalshi_data/` snapshot is from mid-August 2026, ~52 days after the first
  launch. Re-running the collectors extends the panel as more history accrues.
- `mark_price` from the funding endpoint is a modeled index, not necessarily a
  traded price; `exploratory_extensions.ipynb` section 3 checks the main tracking
  result against actual traded and bid/ask-midpoint prices for that reason.

