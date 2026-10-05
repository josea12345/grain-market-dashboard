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
def load_crush():
    """Board crush margin history: 44 lbs meal + 11 lbs oil - 1 bushel soybeans.

    Returns [(date, crush_$/bu, oil_share), ...]. Computed live from prices.db.
    """
    conn = sqlite3.connect("data/prices.db")
    rows = conn.execute(
        "SELECT b.date, 44*(m.close/2000.0) + 11*o.close - b.close AS crush, "
        "11*o.close / (44*(m.close/2000.0) + 11*o.close) AS oil_share "
        "FROM (SELECT date, close FROM daily_prices WHERE symbol='ZS') b "
        "JOIN (SELECT date, close FROM daily_prices WHERE symbol='ZM') m "
        "  ON b.date = m.date "
        "JOIN (SELECT date, close FROM daily_prices WHERE symbol='ZL') o "
        "  ON b.date = o.date "
        "ORDER BY b.date").fetchall()
    conn.close()
    return [(datetime.strptime(d, "%Y-%m-%d").date(), c, s) for d, c, s in rows]


@st.cache_data(ttl=3600)
def cot_signals():
    """Crowded-trade radar: current managed-money net as a percentile of its
    own history, plus the 4-week change. Returns {commodity: {...}}."""
    from collections import defaultdict
    by_comm = defaultdict(list)
    for d, c, n, p in cot:
        by_comm[c].append((d, n))
    out = {}
    for comm, series in by_comm.items():
        series.sort()
        nets = [n for _, n in series]
        cur = nets[-1]
        pctile = 100 * sum(1 for v in nets if v <= cur) / len(nets)
        chg4 = cur - nets[-5] if len(nets) >= 5 else 0
        if pctile >= 90:
            sig, hint = "🔴 Extremely crowded LONG", "contrarian bearish"
        elif pctile >= 75:
            sig, hint = "🟠 Crowded long", "getting stretched"
        elif pctile <= 10:
            sig, hint = "🟢 Extremely crowded SHORT", "contrarian bullish"
        elif pctile <= 25:
            sig, hint = "🟡 Crowded short", "getting stretched"
        else:
            sig, hint = "⚪ Neutral", "no extreme"
        out[comm] = {"net": cur, "pctile": pctile, "chg4": chg4,
                     "date": series[-1][0], "signal": sig, "hint": hint}
    return out


@st.cache_data(ttl=3600)
def load_seasonality():
    """Seasonal path per commodity: each year rebased to 100 on its first
    trading day, then averaged by ISO week across all years (2010–present).
    Returns {symbol: {"avg": {week: v}, "lo": ..., "hi": ..., "cur": [(date, v)]}}.
    """
    out = {}
    for sym in SYM.values():
        by_year = {}
        for d, c, _, _ in prices[sym]:
            by_year.setdefault(d.year, []).append((d, c))
        weekly = {}
        for pts in by_year.values():
            pts.sort()
            base = pts[0][1]
            for d, c in pts:
                weekly.setdefault(d.isocalendar()[1], []).append(100 * c / base)
        cur_pts = sorted(by_year.get(today.year, []))
        cur_base = cur_pts[0][1] if cur_pts else 1
        out[sym] = {
            "avg": {w: sum(v) / len(v) for w, v in weekly.items()},
            "lo": {w: min(v) for w, v in weekly.items()},
            "hi": {w: max(v) for w, v in weekly.items()},
            "cur": [(d, 100 * c / cur_base) for d, c in cur_pts],
        }
    return out


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


today = date.today()

prices = load_prices()
curve = load_curve()
crush = load_crush()
seas = load_seasonality()
cot = load_cot()
wasde_dates = load_wasde()

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

tab_prices, tab_curve, tab_crush, tab_seas, tab_cot, tab_wasde, tab_alerts = st.tabs(
    ["📈 Prices", "📉 Forward Curve", "🫘 Crush Spread", "📅 Seasonality",
     "💰 Positioning", "📰 WASDE Reports", "🔔 Alerts"])

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

