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

# ---------- professional styling ----------
st.markdown("""
<style>
.hero { background: linear-gradient(135deg, #1B4332 0%, #2D6A4F 100%);
        border-radius: 14px; padding: 24px 28px; margin-bottom: 20px; }
.hero-title { color: #FFFFFF; font-size: 30px; font-weight: 700; letter-spacing: -0.5px; }
.hero-sub { color: #D8F3DC; font-size: 15px; margin-top: 6px; }
.hero-meta { color: #B7E4C7; font-size: 12.5px; margin-top: 10px; }
[data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #E9E7E0;
    border-radius: 12px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
[data-testid="stMetricLabel"] { font-weight: 600; }
.stTabs [data-baseweb="tab-list"] { gap: 2px; }
.stTabs [data-baseweb="tab"] { font-size: 15px; font-weight: 600; padding: 10px 16px; }
.lede-card { background: #EFF6F0; border-left: 4px solid #2D6A4F; border-radius: 0 12px 12px 0;
    padding: 14px 18px; font-size: 16px; margin: 4px 0 18px 0; }
.brief-card { background: #FFFFFF; border: 1px solid #E9E7E0; border-radius: 12px;
    padding: 14px 18px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
.brief-card .btitle { font-weight: 700; font-size: 15px; margin-bottom: 6px; }
.brief-card ul { margin: 0; padding-left: 18px; }
.brief-card li { margin-bottom: 4px; font-size: 14.5px; }
.section-head { font-size: 19px; font-weight: 700; margin: 6px 0 4px 0; }
.section-sub { color: #5A5A5A; font-size: 13.5px; margin-bottom: 12px; }
</style>
""", unsafe_allow_html=True)

# chart polish applied to every figure
plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": "#D8D5CC",
    "axes.labelcolor": "#333333",
    "xtick.color": "#555555",
    "ytick.color": "#555555",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.22,
    "grid.color": "#B9B5A9",
})

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


def ordinal(n):
    n = int(round(n))
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


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
def wasde_drift_study():
    """Do funds systematically trade around WASDE reports? Three tests across
    all releases and COT markets. Returns per-commodity dicts + verdict."""
    import math
    from collections import defaultdict
    by_comm = defaultdict(list)
    for d, c, n, p in cot:
        by_comm[c].append((d, n))
    for c in by_comm:
        by_comm[c].sort()
    sym_of = {"CORN": "ZC", "SOYBEANS": "ZS", "SOYBEAN MEAL": "ZM",
              "SOYBEAN OIL": "ZL", "WHEAT-SRW": "ZW"}
    pxd = {}
    for sym in set(sym_of.values()):
        pxd[sym] = {d: c for d, c, _, _ in prices[sym]}

    def rep_ret(sym, w):
        days = pxd[sym]
        ds = sorted(days)
        b = [d for d in ds if d < w]
        a = [d for d in ds if d >= w]
        if not b or not a:
            return None
        return 100 * (days[a[0]] - days[b[-1]]) / days[b[-1]]

    out = {}
    for comm, sym in sym_of.items():
        s = by_comm[comm]
        nets = [n for _, n in s]
        ins, outs, vol_ext, vol_mid = [], [], [], []
        for w in wasde_dates:
            bef = [i for i, (d, _) in enumerate(s) if d < w]
            aft = [i for i, (d, _) in enumerate(s) if d >= w]
            if len(bef) >= 3:
                i = bef[-1]
                ins.append((s[i][1] - s[i - 2][1]) / 1000.0)
                pct = sum(1 for v in nets if v <= s[i][1]) / len(nets)
                r = rep_ret(sym, w)
                if r is not None:
                    (vol_ext if (pct >= 0.9 or pct <= 0.1) else
                     vol_mid if 0.25 <= pct <= 0.75 else []).append(abs(r))
            if len(aft) >= 3:
                j = aft[0]
                outs.append((s[j + 2][1] - s[j][1]) / 1000.0)
        n = len(outs)
        m = sum(outs) / n if n else 0
        sd = math.sqrt(sum((x - m) ** 2 for x in outs) / (n - 1)) if n > 1 else 0
        t = m / (sd / math.sqrt(n)) if sd else 0
        out[comm] = {
            "n": len(ins),
            "pct_cut": 100 * sum(1 for x in ins if x < 0) / len(ins) if ins else 0,
            "avg_in": sum(ins) / len(ins) if ins else 0,
            "t_out": t,
            "vol_ratio": ((sum(vol_ext) / len(vol_ext)) / (sum(vol_mid) / len(vol_mid))
                          if vol_ext and vol_mid else 1.0),
        }
    return out


