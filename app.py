"""Grain Market Dashboard — grain & oilseed futures.

Live view of futures prices, forward curves, CFTC positioning, USDA WASDE
report effects, and price alerts. Data refreshes daily via pipeline/.
"""
import csv
import sqlite3
from datetime import date, datetime

import matplotlib
import matplotlib.pyplot as plt
import streamlit as st

matplotlib.use("Agg")

st.set_page_config(page_title="Grain Market Dashboard",
                   page_icon="🌽", layout="wide")

SYM = {"Corn": "ZC", "Soybeans": "ZS", "Soybean Meal": "ZM",
       "Soybean Oil": "ZL", "CBOT Wheat": "ZW", "KC Wheat": "KE"}
UNIT = {"ZC": "$/bu", "ZS": "$/bu", "ZM": "$/ton",
        "ZL": "$/lb", "ZW": "$/bu", "KE": "$/bu"}
# CFTC disaggregated COT has no KC wheat managed-money series
COT_COMMS = {"Corn": "CORN", "Soybeans": "SOYBEANS",
             "Soybean Meal": "SOYBEAN MEAL", "Soybean Oil": "SOYBEAN OIL",
             "CBOT Wheat": "WHEAT-SRW"}


def fmt_price(c, unit):
    return f"${c:,.2f}/{unit[2:]}" if unit == "$/ton" else f"${c:.2f}/{unit[2:]}"


@st.cache_data(ttl=3600)
def load_prices():
    conn = sqlite3.connect("data/prices.db")
    rows = conn.execute(
        "SELECT symbol, date, close, volume, unit FROM daily_prices ORDER BY date").fetchall()
    conn.close()
    data = {}
    for s, d, c, v, u in rows:
        data.setdefault(s, []).append(
            (datetime.strptime(d, "%Y-%m-%d").date(), c, v or 0, u or "$/bu"))
    return data


@st.cache_data(ttl=3600)
def load_cot():
    rows = []
    with open("data/cot_weekly.csv", newline="") as f:
        for r in csv.DictReader(f):
            rows.append((datetime.strptime(r["report_date"], "%Y-%m-%d").date(),
                         r["commodity"], int(r["mmoney_net"]),
                         float(r["futures_close"]) if r["futures_close"] else None))
    return rows


@st.cache_data(ttl=3600)
def load_curve():
    conn = sqlite3.connect("data/curve.db")
    rows = conn.execute(
        "SELECT commodity, contract, expiry, close, unit FROM forward_curve "
        "ORDER BY expiry").fetchall()
    conn.close()
    data = {}
    for comm, contract, expiry, c, u in rows:
        data.setdefault(comm, []).append((contract, expiry, c, u))
    return data


@st.cache_data(ttl=3600)
def load_alert_rules():
    rules, cur = [], None
    try:
        with open("data/alerts.yaml") as f:
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
    except FileNotFoundError:
        pass
    return rules


@st.cache_data(ttl=3600)
def load_wasde():
    with open("data/wasde_dates.csv", newline="") as f:
        return sorted(datetime.strptime(r["release_date"], "%Y-%m-%d").date()
                      for r in csv.DictReader(f))


@st.cache_data(ttl=3600)
def wasde_stats():
    """Recompute the event study live: avg abs daily move on WASDE vs normal days."""
    conn = sqlite3.connect("data/prices.db")
    wasde = set(load_wasde())
    out = {}
    for label, sym in SYM.items():
        rows = conn.execute(
            "SELECT date, close FROM daily_prices WHERE symbol=? ORDER BY date",
            (sym,)).fetchall()
        w_moves, n_moves, w_big, n_big = [], [], 0, 0
        for i in range(1, len(rows)):
            d = datetime.strptime(rows[i][0], "%Y-%m-%d").date()
            pct = abs(100 * (rows[i][1] - rows[i - 1][1]) / rows[i - 1][1])
            (w_moves if d in wasde else n_moves).append(pct)
            if pct > 2:
                if d in wasde:
                    w_big += 1
                else:
                    n_big += 1
        out[label] = {
            "wasde_days": len(w_moves), "normal_days": len(n_moves),
            "wasde_avg": sum(w_moves) / len(w_moves),
            "normal_avg": sum(n_moves) / len(n_moves),
            "wasde_big_pct": 100 * w_big / len(w_moves),
            "normal_big_pct": 100 * n_big / len(n_moves),
        }
    conn.close()
    return out


