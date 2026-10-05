"""Daily price refresh: pull the last 90 days of ZC=F / ZS=F from Yahoo
Finance and upsert into data/prices.db. Safe to run daily; idempotent."""
import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

DB = "data/prices.db"
SYMBOLS = {"ZC": "ZC%3DF", "ZS": "ZS%3DF"}
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}


def fetch(symbol, days=100):
    now = int(time.time())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOLS[symbol]}"
           f"?period1={now - days * 86400}&period2={now}&interval=1d")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.load(r)
    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(stamps):
        o, h, l, c = (quote[k][i] for k in ("open", "high", "low", "close"))
        if c is None:
            continue
        v = quote["volume"][i]
        out.append((symbol,
                    datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                    o, h, l, c, int(v) if v else 0))
    return out


def main():
    conn = sqlite3.connect(DB)
    n = 0
    for sym in SYMBOLS:
        rows = fetch(sym)
        conn.executemany("INSERT OR REPLACE INTO daily_prices VALUES (?,?,?,?,?,?,?)", rows)
        n += len(rows)
    conn.commit()
    latest = conn.execute("SELECT MAX(date) FROM daily_prices").fetchone()[0]
    conn.close()
    print(f"upserted {n} rows; latest trading day in db: {latest}")


if __name__ == "__main__":
    main()