def build_brief():
    """Auto-generated market brief: one synthesis of every dataset in the app.
    Returns (lede, [(section_title, [bullets])])."""
    label_for = {v: k for k, v in SYM.items()}
    sections = []

    # ---- prices ----
    moves = []
    for label, sym in SYM.items():
        d, c, _, u = prices[sym][-1]
        _, pc, _, _ = prices[sym][-2]
        moves.append((label, 100 * (c - pc) / pc, c, u, d))
    moves.sort(key=lambda m: abs(m[1]), reverse=True)
    big = moves[0]
    up = sum(1 for m in moves if m[1] > 0)
    pb = [f"Biggest mover: {big[0]} {big[1]:+.2f}% to {fmt_price(big[2], big[3])}."]
    pb.append(f"{up} of {len(moves)} commodities higher on the day "
              f"({', '.join(f'{m[0]} {m[1]:+.1f}%' for m in moves[1:3])}).")
    sections.append(("📈 Prices", pb))

    # ---- crush ----
    cv, pv = crush[-1][1], crush[-2][1]
    allv = [c for _, c, _ in crush]
    cpct = 100 * sum(1 for v in allv if v <= cv) / len(allv)
    sections.append(("🫘 Crush", [
        f"Board crush ${cv:.2f}/bu ({cv - pv:+.2f} on the day) — "
        f"{ordinal(cpct)} percentile vs history, "
        f"{'rich' if cpct >= 75 else 'soft' if cpct <= 25 else 'mid-range'}.",
    ]))

    # ---- positioning ----
    sigs = cot_signals()
    extremes = [(c, s) for c, s in sigs.items()
                if s["pctile"] >= 90 or s["pctile"] <= 10]
    inv = {v: k for k, v in COT_COMMS.items()}
    if extremes:
        eb = [f"{inv[c]}: funds {ordinal(s["pctile"])}-percentile "
              f"{'long' if s['pctile'] >= 90 else 'short'} "
              f"({s['net'] / 1000:+,.0f}k net) — crowded."
              for c, s in extremes]
    else:
        eb = ["No positioning extremes right now — no market above the 90th "
              "or below the 10th percentile."]
    sections.append(("📡 Positioning", eb))

    # ---- seasonality ----
    wk = today.isocalendar()[1]
    diffs = []
    for label, sym in SYM.items():
        s = seas[sym]
        if s["cur"] and wk in s["avg"]:
            diffs.append((label, s["cur"][-1][1] - s["avg"][wk]))
    diffs.sort(key=lambda x: x[1], reverse=True)
    sb = [f"2026 is running {'hot' if diffs[0][1] > 0 else 'cold'} vs seasonal norms — "
          f"hottest: {diffs[0][0]} ({diffs[0][1]:+.0f} pts), "
          f"coolest: {diffs[-1][0]} ({diffs[-1][1]:+.0f} pts)."]
    sections.append(("📅 Seasonality", sb))

    # ---- curves ----
    cb = []
    for label, sym in SYM.items():
        rows = curve.get(sym, [])
        if len(rows) >= 2:
            sp = rows[-1][2] - rows[0][2]
            cb.append(f"{label}: {'contango' if sp > 0 else 'backwardation'} "
                      f"({sp:+.2f} {UNIT[sym]} front→deferred)")
    if cb:
        sections.append(("📉 Curves", cb))

    # ---- wasde ----
    last_w = max(d for d in wasde_dates if d <= today)
    stt = wasde_stats()
    wb = [f"{(today - last_w).days} days since the last WASDE ({last_w}). "
          f"Report days average {stt['Corn']['wasde_avg']:.2f}% daily moves in corn — "
          f"plan around the next one (monthly, usually around the 10th)."]
    sections.append(("📰 WASDE", wb))

    # ---- alerts ----
    rules = load_alert_rules()
    sections.append(("🔔 Alerts",
                     [f"{len(rules)} rules watching. Details on the Alerts tab."]))

    # ---- lede: the single most notable thing ----
    notes = []
    if extremes:
        worst = max(extremes, key=lambda e: abs(e[1]["pctile"] - 50))
        notes.append(f"funds are at extreme {ordinal(worst[1]["pctile"])}-percentile "
                     f"{'long' if worst[1]['pctile'] >= 90 else 'short'} "
                     f"{inv[worst[0]].lower()}")
    if abs(big[1]) >= 2:
        notes.append(f"{big[0].lower()} moved {big[1]:+.1f}%")
    if cpct >= 90 or cpct <= 10:
        notes.append(f"crush margin is at its {ordinal(cpct)} percentile (${cv:.2f}/bu)")
    lede = ("Today's tape: " + "; ".join(notes) + "." if notes
            else "Quiet tape — no extremes standing out across prices, positioning, or crush.")
    return lede, sections


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

