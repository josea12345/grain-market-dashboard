# 🌽 Grain Market Dashboard

A live dashboard for CBOT corn and soybean futures — daily prices, CFTC fund
positioning, and the market impact of USDA WASDE reports — that **refreshes
itself every day** with zero manual work.

**Live demo:** https://grain-market-dashboard.streamlit.app

## What's inside

| Tab | Content |
|-----|---------|
| 📈 Prices | Interactive price chart with adjustable date range, period high/low/average |
| 💰 Positioning | CFTC Commitments of Traders — managed-money net positioning vs futures price (2020–present) |
| 📰 WASDE Reports | Live event study: are prices more volatile on USDA report days? |

Headline cards up top: latest corn/soy prices with daily change, current fund
positioning (381k corn / 247k soy contracts long as of the latest COT report),
and days since the last WASDE release.

## How the self-updating pipeline works

1. **GitHub Actions** runs daily at ~8am ET (`.github/workflows/daily.yml`).
2. `pipeline/fetch_prices.py` pulls the last 90 days of `ZC=F` / `ZS=F` from
   Yahoo Finance and upserts into `data/prices.db` (idempotent).
3. `pipeline/fetch_cot.py` attempts the current-year CFTC Disaggregated COT
   zip and merges new weekly reports into `data/cot_weekly.csv`. CFTC's bot
   check often blocks automated fetches — the script logs it and keeps the
   existing data, so the dashboard never breaks.
4. Changed data is committed back to the repo, which triggers Streamlit Cloud
   to redeploy with fresh numbers.

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy (free, ~2 minutes)

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. **New app** → select this repo → main file `app.py` → **Deploy**.
3. Daily GitHub Actions commits redeploy the app automatically — no further action needed.

## Data sources

- Futures prices: Yahoo Finance (`ZC=F`, `ZS=F`), 2010–present, 8,404+ trading days
- Positioning: [CFTC Commitments of Traders](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm), 704 weekly reports
- Reports: [USDA WASDE archive](https://www.usda.gov/about-usda/general-information/staff-offices/office-chief-economist/commodity-markets/wasde-report), 80 releases 2020–2026

## Related projects

- [corn-soy-futures-sql](https://github.com/josea12345/corn-soy-futures-sql) — the underlying SQL analysis
- [cot-positioning-tracker](https://github.com/josea12345/cot-positioning-tracker) — the COT dataset
- [wasde-report-day-study](https://github.com/josea12345/wasde-report-day-study) — the WASDE event study

Built by [@josea12345](https://github.com/josea12345)
