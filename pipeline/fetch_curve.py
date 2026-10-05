"""Forward-curve snapshot: fetch the latest close for the next N contract
months of each commodity and store in data/curve.db. Run daily after
fetch_prices.py; the dashboard's Forward Curve tab reads this table.

Yahoo individual-contract symbols: ROOT + month code + 2-digit year + .CBT
(e.g. ZCZ26.CBT). KC wheat (KE) is also quoted under .CBT on Yahoo.
"""
import json
import sqlite3
import time
import urllib.request
from datetime import date

DB = "data/curve.db"
MONTHS = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
          "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}
CONTRACT_MONTHS = {
    "ZC": ["H", "K", "N", "U", "Z"],
    "ZS": ["F", "H", "K", "N", "Q", "U", "X"],
    "ZM": ["F", "H", "K", "N", "Q", "U", "V", "Z"],
    "ZL": ["F", "H", "K", "N", "Q", "U", "V", "Z"],
    "ZW": ["H", "K", "N", "U", "Z"],
    "KE": ["H", "K", "N", "U", "Z"],
}
DIV = {"ZC": 100.0, "ZS": 100.0, "ZM": 1.0, "ZL": 100.0, "ZW": 100.0, "KE": 100.0}
UNIT = {"ZC": "$/bu", "ZS": "$/bu", "ZM": "$/ton",
        "ZL": "$/lb", "ZW": "$/bu", "KE": "$/bu"}
N_CONTRACTS = 8
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}


def upcoming_contracts(root, n=N_CONTRACTS):
    """Next n contract codes, e.g. [('Z26', '2026-12-01'), ...]."""
    valid = set(CONTRACT_MONTHS[root])
    code_for_month = {v: k for k, v in MONTHS.items()}
    out, today = [], date.today()
    yy, mm = today.year, today.month
    while len(out) < n and yy < today.year + 4:
        code = code_for_month[mm]
        if code in valid:
            # skip the current month's contract late in the month (rolling off)
            if not (yy == today.year and mm == today.month and today.day > 20):
                out.append((f"{code}{str(yy)[-2:]}", f"{yy}-{mm:02d}-01"))
        mm += 1
        if mm > 12:
            mm, yy = 1, yy + 1
    return out[:n]


def fetch_close(yahoo_symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
           "?range=5d&interval=1d")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    result = payload["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    closes = [c for c in closes if c is not None]
    return closes[-1] if closes else None


def main():
    conn = sqlite3.connect(DB)
    conn.execute("DROP TABLE IF EXISTS forward_curve")
    conn.execute("CREATE TABLE forward_curve "
                 "(commodity TEXT, contract TEXT, expiry TEXT, close REAL, unit TEXT)")
    n = 0
    today = date.today().isoformat()
    for root in CONTRACT_MONTHS:
        for code, expiry in upcoming_contracts(root):
            ysym = f"{root}{code}.CBT"
            try:
                c = fetch_close(ysym)
                time.sleep(0.3)
            except Exception as e:
                print(f"  skip {ysym}: {e}")
                continue
            if c is None:
                print(f"  skip {ysym}: no quote")
                continue
            conn.execute("INSERT INTO forward_curve VALUES (?,?,?,?,?)",
                         (root, f"{root}{code}", expiry, c / DIV[root], UNIT[root]))
            n += 1
    conn.commit()
    conn.close()
    print(f"curve snapshot {today}: {n} contracts stored")


if __name__ == "__main__":
    main()
