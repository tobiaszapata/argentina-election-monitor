"""Argentina 2027 election monitor (Streamlit).

Data sources
- Polymarket public API (live, cached a few minutes)
- Polls sheet: Google Sheets published as CSV (secret POLLS_CSV_URL) or data/polls/polls.csv
- ICG microdata (.dta) in data/icg/
- ICC workbook (.xls/.xlsx) in data/icc/
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from monitor import icc as icc_mod
from monitor import icg as icg_mod
from monitor import polls as polls_mod
from monitor import polymarket as pm
from monitor.utils import DATA_DIR, latest_file, to_excel

st.set_page_config(page_title="Argentina 2027 election monitor", page_icon="🗳️", layout="wide")

# ------------------------------------------------------------------ look and feel
INK = "#1B2A41"
GRID = "#E3E8EF"
MUTED = "#6B7689"
CANDIDATE_COLORS = {"milei": "#6D28D9", "kicillof": "#1E88E5"}
PALETTE = ["#1B2A41", "#4FA3D9", "#D99A00", "#C8553D", "#3F8F6B", "#8A94A6", "#0F766E", "#9D4EDD"]
ICG_COLOR, ICC_COLOR = "#1B2A41", "#D99A00"
PLOTLY_CONFIG = {"displaylogo": False,
                 "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]}

st.markdown("""
<style>
.block-container {max-width: 1180px; padding-top: 2.2rem;}
h1 {font-weight: 700; letter-spacing: -0.02em; margin-bottom: 0.2rem;}
h2 {font-weight: 650; letter-spacing: -0.01em; margin-top: 2.6rem; padding-top: 1.2rem;
    border-top: 2px solid #1B2A41;}
h3 {font-weight: 600; margin-top: 1.4rem;}
[data-testid="stCaptionContainer"] p {color: #5B6577;}
[data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;}
</style>
""", unsafe_allow_html=True)


def color_for(name: str, i: int) -> str:
    for key, c in CANDIDATE_COLORS.items():
        if key in name.lower() and not name.lower().startswith("karina"):
            return c
    return PALETTE[i % len(PALETTE)]


def base_layout(fig: go.Figure, y_title: str, height: int = 420, time_axis: bool = True,
                y_suffix: str = "") -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=30, b=10),
        font=dict(family="Inter, system-ui, -apple-system, Segoe UI, sans-serif", color=INK, size=13),
        plot_bgcolor="white", paper_bgcolor="white", hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=None),
        yaxis=dict(title=y_title, gridcolor=GRID, zeroline=False, ticksuffix=y_suffix),
        xaxis=dict(gridcolor=GRID, showline=True, linecolor="#B8C0CC"),
    )
    if time_axis:
        fig.update_xaxes(rangeselector=dict(
            buttons=[dict(count=6, label="6M", step="month", stepmode="backward"),
                     dict(count=1, label="1Y", step="year", stepmode="backward"),
                     dict(count=3, label="3Y", step="year", stepmode="backward"),
                     dict(step="all", label="All")],
            bgcolor="#F2F5F9", activecolor="#D5DEEA", y=1.12, x=1, xanchor="right"))
    return fig


def download(label: str, sheets: dict, info: dict, filename: str, key: str):
    st.download_button(f"Download {label} (Excel)", to_excel(sheets, info), file_name=filename,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key=key)


def source(text: str):
    st.caption(f"Source: {text}")


# ------------------------------------------------------------------ data loaders
@st.cache_data(show_spinner="Loading government confidence microdata...")
def load_icg(path: str, stamp: float):
    df = icg_mod.load(path)
    nat = icg_mod.national(df)
    parts = {d: icg_mod.breakdown(df, nat, d) for d in [*icg_mod.DIMENSIONS, "Components"]}
    return nat, parts


@st.cache_data(show_spinner="Loading consumer confidence series...")
def load_icc(path: str, stamp: float):
    return icc_mod.load(path)


@st.cache_data(ttl=300, show_spinner="Fetching Polymarket prices...")
def load_market_now():
    return pm.fetch_current()


@st.cache_data(ttl=1800, show_spinner="Fetching Polymarket price history...")
def load_market_history(current: pd.DataFrame):
    return pm.fetch_history(current)


@st.cache_data(ttl=600, show_spinner="Loading polls...")
def load_polls(url: str | None, local_stamp: float):
    if url:
        raw = pd.read_csv(url, dtype=str)
        origin = "Google Sheets"
    else:
        path = DATA_DIR / "polls" / "polls.csv"
        raw = pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame(columns=polls_mod.COLUMNS)
        origin = "data/polls/polls.csv"
    clean, problems = polls_mod.clean(raw)
    return clean, problems, origin


def _stamp(path):
    return path.stat().st_mtime if path else 0.0


icg_path, icg_warn = latest_file(
    "icg", ("*.dta",), content_key=lambda f: pd.read_stata(f, columns=["ola"])["ola"].max())
icc_path, icc_warn = latest_file("icc", ("*.xls", "*.xlsx"))
ICG = load_icg(str(icg_path), _stamp(icg_path)) if icg_path else None
ICC = load_icc(str(icc_path), _stamp(icc_path)) if icc_path else None

ICG_SOURCE = ("Government Confidence Index (ICG), Escuela de Gobierno, Universidad Torcuato Di Tella; "
              "fieldwork by Poliarquía Consultores. Computed from the microdata with the official weights.")
ICC_SOURCE = ("Consumer Confidence Index (ICC), Centro de Investigación en Finanzas, "
              "Universidad Torcuato Di Tella.")
PM_SOURCE = f"Polymarket, \"Argentina Presidential Election Winner\" market ({pm.EVENT_URL})."

# ------------------------------------------------------------------ header
st.title("Argentina 2027 election monitor")
st.markdown("What prediction markets, opinion polls and public-confidence surveys say about "
            "the October 2027 presidential election. Every chart can be downloaded as Excel.")

fresh = []
if ICG:
    fresh.append(f"Government confidence: {ICG[0]['date'].max():%B %Y}")
if ICC:
    fresh.append(f"Consumer confidence: {icc_mod.national(ICC)['date'].max():%B %Y}")
fresh.append("Prediction markets: refreshed every 5 minutes")
st.caption("Latest data. " + "; ".join(fresh) + ".")
for w in (icg_warn, icc_warn):
    if w:
        st.warning(w)

with st.sidebar:
    st.markdown("### Contents")
    st.markdown("""
- [Prediction markets](#prediction-markets)
- [Polls](#polls)
- [Government vs. consumer confidence](#government-vs-consumer-confidence)
- [Government confidence by presidency](#government-confidence-by-presidency)
- [Government confidence breakdown](#government-confidence-breakdown)
- [Consumer confidence breakdown](#consumer-confidence-breakdown)
""")

# ================================================================== 1. PREDICTION MARKETS
st.header("Prediction markets", anchor="prediction-markets")
st.markdown(
    "Polymarket lets people bet on who will win the presidency, including a possible runoff. "
    "A price of 54 cents on a candidate means the market gives them a 54% chance. Prices across "
    "candidates add up to slightly more than 100%, so they are rescaled to sum to 100%. "
    "Trading volume in this market is small, so a single large bet can move prices.")

market_now, market_hist, market_error = None, None, None
try:
    market_now = load_market_now()
    market_hist = load_market_history(market_now)
except Exception as e:  # network or API change: keep the rest of the page working
    market_error = str(e)

if market_error or market_now is None or market_now.empty:
    st.warning("Polymarket could not be reached right now, so this section is empty. "
               "It will load again on the next refresh. "
               f"Details: {market_error or 'no open markets returned.'}")
else:
    cands = market_now["candidate"].tolist()
    norm = pm.normalize(market_hist) if market_hist is not None and not market_hist.empty else pd.DataFrame()

    def find(*keys):
        for k in keys:
            for c in cands:
                if k in c.lower():
                    return c
        return None

    milei, kicillof = find("javier milei", "milei"), find("kicillof")
    prob = market_now.set_index("candidate")["probability"]

    def change_7d(c):
        if norm.empty or c not in norm or len(norm) < 8:
            return None
        return prob[c] - norm[c].iloc[-8]

    def market_chart(series: dict, title_key: str):
        fig = go.Figure()
        for i, (name, s) in enumerate(series.items()):
            fig.add_scatter(x=s.index, y=s.values, name=name, mode="lines",
                            line=dict(color=color_for(name, i), width=2.6),
                            hovertemplate="%{y:.1f}%")
        base_layout(fig, "Probability of winning", y_suffix="%")
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key=title_key)

    pm_info = {"Source": PM_SOURCE,
               "Price": "Mid-point between best bid and best ask when the spread is 10 cents or less; "
                        "otherwise last traded price.",
               "Probability": "Prices rescaled so that all candidates sum to 100% each day.",
               "Retrieved": f"{market_now.attrs['fetched_at']:%Y-%m-%d %H:%M} UTC"}

    @st.fragment
    def section_head_to_head():
        st.subheader("Milei vs. Kicillof")
        if not (milei and kicillof):
            st.info("The market does not list both candidates right now.")
            return
        c1, c2, c3 = st.columns(3)
        for col, c in ((c1, milei), (c2, kicillof)):
            d = change_7d(c)
            col.metric(c, f"{prob[c]:.1f}%", None if d is None else f"{d:+.1f} pts in 7 days")
        c3.metric("Raw sum of all prices", f"{market_now.attrs['raw_sum'] * 100:.1f}%",
                  help="Above 100% is normal: it is the market's built-in margin.")
        st.caption("Chance of each one becoming president, from the winner market. "
                   "Polymarket has no specific Milei vs. Kicillof runoff market.")
        if not norm.empty:
            market_chart({c: norm[c] for c in (milei, kicillof) if c in norm}, "pm_h2h")
        source(PM_SOURCE)
        download("Milei vs. Kicillof", {
            "Daily probability": norm[[c for c in (milei, kicillof) if c in norm]].reset_index()
            if not norm.empty else pd.DataFrame(),
            "Current": market_now[market_now["candidate"].isin([milei, kicillof])].drop(columns="token_yes").copy(),
        }, pm_info, "polymarket_milei_vs_kicillof.xlsx", "dl_pm_h2h")

    @st.fragment
    def section_top4():
        st.subheader("Top four candidates")
        top = cands[:4]
        cols = st.columns(4)
        for col, c in zip(cols, top):
            d = change_7d(c)
            col.metric(c, f"{prob[c]:.1f}%", None if d is None else f"{d:+.1f} pts in 7 days")
        if not norm.empty:
            market_chart({c: norm[c] for c in top if c in norm}, "pm_top4")
        source(PM_SOURCE)
        download("top four candidates", {
            "Daily probability": norm[[c for c in top if c in norm]].reset_index() if not norm.empty else pd.DataFrame(),
            "All candidates now": market_now.drop(columns="token_yes"),
        }, pm_info, "polymarket_top4.xlsx", "dl_pm_top4")

    @st.fragment
    def section_matchup():
        st.subheader("Build your own matchup")
        st.caption("Pick the candidates on each side. Each line adds up the chances of everyone on "
                   "that side, e.g. Milei against all Peronist candidates combined.")
        peronists = pm.default_peronists(cands) or ([kicillof] if kicillof else cands[1:2])
        a_col, b_col = st.columns(2)
        side_a = a_col.multiselect("Side A", cands, default=[milei] if milei else cands[:1], key="side_a")
        side_b = b_col.multiselect("Side B", [c for c in cands if c not in side_a],
                                   default=[c for c in peronists if c not in side_a], key="side_b")
        name_a = a_col.text_input("Label for side A", "Side A" if len(side_a) != 1 else side_a[0], key="lab_a")
        default_b = side_b[0] if len(side_b) == 1 else (
            "Peronist candidates" if side_b and set(side_b) <= set(peronists) else "Side B")
        name_b = b_col.text_input("Label for side B", default_b, key="lab_b")
        if not side_a or not side_b:
            st.info("Choose at least one candidate on each side.")
            return
        rest = [c for c in cands if c not in side_a + side_b]
        m1, m2, m3 = st.columns(3)
        m1.metric(name_a, f"{prob[side_a].sum():.1f}%")
        m2.metric(name_b, f"{prob[side_b].sum():.1f}%")
        m3.metric("Everyone else", f"{prob[rest].sum():.1f}%")
        if not norm.empty:
            pick = lambda group: norm[[c for c in group if c in norm]].sum(axis=1, min_count=1)
            series = {name_a: pick(side_a), name_b: pick(side_b)}
            if rest:
                series["Everyone else"] = pick(rest)
            market_chart(series, "pm_matchup")
            table = pd.DataFrame(series).reset_index()
        else:
            table = pd.DataFrame()
        source(PM_SOURCE)
        download("this matchup", {
            "Daily probability": table,
            "Sides": pd.DataFrame({"side": [name_a] * len(side_a) + [name_b] * len(side_b) + ["Everyone else"] * len(rest),
                                   "candidate": side_a + side_b + rest}),
        }, pm_info, "polymarket_matchup.xlsx", "dl_pm_matchup")

    section_head_to_head()
    section_top4()
    section_matchup()
    with st.expander("Market depth by candidate"):
        depth = market_now.drop(columns="token_yes").copy()
        depth.attrs = {}
        depth["spread"] = depth["best_ask"] - depth["best_bid"]
        st.dataframe(depth, hide_index=True, width="stretch", column_config={
            "probability": st.column_config.NumberColumn("Probability", format="%.1f%%"),
            "price": st.column_config.NumberColumn("Price (USD)", format="%.3f"),
            "volume_usd": st.column_config.NumberColumn("Volume traded (USD)", format="%,.0f"),
            "liquidity_usd": st.column_config.NumberColumn("Liquidity (USD)", format="%,.0f"),
        })
        st.caption("A wide spread between best bid and best ask, or low liquidity, means the price "
                   "is less reliable.")

# ================================================================== 2. POLLS
st.header("Polls", anchor="polls")
try:
    polls_url = st.secrets.get("POLLS_CSV_URL") or None
except Exception:  # no secrets configured (e.g. running locally)
    polls_url = None
try:
    POLLS, poll_problems, poll_origin = load_polls(polls_url, _stamp(DATA_DIR / "polls" / "polls.csv"))
except Exception as e:
    POLLS, poll_problems, poll_origin = pd.DataFrame(), [f"Could not read the polls sheet: {e}"], ""

for p in poll_problems:
    st.warning(p)

if POLLS is None or POLLS.empty:
    st.info("No polls loaded yet. Add rows to the polls sheet (one row per poll and candidate) "
            "and they will appear here on the next refresh. Expected columns: "
            + ", ".join(polls_mod.COLUMNS) + ".")
else:
    @st.fragment
    def section_polls():
        scenarios = POLLS["scenario"].value_counts().index.tolist()
        c1, c2 = st.columns([2, 1])
        scenario = c1.selectbox("Question", scenarios, key="poll_scenario")
        basis = c2.radio("Show as", ["% of all respondents", "% of valid votes"], key="poll_basis",
                         help="Valid votes leaves out undecided and blank answers and rescales the "
                              "rest to 100%, like an election result.")
        d = POLLS[POLLS["scenario"] == scenario]
        pollsters = sorted(d["pollster"].unique())
        chosen = st.multiselect("Pollsters", pollsters, default=pollsters, key="poll_firms")
        d = d[d["pollster"].isin(chosen)]
        if d.empty:
            st.info("Select at least one pollster.")
            return
        if basis == "% of valid votes":
            d = polls_mod.to_valid_votes(d)
        tr = polls_mod.trend(d)
        mo = polls_mod.monthly(d)
        cand = d[d["kind"] == "Candidate"]
        order = cand.groupby("candidate")["value"].mean().sort_values(ascending=False).index.tolist()

        fig = go.Figure()
        for i, c in enumerate(order):
            col = color_for(c, i)
            g = cand[cand["candidate"] == c]
            fig.add_scatter(
                x=g["date"], y=g["value"], mode="markers", name=c, legendgroup=c, showlegend=False,
                marker=dict(color=col, size=9, opacity=0.35, line=dict(width=0)),
                customdata=g[["pollster", "sample_size"]].fillna("n/a").values,
                hovertemplate="%{customdata[0]}: %{y:.1f}% (n=%{customdata[1]})<extra>" + c + "</extra>")
            t = tr[tr["candidate"] == c]
            if not t.empty:
                fig.add_scatter(x=pd.concat([t["date"], t["date"][::-1]]),
                                y=pd.concat([t["high"], t["low"][::-1]]), fill="toself",
                                fillcolor=col, opacity=0.12, line=dict(width=0), hoverinfo="skip",
                                legendgroup=c, showlegend=False)
                fig.add_scatter(x=t["date"], y=t["trend"], mode="lines", name=c, legendgroup=c,
                                line=dict(color=col, width=3), hovertemplate="trend %{y:.1f}%<extra>" + c + "</extra>")
        base_layout(fig, "Vote intention", height=460, y_suffix="%")
        fig.update_layout(hovermode="closest")
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key="polls_chart")
        st.caption("Each faint dot is one poll; the line is a weighted average of nearby polls "
                   "(closer dates, larger samples and pollsters that publish less often weigh more), "
                   "with a shaded 95% band. The last weeks of the line move as new polls arrive.")

        st.markdown("**Monthly average**")
        wide = mo[mo["candidate"].isin(order)].pivot(index="month", columns="candidate", values="value")[order]
        counts = mo.groupby("month")[["pollsters", "polls"]].max()
        show = wide.join(counts).sort_index(ascending=False).reset_index()
        show["month"] = show["month"].dt.strftime("%b %Y")
        st.dataframe(show, hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.1f%%") for c in order})
        st.caption("Each pollster counts once per month (its polls that month are averaged), "
                   "weighted by the square root of its sample size. When a pollster does not report "
                   "its sample size, the median of the other polls is used.")
        source("compiled from the pollsters listed; links in the downloadable file.")
        download("polls for this question", {
            "Polls": d.drop(columns=["poll_id"]).sort_values("date"),
            "Trend (daily)": tr,
            "Monthly average": mo,
        }, {"Question": scenario, "Shown as": basis, "Data from": poll_origin,
            "Trend": f"Gaussian kernel, sd {polls_mod.BANDWIDTH_DAYS} days; weights sqrt(n) / polls by "
                     f"the same pollster within {polls_mod.POLLSTER_WINDOW_DAYS} days."},
            "polls.xlsx", "dl_polls")

    section_polls()

# ================================================================== 3. ICG vs ICC
st.header("Government vs. consumer confidence", anchor="government-vs-consumer-confidence")
if ICG and ICC:
    @st.fragment
    def section_icg_icc():
        st.markdown("Confidence in the government (ICG) next to consumer confidence about the economy "
                    "(ICC), both surveyed monthly by Universidad Torcuato Di Tella.")
        mode = st.radio("Scale", ["Both on 0-100 (ICG x 20)", "Index, December 2023 = 100"],
                        horizontal=True, key="cmp_mode")
        a = ICG[0][["date", "ICG"]]
        b = icc_mod.national(ICC)
        m = a.merge(b, on="date", how="inner")
        m["ICG x 20"] = m["ICG"] * 20
        if mode.startswith("Both"):
            ya, yb, title = m["ICG x 20"], m["ICC"], "Index (0-100)"
            note = ("Both indices on the same 0-100 range. Compare how they move rather than their "
                    "levels: the two surveys ask different questions.")
        else:
            base = m[m["date"] == "2023-12-01"]
            if base.empty:
                base = m.iloc[[0]]
            m["ICG (Dec 2023 = 100)"] = m["ICG"] / base["ICG"].iloc[0] * 100
            m["ICC (Dec 2023 = 100)"] = m["ICC"] / base["ICC"].iloc[0] * 100
            ya, yb, title = m["ICG (Dec 2023 = 100)"], m["ICC (Dec 2023 = 100)"], "Index (Dec 2023 = 100)"
            note = "Each index relative to its own value in December 2023, the month Milei took office."
        fig = go.Figure()
        fig.add_scatter(x=m["date"], y=ya, name="Government confidence (ICG)", mode="lines",
                        line=dict(color=ICG_COLOR, width=2.4), hovertemplate="%{y:.1f}")
        fig.add_scatter(x=m["date"], y=yb, name="Consumer confidence (ICC)", mode="lines",
                        line=dict(color=ICC_COLOR, width=2.4), hovertemplate="%{y:.1f}")
        base_layout(fig, title)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key="cmp_chart")
        st.caption(note)
        source(f"{ICG_SOURCE} {ICC_SOURCE}")
        download("both series", {"ICG vs ICC": m}, {"ICG": ICG_SOURCE, "ICC": ICC_SOURCE,
                 "Note": "ICG ranges 0-5; 'ICG x 20' puts it on 0-100."}, "icg_vs_icc.xlsx", "dl_cmp")

    section_icg_icc()
else:
    st.info("Add the ICG microdata to data/icg and the ICC workbook to data/icc to see this chart.")

# ================================================================== 4. ICG BY PRESIDENCY
st.header("Government confidence by presidency", anchor="government-confidence-by-presidency")
if ICG:
    nat = ICG[0]
    pres = icg_mod.by_presidency(nat)
    shown = [c for c in pres.columns if c not in ("month_in_office", "De la Rúa")]
    st.markdown("Each line follows one president's government confidence from their first month in "
                "office, so administrations can be compared at the same point of their term.")
    fig = go.Figure()
    for i, c in enumerate(shown):
        is_current = c == shown[-1]
        s = pres[["month_in_office", c]].dropna()
        fig.add_scatter(x=s["month_in_office"], y=s[c], name=c, mode="lines",
                        line=dict(color="#6D28D9" if is_current else PALETTE[i % len(PALETTE)],
                                  width=3.4 if is_current else 1.6),
                        opacity=1 if is_current else 0.75, hovertemplate="%{y:.2f}")
    base_layout(fig, "ICG (0-5)", height=460, time_axis=False)
    fig.update_xaxes(title="Month in office")
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key="pres_chart")
    last = nat.iloc[-1]
    same = pres[pres["month_in_office"] == last["month_in_office"]].drop(columns="month_in_office").T.dropna()
    st.caption(f"Month {int(last['month_in_office'])} of the current term ({last['date']:%B %Y}): "
               + "; ".join(f"{k} {v.iloc[0]:.2f}" for k, v in same.iterrows()) + ". "
               "De la Rúa is left out: the series starts in his 23rd month.")
    source(ICG_SOURCE)
    summary = nat.groupby("president").agg(first_month=("date", "min"), last_month=("date", "max"),
                                           months=("date", "size"), average=("ICG", "mean")).reset_index()
    download("ICG by presidency", {"By month in office": pres, "Summary": summary,
                                   "Monthly national ICG": nat},
             {"Source": ICG_SOURCE, "Month in office": "Month 1 is the first survey after "
              "inauguration (fieldwork runs early in the month)."}, "icg_by_presidency.xlsx", "dl_pres")

# ================================================================== 5. ICG BREAKDOWN
st.header("Government confidence breakdown", anchor="government-confidence-breakdown")
if ICG:
    @st.fragment
    def section_icg_breakdown():
        dims = list(icg_mod.DIMENSIONS) + ["Components"]
        c1, c2 = st.columns([2, 1])
        dim = c1.selectbox("Break down by", dims, index=0, key="icg_dim")
        smooth = c2.toggle("3-month moving average", value=False, key="icg_smooth",
                           help="Averages each month with the two before it. Recommended for small "
                                "groups such as primary education or ages 18-29.")
        t = ICG[1][dim].copy()
        wide = t.pivot(index="date", columns="category", values="ICG")
        wide.columns = wide.columns.astype(str)
        plot = wide.rolling(3, min_periods=3).mean() if smooth else wide
        fig = go.Figure()
        for i, c in enumerate(icg_mod.category_order(dim)):
            if c in plot:
                fig.add_scatter(x=plot.index, y=plot[c], name=c, mode="lines",
                                line=dict(color=PALETTE[i % len(PALETTE)], width=2.2),
                                hovertemplate="%{y:.2f}")
        if dim != "Components":
            nat = ICG[0].set_index("date")["ICG"]
            nat = nat.rolling(3, min_periods=3).mean() if smooth else nat
            fig.add_scatter(x=nat.index, y=nat, name="National", mode="lines",
                            line=dict(color="#9AA3B2", width=1.6, dash="dot"), hovertemplate="%{y:.2f}")
        base_layout(fig, "ICG (0-5)", height=440)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key="icg_bd_chart")
        if dim != "Components":
            latest = t[t["date"] == t["date"].max()]
            st.caption(f"Respondents in {t['date'].max():%B %Y}: "
                       + "; ".join(f"{r.category} {int(r.n)}" for r in latest.itertuples())
                       + ". Groups with fewer than about 300 respondents move a lot from month to "
                         "month by chance alone.")
        else:
            st.caption("The ICG is the average of these five components.")
        source(ICG_SOURCE)
        n_wide = t.pivot(index="date", columns="category", values="n")
        n_wide.columns = [f"n {c}" for c in n_wide.columns]
        download(f"ICG by {dim.lower()}", {
            f"By {dim}"[:31]: wide.reset_index(),
            "Long format": t,
            **({"Sample sizes": n_wide.reset_index()} if dim != "Components" else {}),
        }, {"Source": ICG_SOURCE, "Breakdown": dim,
            "Confidence interval": "ci_low / ci_high: approximate 95% interval (Kish effective sample size)."},
            f"icg_{dim.lower().replace(' ', '_')}.xlsx", "dl_icg_bd")

    section_icg_breakdown()

# ================================================================== 6. ICC BREAKDOWN
st.header("Consumer confidence breakdown", anchor="consumer-confidence-breakdown")
if ICC:
    @st.fragment
    def section_icc_breakdown():
        dims = icc_mod.dimensions(ICC)
        dim = st.selectbox("Break down by", dims, index=0, key="icc_dim")
        t = icc_mod.breakdown(ICC, dim)
        wide = t.pivot(index="date", columns="category", values="ICC")
        wide.columns = wide.columns.astype(str)
        nat = icc_mod.national(ICC).set_index("date")["ICC"]
        fig = go.Figure()
        for i, c in enumerate(wide.columns):
            fig.add_scatter(x=wide.index, y=wide[c], name=c, mode="lines",
                            line=dict(color=PALETTE[i % len(PALETTE)], width=2.2), hovertemplate="%{y:.1f}")
        fig.add_scatter(x=nat.index, y=nat, name="National", mode="lines",
                        line=dict(color="#9AA3B2", width=1.6, dash="dot"), hovertemplate="%{y:.1f}")
        base_layout(fig, "ICC (0-100)", height=440)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key="icc_bd_chart")
        if dim == "Components":
            st.caption("Personal situation, the macroeconomic situation and buying conditions for "
                       "durable goods and real estate are the three components of the ICC.")
        source(ICC_SOURCE)
        out = wide.join(nat.rename("National")).reset_index()
        download(f"ICC by {dim.lower()}", {f"By {dim}": out},
                 {"Source": ICC_SOURCE, "Breakdown": dim}, f"icc_{dim.lower()}.xlsx", "dl_icc_bd")

    section_icc_breakdown()
elif not icc_path:
    st.info("Add the ICC workbook (Serie_ICC_MM-YY.xls) to data/icc to see this section.")

st.divider()
st.caption("Built with public data. Figures are recomputed whenever a new file is uploaded; "
           "prediction market prices refresh every 5 minutes.")
