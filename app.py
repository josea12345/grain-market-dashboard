"""Grain Market Dashboard — CBOT corn & soybeans.

Live view of futures prices, CFTC positioning, and USDA WASDE report effects.
Data refreshes daily via the pipeline/ scripts + GitHub Actions.
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

SYM = {"Corn": "ZC", "Soybeans": "ZS"}
COMM = {"ZC": "CORN", "ZS": "SOYBEANS"}


@st.cache_data(ttl=3600)
def load_prices():
    conn = sqlite3.connect("data/prices.db")
    rows = conn.execute(
        "SELECT symbol, date, close, volume FROM daily_prices ORDER BY date").fetchall()
    conn.close()
    data = {}
    for s, d, c, v in rows:
        data.setdefault(s, []).append(
            (datetime.strptime(d, "%Y-%m-%d").date(), c / 100, v or 0))
    return data


@st.cache_data(ttl=3600)
def load_cot():
    rows = []
    with open("data/cot_weekly.csv", newline="") as f:
        for r in csv.DictReader(f):
            rows.append((datetime.strptime(r["report_date"], "%Y-%m-%d").date(),
                         r["commodity"], int(r["mmoney_net"]),
                         float(r["futures_close"]) / 100 if r["futures_close"] else None))
    return rows


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
cot = load_cot()
wasde_dates = load_wasde()
today = date.today()

st.title("🌽 Grain Market Dashboard")
st.caption("CBOT corn & soybean futures — prices, CFTC positioning, USDA WASDE effects. "
           "Data refreshes daily.")

# ---- headline metrics ----
latest = {}
for label, sym in SYM.items():
    d, c, _ = prices[sym][-1]
    pd, pc, _ = prices[sym][-2]
    latest[label] = (d, c, 100 * (c - pc) / pc)

cot_latest = {}
for d_, comm, net, px in cot:
    cot_latest[comm] = (d_, net)
last_wasde = max(d for d in wasde_dates if d <= today)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Corn (ZC)", f"${latest['Corn'][1]:.2f}/bu",
          f"{latest['Corn'][2]:+.2f}%", delta_color="normal")
c2.metric("Soybeans (ZS)", f"${latest['Soybeans'][1]:.2f}/bu",
          f"{latest['Soybeans'][2]:+.2f}%", delta_color="normal")
c3.metric("Funds net long — corn", f"{cot_latest['CORN'][1] / 1000:,.0f}k contracts",
          f"COT {cot_latest['CORN'][0]}")
c4.metric("Funds net long — soybeans", f"{cot_latest['SOYBEANS'][1] / 1000:,.0f}k contracts",
          f"COT {cot_latest['SOYBEANS'][0]}")
c5.metric("Last WASDE", str(last_wasde), f"{(today - last_wasde).days} days ago")

tab_prices, tab_cot, tab_wasde = st.tabs(["📈 Prices", "💰 Positioning", "📰 WASDE Reports"])

with tab_prices:
    comm_label = st.selectbox("Contract", list(SYM), key="px_comm")
    sym = SYM[comm_label]
    series = prices[sym]
    mind, maxd = series[0][0], series[-1][0]
    start, end = st.slider("Date range", mind, maxd, (maxd.replace(year=maxd.year - 2), maxd),
                           key="px_range")
    filt = [(d, c) for d, c, v in series if start <= d <= end]
    closes = [c for _, c in filt]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot([d for d, _ in filt], closes, linewidth=1.2)
    ax.set_title(f"{comm_label} futures — daily close ($/bushel)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    st.pyplot(fig)
    k1, k2, k3 = st.columns(3)
    k1.metric("Period high", f"${max(closes):.2f}")
    k2.metric("Period low", f"${min(closes):.2f}")
    k3.metric("Period average", f"${sum(closes) / len(closes):.2f}")

with tab_cot:
    comm_label = st.selectbox("Contract", list(SYM), key="cot_comm")
    comm = COMM[SYM[comm_label]]
    rows = [(d, n, p) for d, c, n, p in cot if c == comm]
    fig, ax1 = plt.subplots(figsize=(10, 4.5))
    ax1.bar([d for d, _, _ in rows], [n / 1000 for _, n, _ in rows],
            color="#1f77b4", alpha=0.55, width=6)
    ax1.axhline(0, color="black", linewidth=0.8)
    ax1.set_ylabel("Managed-money net (k contracts)")
    ax2 = ax1.twinx()
    ax2.plot([d for d, _, _ in rows], [p for _, _, p in rows],
             color="black", linewidth=1.2)
    ax2.set_ylabel("Futures price ($/bu)")
    ax1.set_title(f"{comm_label} — fund positioning vs price (CFTC COT)")
    ax1.grid(alpha=0.25)
    fig.tight_layout()
    st.pyplot(fig)
    st.caption("Managed money = hedge funds. When bars are high, funds are crowded long — "
               "vulnerable to a squeeze if the market turns.")

with tab_wasde:
    stats = wasde_stats()
    st.subheader("Do prices jump on WASDE report days? Yes.")
    w1, w2 = st.columns(2)
    for col, label in zip((w1, w2), ("Corn", "Soybeans")):
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

st.divider()
st.caption("Sources: Yahoo Finance (futures), CFTC Commitments of Traders, USDA WASDE archive. "
           "Built by github.com/josea12345")