prices = load_prices()
curve = load_curve()
cot = load_cot()
wasde_dates = load_wasde()
today = date.today()

st.title("🌽 Grain Market Dashboard")
st.caption("Grain & oilseed futures — prices, forward curves, CFTC positioning, "
           "USDA WASDE effects. Data refreshes daily.")

# ---- headline metrics ----
latest = {}
for label, sym in SYM.items():
    d, c, _, u = prices[sym][-1]
    pd, pc, _, _ = prices[sym][-2]
    latest[label] = (d, c, 100 * (c - pc) / pc, u)

cot_latest = {}
for d_, comm, net, px in cot:
    cot_latest[comm] = (d_, net)
last_wasde = max(d for d in wasde_dates if d <= today)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Corn (ZC)", fmt_price(latest['Corn'][1], latest['Corn'][3]),
          f"{latest['Corn'][2]:+.2f}%", delta_color="normal")
c2.metric("Soybeans (ZS)", fmt_price(latest['Soybeans'][1], latest['Soybeans'][3]),
          f"{latest['Soybeans'][2]:+.2f}%", delta_color="normal")
c3.metric("Funds net long — corn", f"{cot_latest['CORN'][1] / 1000:,.0f}k contracts",
          f"COT {cot_latest['CORN'][0]}")
c4.metric("Funds net long — soybeans", f"{cot_latest['SOYBEANS'][1] / 1000:,.0f}k contracts",
          f"COT {cot_latest['SOYBEANS'][0]}")
c5.metric("Last WASDE", str(last_wasde), f"{(today - last_wasde).days} days ago")

m1, m2, m3, m4 = st.columns(4)
for col, label in zip((m1, m2, m3, m4),
                      ("Soybean Meal", "Soybean Oil", "CBOT Wheat", "KC Wheat")):
    d, c, chg, u = latest[label]
    col.metric(f"{label} ({SYM[label]})", fmt_price(c, u),
               f"{chg:+.2f}%", delta_color="normal")

tab_prices, tab_curve, tab_cot, tab_wasde, tab_alerts = st.tabs(
    ["📈 Prices", "📉 Forward Curve", "💰 Positioning", "📰 WASDE Reports", "🔔 Alerts"])

with tab_prices:
    comm_label = st.selectbox("Contract", list(SYM), key="px_comm")
    sym = SYM[comm_label]
    unit = UNIT[sym]
    series = prices[sym]
    mind, maxd = series[0][0], series[-1][0]
    start, end = st.slider("Date range", mind, maxd, (maxd.replace(year=maxd.year - 2), maxd),
                           key="px_range")
    filt = [(d, c) for d, c, v, u in series if start <= d <= end]
    closes = [c for _, c in filt]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot([d for d, _ in filt], closes, linewidth=1.2)
    ax.set_title(f"{comm_label} futures — daily close ({unit})")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    st.pyplot(fig)
    k1, k2, k3 = st.columns(3)
    k1.metric("Period high", fmt_price(max(closes), unit))
    k2.metric("Period low", fmt_price(min(closes), unit))
    k3.metric("Period average", fmt_price(sum(closes) / len(closes), unit))

with tab_curve:
    comm_label = st.selectbox("Contract", list(SYM), key="cv_comm")
    sym = SYM[comm_label]
    unit = UNIT[sym]
    rows = curve.get(sym, [])
    if not rows:
        st.info("Forward curve data not available yet — refreshes with the daily pipeline.")
    else:
        contracts = [r[0] for r in rows]
        closes = [r[2] for r in rows]
        spread = closes[-1] - closes[0]
        shape = "contango (deferred higher — market paying to store it)" if spread > 0 \
            else "backwardation (front higher — spot shortage bid)"
        st.subheader(f"{comm_label} forward curve — {shape}")
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(contracts, closes, marker="o", linewidth=1.5)
        ax.set_title(f"{comm_label} — price by contract month ({unit})")
        ax.grid(alpha=0.3)
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        fig.tight_layout()
        st.pyplot(fig)
        f1, f2 = st.columns(2)
        f1.metric("Front contract", f"{contracts[0]}  {fmt_price(closes[0], unit)}")
        f2.metric("Farthest contract", f"{contracts[-1]}  {fmt_price(closes[-1], unit)}",
                  f"{spread:+.2f} {unit} vs front")
        st.caption("Contango usually means comfortable supply; backwardation means "
                   "the market wants grain now — often bullish.")

