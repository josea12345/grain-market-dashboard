"""Weekly COT refresh (best-effort).

Tries to download the current year's CFTC Disaggregated COT zip and merge any
new weekly reports into data/cot_weekly.csv. cftc.gov sits behind a bot check
that blocks most automated fetches — if the download fails, the script logs it
and keeps the existing data untouched. The dashboard always works off the CSV.

KC wheat has no managed-money series in the CFTC disaggregated reports, so
positioning covers corn, soybeans, soybean meal, soybean oil, and CBOT wheat.
"""
import csv
import io
import sqlite3
import urllib.request
import zipfile
from datetime import date, timedelta

CSV = "data/cot_weekly.csv"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
WANT = {
    "CORN - CHICAGO BOARD OF TRADE": ("CORN", "ZC"),
    "SOYBEANS - CHICAGO BOARD OF TRADE": ("SOYBEANS", "ZS"),
    "SOYBEAN MEAL - CHICAGO BOARD OF TRADE": ("SOYBEAN MEAL", "ZM"),
    "SOYBEAN OIL - CHICAGO BOARD OF TRADE": ("SOYBEAN OIL", "ZL"),
    "WHEAT-SRW - CHICAGO BOARD OF TRADE": ("WHEAT-SRW", "ZW"),
}


def num(v):
    return int(v.strip().replace(",", "")) if v and v.strip() else 0


def close_on(closes, sym, report_date):
    d = report_date
    while d >= "2010-01-01":
        if d in closes.get(sym, {}):
            return closes[sym][d]
        y, m, dd = int(d[:4]), int(d[5:7]), int(d[8:10])
        d = (date(y, m, dd) - timedelta(days=1)).isoformat()
    return 0


def main():
    year = date.today().year
    url = f"https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            blob = r.read()
        if not blob.startswith(b"PK"):
            raise ValueError("not a zip (bot check page?)")
    except Exception as e:  # noqa: BLE001 — any failure keeps existing data
        print(f"COT refresh skipped ({e}); existing data kept.")
        return

    conn = sqlite3.connect("data/prices.db")
    closes = {}
    for sym, d, c in conn.execute("SELECT symbol, date, close FROM daily_prices"):
        closes.setdefault(sym, {})[d] = c
    conn.close()

    with open(CSV, newline="") as f:
        rows = {(r["report_date"], r["commodity"]): r for r in csv.DictReader(f)}

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = [i for i in z.namelist() if i.endswith(".txt")][0]
        text = io.TextIOWrapper(z.open(name), encoding="utf-8-sig")
        added = 0
        for row in csv.DictReader(text):
            market = row["Market_and_Exchange_Names"].strip()
            if market not in WANT:
                continue
            comm, sym = WANT[market]
            key = (row["Report_Date_as_YYYY-MM-DD"].strip(), comm)
            rows[key] = {
                "report_date": key[0],
                "commodity": comm,
                "mmoney_net": num(row["M_Money_Positions_Long_All"])
                              - num(row["M_Money_Positions_Short_All"]),
                "futures_close": round(close_on(closes, sym, key[0]), 4),
            }
            added += 1

    with open(CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["report_date", "commodity",
                                          "mmoney_net", "futures_close"])
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))
    print(f"COT refresh merged ({added} rows parsed); {len(rows)} weeks in csv.")


if __name__ == "__main__":
    main()