with tab_crush:
    st.subheader("Soybean crush spread — the crusher's margin")
    dates = [d for d, _, _ in crush]
    vals = [c for _, c, _ in crush]
    shares = [s for _, _, s in crush]
    cur, prev = vals[-1], vals[-2]
    avg_all = sum(vals) / len(vals)
    yr = vals[-252:] if len(vals) > 252 else vals
    avg_yr = sum(yr) / len(yr)
    pctile = 100 * sum(1 for v in vals if v <= cur) / len(vals)
    r1, r2, r3 = st.columns(3)
    r1.metric("Gross crush margin", f"${cur:.2f}/bu", f"{cur - prev:+.2f} vs prior day")
    r2.metric("1-year average", f"${avg_yr:.2f}/bu",
              f"{cur - avg_yr:+.2f} vs avg", delta_color="normal")
    r3.metric("Richness", f"{pctile:.0f}th percentile",
              "richer than this on few days" if pctile > 80 else
              "weaker than this on few days" if pctile < 20 else "mid-range vs history")
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(dates, vals, linewidth=1.1, label="Crush margin ($/bu)")
    ax.axhline(avg_all, color="red", linestyle="--", linewidth=1,
               label=f"All-time avg ${avg_all:.2f}")
    ax.set_title("Board crush: 44 lbs meal + 11 lbs oil − 1 bushel soybeans")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    st.pyplot(fig)
    fig2, ax2 = plt.subplots(figsize=(10, 2.6))
    ax2.plot(dates, [100 * s for s in shares], linewidth=1.1, color="green")
    ax2.set_title("Oil's share of total product value (%)")
    ax2.grid(alpha=0.3)
    fig2.tight_layout()
    st.pyplot(fig2)
    st.caption("The crush is what a soybean crusher earns turning beans into meal "
               "and oil. Wide crush → crushers run hard and buy more beans (supports "
               "soybean prices). Thin crush → plants slow down. Oil share shows which "
               "product is driving the margin — food vs fuel demand.")

with tab_seas:
    st.subheader("Seasonality — what the calendar usually does to prices")
    st.caption("Each year rebased to 100 on its first trading day, averaged by week "
               "across 2010–present. Shaded band = full historical range.")
    comm_label = st.selectbox("Contract", list(SYM), key="se_comm")
    sym = SYM[comm_label]
    s = seas[sym]
    weeks = sorted(s["avg"])
    avg = [s["avg"][w] for w in weeks]
    lo = [s["lo"][w] for w in weeks]
    hi = [s["hi"][w] for w in weeks]
    cur_w = [d.isocalendar()[1] for d, _ in s["cur"]]
    cur_v = [v for _, v in s["cur"]]
    this_wk = today.isocalendar()[1]
    wk_avg = s["avg"].get(this_wk)
    cur_now = cur_v[-1] if cur_v else None
    fwd_wk = min(this_wk + 8, max(weeks))
    drift = s["avg"].get(fwd_wk, wk_avg) - wk_avg if wk_avg else 0
    m1, m2, m3 = st.columns(3)
    m1.metric(f"{today.year} vs seasonal norm",
              f"{cur_now - wk_avg:+.1f} pts" if cur_now and wk_avg else "—",
              "above seasonal" if cur_now and wk_avg and cur_now > wk_avg else "below seasonal")
    m2.metric("Seasonal drift, next 8 weeks", f"{drift:+.1f} pts",
              "tends to firm" if drift > 0 else "tends to fade")
    m3.metric("Weeks of history", f"{len(weeks)}", f"{len(s['cur'])} trading days this year")
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.fill_between(weeks, lo, hi, alpha=0.18, color="gray", label="Historical range")
    ax.plot(weeks, avg, linewidth=1.6, label="Seasonal average")
    ax.plot(cur_w, cur_v, linewidth=2.2, color="#d62728", label=str(today.year))
    ax.axvline(this_wk, color="black", linestyle=":", linewidth=1)
    ax.set_title(f"{comm_label} — seasonal path (rebased to 100 each January)")
    ax.set_xlabel("Week of year")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    st.pyplot(fig)
    st.caption("Seasonality is tendency, not destiny — weather and demand write the "
               "actual story. Best used asking 'is this move normal for October?'")

with tab_cot:
    st.subheader("📡 Crowded-trade radar")
    st.caption("Where fund positioning sits vs its own history (2020–present). "
               "Extremes are contrarian signals — crowded longs are vulnerable to a washout.")
    signals = cot_signals()
    label_for = {v: k for k, v in COT_COMMS.items()}
    cols = st.columns(len(signals))
    for col, comm in zip(cols, COT_COMMS.values()):
        s = signals[comm]
        col.metric(label_for[comm],
                   f"{s['net'] / 1000:+,.0f}k net",
                   f"{s['pctile']:.0f}th percentile · {s['chg4'] / 1000:+,.0f}k in 4 wks")
        col.caption(f"{s['signal']} — {s['hint']}")
    st.caption(f"Latest COT report: {max(s['date'] for s in signals.values())}")
    st.divider()
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
            if sym == "CRUSH":
                d, c = crush[-1][0], crush[-1][1]
                pd, pc = crush[-2][0], crush[-2][1]
                unit = "$/bu"
            else:
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
