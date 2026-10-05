"""Evaluate price alert rules against the latest data.

Reads data/alerts.yaml, compares with the two most recent closes in
data/prices.db, and prints a human-readable summary of triggered alerts.
Crossing alerts fire once per crossing (state in data/alert_state.json);
daily-move alerts fire whenever the day's move exceeds the threshold.

Exit code 0 always; prints "NO_ALERTS" when nothing fired.
"""
import json
import os
import sqlite3

DB = "data/prices.db"
RULES = "data/alerts.yaml"
STATE = "data/alert_state.json"


def load_rules():
    rules, cur = [], None
    with open(RULES) as f:
        for line in f:
            s = line.strip()
            if s.startswith("- symbol:"):
                cur = {"symbol": s.split(":")[1].strip()}
                rules.append(cur)
            elif cur is not None and ":" in s and not s.startswith("#"):
                k, v = s.split(":", 1)
                v = v.strip()
                try:
                    v = float(v)
                except ValueError:
                    pass
                cur[k.strip()] = v
    return rules


def latest_closes(symbol):
    conn = sqlite3.connect(DB)
    if symbol == "CRUSH":
        rows = conn.execute(
            "SELECT b.date, 44*(m.close/2000.0) + 11*o.close - b.close, '$/bu' "
            "FROM (SELECT date, close FROM daily_prices WHERE symbol='ZS') b "
            "JOIN (SELECT date, close FROM daily_prices WHERE symbol='ZM') m "
            "  ON b.date = m.date "
            "JOIN (SELECT date, close FROM daily_prices WHERE symbol='ZL') o "
            "  ON b.date = o.date "
            "ORDER BY b.date DESC LIMIT 2").fetchall()
    else:
        rows = conn.execute(
            "SELECT date, close, unit FROM daily_prices WHERE symbol=? "
            "ORDER BY date DESC LIMIT 2", (symbol,)).fetchall()
    conn.close()
    return rows  # [(date, price, unit), ...] newest first


def fmt(price, unit):
    return f"${price:,.2f}{unit}" if unit == "$/ton" else f"${price:.2f}"


def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    fired = []

    for rule in load_rules():
        sym, name = rule["symbol"], rule.get("name", rule["symbol"])
        rows = latest_closes(sym)
        if len(rows) < 2:
            continue
        (d1, p1, u1), (d0, p0, u0) = rows[0], rows[1]
        move = 100 * (p1 - p0) / p0
        key = f"{sym}"

        # level crossings (fire once, re-arm on return inside the band)
        for side, level in (("above", rule.get("above")), ("below", rule.get("below"))):
            if level is None:
                continue
            crossed = (p0 <= level < p1) if side == "above" else (p0 >= level > p1)
            armed = state.get(key, {}).get(side, True)
            if crossed and armed:
                fired.append(
                    f"{name} crossed {fmt(level, u1)} ({side}): now {fmt(p1, u1)} on {d1}")
                state.setdefault(key, {})[side] = False
            elif side == "above" and p1 < level:
                state.setdefault(key, {})[side] = True
            elif side == "below" and p1 > level:
                state.setdefault(key, {})[side] = True

        # big daily move
        thresh = rule.get("daily_move_pct")
        if thresh and abs(move) >= thresh:
            fired.append(
                f"{name} moved {move:+.2f}% today ({fmt(p0, u1)} -> {fmt(p1, u1)} on {d1})")

    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)

    if fired:
        print("ALERTS TRIGGERED:")
        for a in fired:
            print(" -", a)
    else:
        print("NO_ALERTS")


if __name__ == "__main__":
    main()
