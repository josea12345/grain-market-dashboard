"""Daily price refresh: pull recent data for grain/oilseed futures from Yahoo
Finance and upsert into data/prices.db. Safe to run daily; idempotent.

Prices are stored in native display units (divisor applied at fetch):
  ZC/ZS/ZW/KE -> $/bushel,  ZM -> $/short ton,  ZL -> $/pound.
"""
import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

DB = "data/prices.db"
COMMODITIES = {
    "ZC": {"yahoo": "ZC%3DF", "div": 100.0, "unit": "$/bu",  "name": "Corn"},
    "ZS": {"yahoo": "ZS%3DF", "div": 100.0, "unit": "$/bu",  "name": "Soybeans"},
    "ZM": {"yahoo": "ZM%3DF", "div": 1.0,   "unit": "$/ton", "name": "Soybean Meal"},
    "ZL": {"yahoo": "ZL%3DF", "div": 100.0, "unit": "$/lb",  "name": "Soybean Oil"},
    "ZW": {"yahoo": "ZW%3DF", "div": 100.0, "unit": "$/bu",  "name": "CBOT Wheat"},
    "KE": {"yahoo": "KE%3DF", "div": 100.0, "unit": "$/bu",  "name": "KC Wheat"},
}
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}


def migrate(conn):
    """One-time: add unit column; convert legacy cents rows to native units."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_prices)")]
    if "unit" not in cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN unit TEXT DEFAULT ''")
    conn.execute(
        "UPDATE daily_prices SET open=open/100.0, high=high/100.0, "
        "low=low/100.0, close=close/100.0, unit='$/bu' WHERE unit=''")


def fetch(symbol, days=100):
    now = int(time.time())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{COMMODITIES[symbol]['yahoo']}"
           f"?period1={now - days * 86400}&period2={now}&interval=1d")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.load(r)
    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    div, unit = COMMODITIES[symbol]["div"], COMMODITIES[symbol]["unit"]
    out = []
    for i, ts in enumerate(stamps):
        o, h, l, c = (quote[k][i] for k in ("open", "high", "low", "close"))
        if c is None:
            continue
        v = quote["volume"][i]
        out.append((symbol,
                    datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                    o / div, h / div, l / div, c / div,
                    int(v) if v else 0, unit))
    return out


def main():
    conn = sqlite3.connect(DB)
    migrate(conn)
    n = 0
    for sym in COMMODITIES:
        has = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE symbol=?", (sym,)).fetchone()[0]
        rows = fetch(sym, days=100 if has else 6000)
        conn.executemany(
            "INSERT OR REPLACE INTO daily_prices "
            "(symbol,date,open,high,low,close,volume,unit) VALUES (?,?,?,?,?,?,?,?)",
            rows)
        n += len(rows)
    conn.commit()
    latest = conn.execute("SELECT MAX(date) FROM daily_prices").fetchone()[0]
    conn.close()
    print(f"upserted {n} rows; latest trading day in db: {latest}")


if __name__ == "__main__":
    main()
