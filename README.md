# 🌽 Grain Market Dashboard

A live dashboard for grain & oilseed futures — **corn, soybeans, soybean meal,
soybean oil, CBOT wheat, and KC wheat** — with daily prices, forward curves,
CFTC fund positioning, USDA WASDE report effects, and price alerts. It
**refreshes itself every day** with zero manual work.

**Live demo:** https://grain-market-dashboard.streamlit.app

## What's inside

| Tab | Content |
|-----|---------|
| 📈 Prices | Interactive price chart with adjustable date range, period high/low/average (all 6 commodities) |
| 📉 Forward Curve | Term structure across the next 8 contract months — contango vs backwardation |
| 💰 Positioning | CFTC Commitments of Traders — managed-money net positioning vs futures price (corn, soybeans, meal, oil, CBOT wheat; 2020–present) |
| 📰 WASDE Reports | Live event study: are prices more volatile on USDA report days? (all 6 commodities) |
| 🔔 Alerts | Your price alert rules and their live status — checked every morning |

Headline cards up top: latest prices with daily change for all six
commodities, current fund positioning, and days since the last WASDE release.

## How the self-updating pipeline works

1. **GitHub Actions** runs daily at ~8am ET (`.github/workflows/daily.yml`).
2. `pipeline/fetch_prices.py` pulls recent data for `ZC=F` / `ZS=F` / `ZM=F` /
   `ZL=F` / `ZW=F` / `KE=F` from Yahoo Finance and upserts into
   `data/prices.db` (idempotent; native units: $/bu, $/ton, $/lb).
3. `pipeline/fetch_curve.py` snapshots the latest close for the next 8
   contract months of each commodity into `data/curve.db` (the Forward Curve tab).
4. `pipeline/fetch_cot.py` attempts the current-year CFTC Disaggregated COT
   zip and merges new weekly reports into `data/cot_weekly.csv`. CFTC's bot
   check often blocks automated fetches — the script logs it and keeps the
   existing data, so the dashboard never breaks.
5. Changed data is committed back to the repo, which triggers Streamlit Cloud
   to redeploy with fresh numbers.

### Price alerts

`data/alerts.yaml` holds alert rules (price above/below levels, big daily
moves) for all six commodities. `pipeline/check_alerts.py` evaluates them
against the latest closes — crossing alerts fire once per crossing and re-arm
when price returns inside the band. A scheduled check runs every morning and
pings on triggers; the Alerts tab shows each rule's live status. Edit
thresholds in `alerts.yaml` — the next check picks them up.

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

- Futures prices: Yahoo Finance (`ZC=F`, `ZS=F`, `ZM=F`, `ZL=F`, `ZW=F`, `KE=F`), 2010–present, ~25,000 trading days
- Positioning: [CFTC Commitments of Traders](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm) (disaggregated, managed money), 1,760 weekly reports 2020–2026 across 5 markets
- Reports: [USDA WASDE archive](https://www.usda.gov/about-usda/general-information/staff-offices/office-chief-economist/commodity-markets/wasde-report), 80 releases 2020–2026

## Related projects

- [corn-soy-futures-sql](https://github.com/josea12345/corn-soy-futures-sql) — the underlying SQL analysis
- [cot-positioning-tracker](https://github.com/josea12345/cot-positioning-tracker) — the COT dataset
- [wasde-report-day-study](https://github.com/josea12345/wasde-report-day-study) — the WASDE event study

Built by [@josea12345](https://github.com/josea12345)