with tab_cot:
    comm_label = st.selectbox("Contract", list(COT_COMMS), key="cot_comm")
    comm = COT_COMMS[comm_label]
    unit = UNIT[SYM[comm_label]]
    rows = [(d, n, p) for d, c, n, p in cot if c == comm]
    fig, ax1 = plt.subplots(figsize=(10, 4.5))
    ax1.bar([d for d, _, _ in rows], [n / 1000 for _, n, _ in rows],
            color="#1f77b4", alpha=0.55, width=6)
    ax1.axhline(0, color="black", linewidth=0.8)
    ax1.set_ylabel("Managed-money net (k contracts)")
    ax2 = ax1.twinx()
    ax2.plot([d for d, _, _ in rows], [p for _, _, p in rows],
             color="black", linewidth=1.2)
    ax2.set_ylabel(f"Futures price ({unit})")
    ax1.set_title(f"{comm_label} — fund positioning vs price (CFTC COT)")
    ax1.grid(alpha=0.25)
    fig.tight_layout()
    st.pyplot(fig)
    st.caption("Managed money = hedge funds. When bars are high, funds are crowded long — "
               "vulnerable to a squeeze if the market turns. "
               "KC wheat has no managed-money series in the CFTC disaggregated reports.")

with tab_wasde:
    stats = wasde_stats()
    st.subheader("Do prices jump on WASDE report days? Yes.")
    labels = list(stats)
    for i in range(0, len(labels), 3):
        cols = st.columns(3)
        for col, label in zip(cols, labels[i:i + 3]):
            s = stats[label]
            col.metric(f"{label} — avg daily move on WASDE days",
                       f"{s['wasde_avg']:.2f}%",
                       f"{s['wasde_avg'] / s['normal_avg']:.2f}x normal days")
            col.caption(f"Big moves (>2%): {s['wasde_big_pct']:.1f}% of WASDE days vs "
                        f"{s['normal_big_pct']:.1f}% normally · {s['wasde_days']} report days studied")
    fig, ax = plt.subplots(figsize=(9, 3.8))
    labels = list(stats)
    x = range(len(labels))
    w = 0.35
    ax.bar([i - w / 2 for i in x], [stats[l]["wasde_avg"] for l in labels],
           w, label="WASDE days", color="#d62728")
    ax.bar([i + w / 2 for i in x], [stats[l]["normal_avg"] for l in labels],
           w, label="Normal days", color="#7f7f7f")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Average absolute daily move (%)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    st.pyplot(fig)
    st.caption(f"WASDE is released monthly at 12:00 ET. Last release: {last_wasde}. "
               f"Full study: github.com/josea12345/wasde-report-day-study")

with tab_alerts:
    st.subheader("Price alert rules")
    st.caption("Checked every morning after the data refresh. "
               "Crossing alerts fire once per crossing; big-move alerts fire on the day.")
    rules = load_alert_rules()
    if not rules:
        st.info("No alert rules configured.")
    else:
        for rule in rules:
            sym = rule["symbol"]
            unit = UNIT.get(sym, "$/bu")
            d, c, _, _ = prices[sym][-1]
            pd, pc, _, _ = prices[sym][-2]
            move = 100 * (c - pc) / pc
            status = []
            if rule.get("above") and c >= rule["above"]:
                status.append(f"⚠️ above {fmt_price(rule['above'], unit)}")
            if rule.get("below") and c <= rule["below"]:
                status.append(f"⚠️ below {fmt_price(rule['below'], unit)}")
            if rule.get("daily_move_pct") and abs(move) >= rule["daily_move_pct"]:
                status.append(f"⚠️ moved {move:+.2f}% today")
            label = rule.get("name", rule["symbol"])
            with st.expander(f"{label}: {fmt_price(c, unit)} ({move:+.2f}% today)"
                             + (" — " + ", ".join(status) if status else " — watching ✅"),
                             expanded=bool(status)):
                a1, a2, a3 = st.columns(3)
                a1.metric("Alert if above", fmt_price(rule['above'], unit) if rule.get("above") else "—")
                a2.metric("Alert if below", fmt_price(rule['below'], unit) if rule.get("below") else "—")
                a3.metric("Alert on daily move", f"±{rule['daily_move_pct']:.1f}%" if rule.get("daily_move_pct") else "—")
        st.caption("Edit thresholds in data/alerts.yaml on GitHub — the next morning's check picks them up.")

st.divider()
st.caption("Sources: Yahoo Finance (futures), CFTC Commitments of Traders, USDA WASDE archive. "
           "Built by github.com/josea12345")
