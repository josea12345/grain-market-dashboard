"""Weekly COT refresh (best-effort).

Tries to download the current year's CFTC Disaggregated COT zip and merge any
new weekly reports into data/cot_weekly.csv. cftc.gov sits behind a bot check
that blocks most automated fetches — if the download fails, the script logs it
and keeps the existing data untouched. The dashboard always works off the CSV.
"""
import csv
import io
import urllib.request
import zipfile
from datetime import date

CSV = "data/cot_weekly.csv"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
WANT = {"CORN - CHICAGO BOARD OF TRADE": "CORN",
        "SOYBEANS - CHICAGO BOARD OF TRADE": "SOYBEANS"}


def num(v):
    return int(v.strip().replace(",", "")) if v and v.strip() else 0


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
            key = (row["Report_Date_as_YYYY-MM-DD"].strip(), WANT[market])
            close = row.get("futures_close", "")
            rows[key] = {
                "report_date": key[0],
                "commodity": key[1],
                "mmoney_net": num(row["M_Money_Positions_Long_All"])
                              - num(row["M_Money_Positions_Short_All"]),
                "futures_close": close,  # refreshed by the dashboard build step
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