latest_date = max(d for sym in prices for d, _, _, _ in prices[sym])
st.markdown(f"""
<div class="hero">
  <div class="hero-title">🌽 Grain Market Dashboard</div>
  <div class="hero-sub">Grain &amp; oilseed futures — prices, forward curves, crush spreads,
  fund positioning, WASDE effects, alerts</div>
  <div class="hero-meta">Data as of {latest_date.strftime('%B %d, %Y')} · Refreshes every morning</div>
</div>
""", unsafe_allow_html=True)

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

tab_brief, tab_prices, tab_curve, tab_crush, tab_seas, tab_cot, tab_wasde, tab_alerts = st.tabs(
    ["🌅 Brief", "📈 Prices", "📉 Forward Curve", "🫘 Crush Spread", "📅 Seasonality",
     "💰 Positioning", "📰 WASDE Reports", "🔔 Alerts"])

with tab_brief:
    lede, brief_sections = build_brief()
    st.markdown(f"<div class='section-head'>Market brief — "
                f"{today.strftime('%A, %B %d, %Y')}</div>",
                unsafe_allow_html=True)
    st.markdown(f"<div class='lede-card'>{lede}</div>", unsafe_allow_html=True)
    cards = "".join(
        f"<div class='brief-card'><div class='btitle'>{title}</div><ul>"
        + "".join(f"<li>{b}</li>" for b in bullets)
        + "</ul></div>"
        for title, bullets in brief_sections)
    st.markdown(cards, unsafe_allow_html=True)

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
    st.divider()
    st.markdown("<div class='section-head'>🌾 Storage breakeven — does the carry pay you to store?</div>"
                "<div class='section-sub'>The market pays carry (deferred minus front) to whoever stores grain. "
                "This weighs it against your cost of carry: interest on the grain's value plus physical storage. "
                "Positive = storing earns · Negative = sell now.</div>",
                unsafe_allow_html=True)
    s_comm = st.selectbox("Contract", ["Corn", "Soybeans", "CBOT Wheat", "KC Wheat"],
                          key="st_comm")
    s_sym = SYM[s_comm]
    s_rows = curve.get(s_sym, [])
    if len(s_rows) < 2:
        st.info("Not enough curve data for this commodity yet.")
    else:
        r1, r2 = st.columns(2)
        rate = r1.slider("Interest rate (annual %)", 0.0, 15.0, 8.0, 0.25, key="st_rate") / 100
        stor = r2.slider("Storage cost ($/bu/month)", 0.0, 0.20, 0.05, 0.01, key="st_stor")
        front_px = s_rows[0][2]
        front_dt = datetime.strptime(s_rows[0][1], "%Y-%m-%d").date()
        results = []
        for contract, expiry, px, u in s_rows[1:]:
            edt = datetime.strptime(expiry, "%Y-%m-%d").date()
            months = (edt.year - front_dt.year) * 12 + (edt.month - front_dt.month)
            if months <= 0:
                continue
            carry = px - front_px
            int_cost = front_px * rate * (months * 30.44 / 365)
            stor_cost = stor * months
            results.append((contract, months, carry, int_cost, stor_cost,
                            carry - int_cost - stor_cost))
        if results:
            best = max(results, key=lambda r: r[5])
            v1, v2 = st.columns(2)
            v1.metric("Best month to store into",
                      f"{best[0]} ({best[1]} mo)",
                      f"{best[5]:+.2f} $/bu net of carry costs")
            v2.metric("Market carry to that month",
                      f"{best[2]:+.2f} $/bu",
                      f"costs: {best[3] + best[4]:.2f} $/bu (interest + storage)",
                      delta_color="off")
            fig, ax = plt.subplots(figsize=(10, 3.6))
            labels = [r[0] for r in results]
            nets = [r[5] for r in results]
            colors = ["#2D6A4F" if n >= 0 else "#C0392B" for n in nets]
            ax.bar(labels, nets, color=colors, alpha=0.85)
            ax.axhline(0, color="black", linewidth=0.9)
            ax.set_title(f"{s_comm}: net return to storage by contract "
                         f"(@ {rate * 100:.2f}% interest, ${stor:.2f}/bu/mo)")
            ax.set_ylabel("Net $/bu")
            ax.grid(axis="y", alpha=0.25)
            fig.tight_layout()
            st.pyplot(fig)
            st.caption("Full carry belongs to whoever can store cheapest — on-farm storage "
                       "often beats commercial rates, which is why elevators watch this spread.")

with tab_crush:
    st.markdown("<div class='section-head'>Soybean crush spread — the crusher's margin</div>", unsafe_allow_html=True)
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
    r3.metric("Richness", f"{ordinal(pctile)} percentile",
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
    st.markdown("<div class='section-head'>Seasonality — what the calendar usually does to prices</div>"
                "<div class='section-sub'>Each year rebased to 100 on its first trading day, averaged by week "
                "across 2010–present. Shaded band = full historical range.</div>", unsafe_allow_html=True)
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
    st.markdown("<div class='section-head'>📡 Crowded-trade radar</div>"
                "<div class='section-sub'>Where fund positioning sits vs its own history (2020–present). "
                "Extremes are contrarian signals — crowded longs are vulnerable to a washout.</div>", unsafe_allow_html=True)
    signals = cot_signals()
    label_for = {v: k for k, v in COT_COMMS.items()}
    cols = st.columns(len(signals))
    for col, comm in zip(cols, COT_COMMS.values()):
        s = signals[comm]
        col.metric(label_for[comm],
                   f"{s['net'] / 1000:+,.0f}k net",
                   f"{ordinal(s["pctile"])} percentile · {s["chg4"] / 1000:+,.0f}k in 4 wks")
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
    st.markdown("<div class='section-head'>Do prices jump on WASDE report days? Yes.</div>", unsafe_allow_html=True)
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
    st.divider()
    st.markdown("<div class='section-head'>Do funds front-run WASDE reports? We checked.</div>"
                "<div class='section-sub'>Three tests across 80 releases and 5 markets, 2020–present. "
                "Spoiler: the data says no.</div>", unsafe_allow_html=True)
    drift = wasde_drift_study()
    inv_cot = {v: k for k, v in COT_COMMS.items()}
    d1, d2, d3 = st.columns(3)
    avg_cut = sum(v["pct_cut"] for v in drift.values()) / len(drift)
    d1.metric("Test 1 — cut risk into the report?",
              f"{avg_cut:.0f}% of the time",
              "a coin flip — no systematic de-risking", delta_color="off")
    max_t = max(abs(v["t_out"]) for v in drift.values())
    d2.metric("Test 2 — chase the report after?",
              f"t = {max_t:.2f}",
              "not significant in any market (|t| < 2)", delta_color="off")
    avg_vr = sum(v["vol_ratio"] for v in drift.values()) / len(drift)
    d3.metric("Test 3 — crowded books, bigger moves?",
              f"{avg_vr:.2f}x",
              "report-day moves look the same either way", delta_color="off")
    st.markdown("<div class='lede-card'>Verdict: <b>no detectable pattern.</b> Funds don't "
                "systematically position for WASDE — the report itself moves the market, "
                "not the setup into it. A null result is still a result.</div>",
                unsafe_allow_html=True)
    with st.expander("See the numbers by market"):
        for comm, v in drift.items():
            st.write(f"• **{inv_cot[comm]}** — cut into report {v['pct_cut']:.0f}% of releases "
                     f"(n={v['n']}), post-report drift t={v['t_out']:+.2f}, "
                     f"extreme-vs-neutral move ratio {v['vol_ratio']:.2f}x")

with tab_alerts:
    st.markdown("<div class='section-head'>Price alert rules</div>"
                "<div class='section-sub'>Checked every morning after the data refresh. "
                "Crossing alerts fire once per crossing; big-move alerts fire on the day.</div>", unsafe_allow_html=True)
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
