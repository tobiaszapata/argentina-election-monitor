"""Argentina 2027 election monitor (Streamlit).

Data sources
- Polymarket public API (live, cached a few minutes)
- Polls sheet: Google Sheets published as CSV (secret POLLS_CSV_URL) or data/polls/polls.csv
- ICG microdata (.dta) in data/icg/
- ICC workbook (.xls/.xlsx) in data/icc/
"""
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Make the project folder importable, whether the helper modules sit in monitor/
# or were uploaded loose next to app.py.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    from monitor import icc as icc_mod
    from monitor import icg as icg_mod
    from monitor import polls as polls_mod
    from monitor import polymarket as pm
    from monitor import kalshi as ks
    from monitor.utils import find_file, latest_file, to_excel
except ModuleNotFoundError:
    try:
        import icc as icc_mod
        import icg as icg_mod
        import polls as polls_mod
        import polymarket as pm
        import kalshi as ks
        from utils import find_file, latest_file, to_excel
    except ModuleNotFoundError:
        st.error("The helper files are missing. The repository needs a folder called `monitor` "
                 "(next to app.py) containing icc.py, icg.py, kalshi.py, polls.py, polymarket.py and utils.py.")
        st.stop()

st.set_page_config(page_title="Argentina 2027 election monitor", page_icon="🗳️", layout="wide")

# ------------------------------------------------------------------ look and feel
INK = "#1B2A41"
GRID = "#E3E8EF"
MUTED = "#6B7689"
CANDIDATE_COLORS = {"milei": "#6D28D9", "kicillof": "#1E88E5"}
PALETTE = ["#1B2A41", "#4FA3D9", "#D99A00", "#C8553D", "#3F8F6B", "#8A94A6", "#0F766E", "#9D4EDD"]
ICG_COLOR, ICC_COLOR = "#1B2A41", "#D99A00"
# Charts are static (no drag-zoom); the period buttons above each chart set the range.
PLOTLY_CONFIG = {"displaylogo": False, "scrollZoom": False, "modeBarButtons": [["toImage"]],
                 "toImageButtonOptions": {"format": "png", "scale": 2}}


def _dark_theme() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


DARK = _dark_theme()
if DARK:  # light text and lighter first color so lines and labels stay readable
    INK, GRID, MUTED = "#E3E8EF", "#2B3544", "#9AA3B2"
    PALETTE[0] = "#AFC2DD"
    ICG_COLOR = "#AFC2DD"
AXIS_LINE = "#4A5568" if DARK else "#B8C0CC"

# Period buttons: label -> days back from the last data point ("term" = since Milei took office).
PERIODS = {
    "daily": [("All", None), ("6 months", 183), ("3 months", 92), ("1 month", 31),
              ("15 days", 15), ("7 days", 7)],
    "monthly": [("All", None), ("10 years", 3653), ("5 years", 1827), ("Current term", "term"),
                ("1 year", 366)],
    "polls": [("All", None), ("1 year", 366), ("6 months", 183), ("3 months", 92)],
}


def period_picker(key: str, kind: str, default: str, container=None):
    labels = [label for label, _ in PERIODS[kind]]
    box = container or st
    choice = box.segmented_control("Period", labels, default=default, key=key) or default
    return dict(PERIODS[kind])[choice]


def apply_period(fig: go.Figure, period):
    """Keep only points from the chosen period, so the y-axis fits what is shown."""
    if period is None:
        return
    xs = [pd.to_datetime(pd.Series(t.x)) for t in fig.data if t.x is not None and len(t.x)]
    if not xs:
        return
    end = max(x.max() for x in xs)
    start = pd.Timestamp("2023-12-01") if period == "term" else end - pd.Timedelta(days=period)
    for t in fig.data:
        if t.x is None or not len(t.x):
            continue
        keep = (pd.to_datetime(pd.Series(t.x)) >= start).to_numpy()
        update = {"x": np.asarray(t.x, dtype=object)[keep]}
        for attr in ("y", "customdata"):
            v = getattr(t, attr)
            if v is not None and len(v) == len(keep):
                update[attr] = np.asarray(v, dtype=object)[keep]
        t.update(**update)

st.markdown("""
<style>
.block-container {max-width: 1180px; padding-top: 2.2rem;}
h1 {font-weight: 700; letter-spacing: -0.02em; margin-bottom: 0.2rem;}
h2 {font-weight: 650; letter-spacing: -0.01em; margin-top: 2.6rem; padding-top: 1.2rem;
    border-top: 2px solid rgba(128, 128, 128, 0.45);}
h3 {font-weight: 600; margin-top: 1.4rem;}
[data-testid="stCaptionContainer"] p {color: #5B6577;}
[data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;}
</style>
""", unsafe_allow_html=True)


PARTY_COLORS = [("lla", "#6D28D9"), ("libertad", "#6D28D9"), ("peron", "#1E88E5"),
                ("fuerza patria", "#1E88E5"), ("provincias", "#D99A00"), ("fit", "#B42318"),
                ("left", "#B42318"), ("izquierda", "#B42318"), ("ucr", "#C8553D"),
                ("pro", "#E8B400"), ("other", "#8A94A6"), ("otros", "#8A94A6"),
                ("blank", "#B8C0CC"), ("undecided", "#D5DBE3")]


def party_color(name: str, i: int) -> str:
    n = name.lower()
    for key, c in PARTY_COLORS:
        if re.search(rf"\b{re.escape(key)}", n):
            return c
    return PALETTE[i % len(PALETTE)]


def color_for(name: str, i: int) -> str:
    for key, c in CANDIDATE_COLORS.items():
        if key in name.lower() and not name.lower().startswith("karina"):
            return c
    return PALETTE[i % len(PALETTE)]


def base_layout(fig: go.Figure, y_title: str, height: int = 420, time_axis: bool = True,
                y_suffix: str = "", period=None) -> go.Figure:
    if time_axis:
        apply_period(fig, period)
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=20, b=10),
        font=dict(family="Inter, system-ui, -apple-system, Segoe UI, sans-serif", color=INK, size=13),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", hovermode="x unified",
        dragmode=False,
        hoverlabel=dict(bgcolor="#1F2733" if DARK else "white", font=dict(color=INK)),
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="left", x=0, title=None,
                    font=dict(color=INK)),
        yaxis=dict(title=y_title, gridcolor=GRID, zeroline=False, ticksuffix=y_suffix, fixedrange=True,
                   color=INK),
        xaxis=dict(gridcolor=GRID, showline=True, linecolor=AXIS_LINE, fixedrange=True, color=INK),
    )
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


@st.cache_data(ttl=600, show_spinner="Loading government confidence sheet...")
def load_icg_sheet(url: str):
    return icg_mod.from_sheet(pd.read_csv(url, dtype=str))


@st.cache_data(ttl=600, show_spinner="Loading consumer confidence sheet...")
def load_icc_sheet(url: str):
    return icc_mod.from_sheet(pd.read_csv(url, dtype=str))


@st.cache_data(show_spinner="Loading consumer confidence series...")
def load_icc(path: str, stamp: float):
    return icc_mod.load(path)


VENUES = {"Polymarket": pm, "Kalshi": ks}
VENUE_DASH = {"Polymarket": "solid", "Kalshi": "dash"}


@st.cache_data(ttl=300, show_spinner="Fetching prediction market prices...")
def load_venue_now(venue: str):
    return VENUES[venue].fetch_current()


@st.cache_data(ttl=1800, show_spinner="Fetching prediction market price history...")
def load_venue_history(venue: str, current: pd.DataFrame):
    return VENUES[venue].fetch_history(current)


@st.cache_data(ttl=600, show_spinner="Loading polls...")
def load_polls(polls_url: str | None, spaces_url: str | None, stamp: float):
    if polls_url:
        raw = pd.read_csv(polls_url, dtype=str)
        origin = "Google Sheets"
    else:
        raw = pd.read_csv(POLLS_FILE, dtype=str) if POLLS_FILE else pd.DataFrame(columns=polls_mod.COLUMNS)
        origin = POLLS_FILE.name if POLLS_FILE else "no polls file"
    raw_spaces = None
    if spaces_url:
        raw_spaces = pd.read_csv(spaces_url, dtype=str)
    elif SPACES_FILE:
        raw_spaces = pd.read_csv(SPACES_FILE, dtype=str)
    rows, spaces, problems = polls_mod.clean(raw, raw_spaces)
    if not spaces_url and not SPACES_FILE:
        problems = problems + ([] if rows.empty else [
            "The spaces sheet is not connected (secret SPACES_CSV_URL), so the default "
            "space-to-bloc list is being used."])
    return rows, spaces, problems, origin


def _stamp(path):
    return path.stat().st_mtime if path else 0.0


try:
    ICG_URL = st.secrets.get("ICG_CSV_URL") or None
    ICC_URL = st.secrets.get("ICC_CSV_URL") or None
except Exception:  # no secrets configured (e.g. running locally)
    ICG_URL = ICC_URL = None

POLLS_FILE = find_file("polls", "polls.csv")
SPACES_FILE = find_file("polls", "spaces.csv")
# ICG: the monthly Google Sheet when it is connected; otherwise the microdata (.dta) file.
ICG, ICG_FROM_SHEET, icg_warn, icg_problems = None, False, None, []
if ICG_URL:
    try:
        _nat, _parts, icg_problems = load_icg_sheet(ICG_URL)
        ICG, ICG_FROM_SHEET = (_nat, _parts), True
    except Exception as e:
        icg_problems = [f"Could not read the ICG sheet ({e}). Using the microdata file instead, if there is one."]
if ICG is None:
    icg_path, icg_warn = latest_file(
        "icg", ("*.dta",), root_patterns=("*.dta",),
        content_key=lambda f: pd.read_stata(f, columns=["ola"])["ola"].max())
    ICG = load_icg(str(icg_path), _stamp(icg_path)) if icg_path else None
# ICC: the monthly Google Sheet when it is connected; otherwise UTDT's workbook (.xls).
ICC, icc_warn, icc_problems = None, None, []
if ICC_URL:
    try:
        ICC, icc_problems = load_icc_sheet(ICC_URL)
    except Exception as e:
        icc_problems = [f"Could not read the ICC sheet ({e}). Using the ICC workbook instead, if there is one."]
icc_path = None
if ICC is None:
    icc_path, icc_warn = latest_file("icc", ("*.xls", "*.xlsx"), root_patterns=("*ICC*.xls", "*ICC*.xlsx"))
    ICC = load_icc(str(icc_path), _stamp(icc_path)) if icc_path else None

ICG_SOURCE = ("Government Confidence Index (ICG), Escuela de Gobierno, Universidad Torcuato Di Tella; "
              "fieldwork by Poliarquía Consultores. "
              + ("Values from UTDT's monthly reports; history computed from the microdata with the official weights."
                 if ICG_FROM_SHEET else "Computed from the microdata with the official weights."))
ICC_SOURCE = ("Consumer Confidence Index (ICC), Centro de Investigación en Finanzas, "
              "Universidad Torcuato Di Tella.")
PM_SOURCE = f"Polymarket, \"Argentina Presidential Election Winner\" market ({pm.EVENT_URL})."
KS_SOURCE = f"Kalshi, \"Who will win the next Argentine presidential election?\" market ({ks.EVENT_URL})."
VENUE_SOURCE = {"Polymarket": PM_SOURCE, "Kalshi": KS_SOURCE}

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
for w in (icg_warn, icc_warn, *icg_problems, *icc_problems):
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
    "Polymarket and Kalshi let people bet on who will win the presidency, including a possible "
    "runoff. A price of 54 cents on a candidate means that market gives them a 54% chance. Prices "
    "across candidates add up to slightly more or less than 100%, so each market is rescaled to sum "
    "to 100%. Both markets are small, so a single large bet can move prices: check the depth table "
    "at the end of this section. Each chart lets you pick one market or both.")


def canon(name: str) -> str:
    """Matching key for the same candidate across markets: no accents, lower case."""
    n = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", re.sub(r"\s+", " ", n.lower())).strip()


MARKETS, DISPLAY = {}, {}
for venue in VENUES:
    entry = {"now": None, "norm": pd.DataFrame(), "error": None}
    try:
        now = load_venue_now(venue).copy()
        attrs = dict(now.attrs)
        for c in now["candidate"]:
            DISPLAY.setdefault(canon(c), c)
        now["candidate"] = now["candidate"].map(lambda c: DISPLAY[canon(c)])
        now.attrs = attrs
        entry["now"] = now
        try:
            hist = load_venue_history(venue, load_venue_now(venue))
            if hist is not None and not hist.empty:
                hist = hist.rename(columns=lambda c: DISPLAY.get(canon(c), c))
                hist = hist.T.groupby(level=0).sum(min_count=1).T  # merge duplicated names
                entry["norm"] = pm.normalize(hist)
        except Exception as e:
            entry["error"] = f"price history unavailable ({e})"
    except Exception as e:  # network or API change: keep the rest of the page working
        entry["error"] = str(e)
    MARKETS[venue] = entry

AVAILABLE = [v for v, e in MARKETS.items() if e["now"] is not None and not e["now"].empty]
for v, e in MARKETS.items():
    if v not in AVAILABLE:
        st.warning(f"{v} could not be reached right now, so it is left out of this section. "
                   f"It will load again on the next refresh. Details: {e['error'] or 'no open markets returned.'}")
    elif e["error"]:
        st.warning(f"{v}: {e['error']}. Current prices are shown; the charts may be incomplete.")


def prob(v: str) -> pd.Series:
    return MARKETS[v]["now"].set_index("candidate")["probability"]


def change_7d(v: str, c: str):
    norm = MARKETS[v]["norm"]
    if norm.empty or c not in norm or len(norm) < 8 or c not in prob(v):
        return None
    past = norm[c].iloc[-8]
    return None if pd.isna(past) else prob(v)[c] - past


def candidates(venues: list) -> list:
    """Union of candidates in the chosen markets, by average probability."""
    p = pd.concat([prob(v) for v in venues], axis=1).mean(axis=1)
    return p.sort_values(ascending=False).index.tolist()


def find(keys, venues):
    for k in keys:
        for c in candidates(venues):
            if k in canon(c):
                return c
    return None


def source_picker(key: str, container=None) -> list:
    options = AVAILABLE + (["Both"] if len(AVAILABLE) > 1 else [])
    choice = (container or st).radio("Market", options, index=len(options) - 1, horizontal=True, key=key,
                      help="Polymarket lines are solid and Kalshi lines are dashed.")
    return AVAILABLE if choice == "Both" else [choice]


def line_name(label: str, v: str, venues: list) -> str:
    return f"{label} ({v})" if len(venues) > 1 else label


def market_controls(key: str):
    """Market and period selectors side by side, above the chart."""
    c1, c2 = st.columns([1, 1.6])
    venues = source_picker(f"src_{key}", c1)
    period = period_picker(f"per_{key}", "daily", "3 months", c2)
    return venues, period


def market_chart(lines: list, key: str, period=None, height: int = 420, y_title="Probability of winning"):
    """lines: (name, series, color, dash)."""
    fig = go.Figure()
    for name, s, color, dash in lines:
        s = s.dropna()
        fig.add_scatter(x=s.index, y=s.values, name=name, mode="lines",
                        line=dict(color=color, width=2.6, dash=dash), hovertemplate="%{y:.1f}%")
    base_layout(fig, y_title, height=height, y_suffix="%", period=period)
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, theme=None, key=key)


def venue_info(venues: list) -> dict:
    info = {f"Source ({v})": VENUE_SOURCE[v] for v in venues}
    info["Price"] = ("Mid-point between best bid and best ask when the spread is 10 cents or less; "
                     "otherwise last traded price.")
    info["Probability"] = "Prices rescaled so that each market's candidates sum to 100% each day."
    info["Retrieved"] = " / ".join(f"{v} {MARKETS[v]['now'].attrs['fetched_at']:%Y-%m-%d %H:%M} UTC"
                                   for v in venues)
    return info


def history_sheet(venues: list, group: list) -> pd.DataFrame:
    parts = []
    for v in venues:
        n = MARKETS[v]["norm"]
        cols = [c for c in group if c in n]
        if cols:
            parts.append(n[cols].rename(columns=lambda c: f"{c} ({v})"))
    return pd.concat(parts, axis=1).reset_index() if parts else pd.DataFrame()


def comparison_table(cands: list, venues: list) -> pd.DataFrame:
    t = pd.DataFrame({v: prob(v).reindex(cands) for v in venues})
    t.index.name = "Candidate"
    if len(venues) > 1:
        t["Gap (Kalshi - Polymarket, pts)"] = t["Kalshi"] - t["Polymarket"]
    return t.reset_index()


def show_comparison(t: pd.DataFrame):
    st.dataframe(t, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.1f%%") for c in ("Polymarket", "Kalshi")} | {
        "Gap (Kalshi - Polymarket, pts)": st.column_config.NumberColumn(format="%+.1f")})


def current_sheets(venues: list, cands: list | None = None) -> dict:
    out = {}
    for v in venues:
        now = MARKETS[v]["now"]
        now = now if cands is None else now[now["candidate"].isin(cands)]
        out[f"Current {v}"] = now.drop(columns=[c for c in ("token_yes", "open_time") if c in now]).copy()
    return out


if not AVAILABLE:
    st.info("No prediction market could be reached right now.")
else:
    @st.fragment
    def section_head_to_head():
        st.subheader("Milei vs. Kicillof")
        venues, period = market_controls("h2h")
        milei, kicillof = find(["javier milei"], venues), find(["kicillof"], venues)
        pair = [c for c in (milei, kicillof) if c]
        if len(pair) < 2:
            st.info("The selected market does not list both candidates right now.")
            return
        cols = st.columns(len(pair) * len(venues))
        i = 0
        for c in pair:
            for v in venues:
                p = prob(v).get(c)
                d = change_7d(v, c)
                cols[i].metric(line_name(c, v, venues), "n/a" if p is None else f"{p:.1f}%",
                               None if d is None else f"{d:+.1f} pts in 7 days")
                i += 1
        st.caption("Chance of each one becoming president, from each market's winner contract. "
                   "Neither market has a specific Milei vs. Kicillof runoff contract.")
        lines = [(line_name(c, v, venues), MARKETS[v]["norm"][c], color_for(c, j), VENUE_DASH[v])
                 for v in venues for j, c in enumerate(pair) if c in MARKETS[v]["norm"]]
        if lines:
            market_chart(lines, "pm_h2h", period)
        gap = None
        if len(venues) > 1:
            n_pm, n_ks = MARKETS["Polymarket"]["norm"], MARKETS["Kalshi"]["norm"]
            gap = pd.DataFrame({c: n_ks[c] - n_pm[c] for c in pair if c in n_pm and c in n_ks}).dropna(how="all")
            if not gap.empty:
                st.markdown("**Gap between markets** (Kalshi minus Polymarket, percentage points)")
                fig = go.Figure()
                for j, c in enumerate(gap.columns):
                    fig.add_scatter(x=gap.index, y=gap[c], name=c, mode="lines",
                                    line=dict(color=color_for(c, j), width=2.2), hovertemplate="%{y:+.1f} pts")
                fig.add_hline(y=0, line=dict(color="#9AA3B2", width=1))
                base_layout(fig, "Kalshi - Polymarket (pts)", height=280, period=period)
                st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, theme=None, key="pm_gap")
                st.caption("Above zero, Kalshi gives the candidate a higher chance than Polymarket.")
        for v in venues:
            source(VENUE_SOURCE[v])
        sheets = {"Daily probability": history_sheet(venues, pair)}
        if gap is not None and not gap.empty:
            sheets["Gap Kalshi-Polymarket"] = gap.reset_index()
        sheets.update(current_sheets(venues, pair))
        download("Milei vs. Kicillof", sheets, venue_info(venues), "markets_milei_vs_kicillof.xlsx", "dl_pm_h2h")

    @st.fragment
    def section_top4():
        st.subheader("Top four candidates")
        venues, period = market_controls("top4")
        top = candidates(venues)[:4]
        if len(venues) == 1:
            cols = st.columns(len(top))
            for col, c in zip(cols, top):
                d = change_7d(venues[0], c)
                col.metric(c, f"{prob(venues[0])[c]:.1f}%", None if d is None else f"{d:+.1f} pts in 7 days")
        else:
            show_comparison(comparison_table(top, venues))
        lines = [(line_name(c, v, venues), MARKETS[v]["norm"][c], color_for(c, j), VENUE_DASH[v])
                 for v in venues for j, c in enumerate(top) if c in MARKETS[v]["norm"]]
        if lines:
            market_chart(lines, "pm_top4", period)
        if len(venues) > 1:
            st.caption("Ranked by the average of both markets. A candidate listed in only one market "
                       "shows a blank for the other.")
        for v in venues:
            source(VENUE_SOURCE[v])
        sheets = {"Daily probability": history_sheet(venues, top)}
        if len(venues) > 1:
            sheets["Comparison now"] = comparison_table(candidates(venues), venues)
        sheets.update(current_sheets(venues))
        download("top four candidates", sheets, venue_info(venues), "markets_top4.xlsx", "dl_pm_top4")

    @st.fragment
    def section_matchup():
        st.subheader("Build your own matchup")
        st.caption("Pick the candidates on each side. Each line adds up the chances of everyone on "
                   "that side, e.g. Milei against all Peronist candidates combined.")
        venues, period = market_controls("matchup")
        cands = candidates(venues)
        milei = find(["javier milei"], venues)
        peronists = pm.default_peronists(cands) or cands[1:2]
        a_col, b_col = st.columns(2)
        side_a = a_col.multiselect("Side A", cands, default=[c for c in [milei or cands[0]] if c in cands],
                                   key="side_a")
        side_b = b_col.multiselect("Side B", [c for c in cands if c not in side_a],
                                   default=[c for c in peronists if c not in side_a], key="side_b")
        default_b = side_b[0] if len(side_b) == 1 else (
            "Peronist candidates" if side_b and set(side_b) <= set(peronists) else "Side B")
        name_a = a_col.text_input("Label for side A", "Side A" if len(side_a) != 1 else side_a[0], key="lab_a")
        name_b = b_col.text_input("Label for side B", default_b, key="lab_b")
        if not side_a or not side_b:
            st.info("Choose at least one candidate on each side.")
            return
        groups = {name_a: side_a, name_b: side_b}
        now_rows, lines, hist = [], [], {}
        for v in venues:
            p = prob(v)
            rest = [c for c in p.index if c not in side_a + side_b]
            g = dict(groups, **({"Everyone else": rest} if rest else {}))
            now_rows.append({"Market": v, **{k: p.reindex(m).sum() for k, m in g.items()}})
            norm = MARKETS[v]["norm"]
            for j, (k, m) in enumerate(g.items()):
                cols = [c for c in m if c in norm]
                if cols:
                    s = norm[cols].sum(axis=1, min_count=1)
                    hist[line_name(k, v, venues)] = s
                    lines.append((line_name(k, v, venues), s, PALETTE[[0, 1, 5][j]], VENUE_DASH[v]))
        now_t = pd.DataFrame(now_rows)
        if len(venues) == 1:
            cols = st.columns(len(now_t.columns) - 1)
            for col, k in zip(cols, now_t.columns[1:]):
                col.metric(k, f"{now_t[k].iloc[0]:.1f}%")
        else:
            st.dataframe(now_t, hide_index=True, width="stretch", column_config={
                k: st.column_config.NumberColumn(format="%.1f%%") for k in now_t.columns[1:]})
            st.caption("Each market lists a slightly different set of candidates; a candidate a "
                       "market does not list counts as zero there.")
        if lines:
            market_chart(lines, "pm_matchup", period)
        for v in venues:
            source(VENUE_SOURCE[v])
        sides = pd.DataFrame({"side": [name_a] * len(side_a) + [name_b] * len(side_b),
                              "candidate": side_a + side_b})
        download("this matchup", {
            "Daily probability": pd.DataFrame(hist).reset_index() if hist else pd.DataFrame(),
            "Now": now_t, "Sides": sides,
        }, venue_info(venues), "markets_matchup.xlsx", "dl_pm_matchup")

    section_head_to_head()
    section_top4()
    section_matchup()

    with st.expander("Market depth by candidate"):
        tabs = st.tabs(AVAILABLE)
        for tab, v in zip(tabs, AVAILABLE):
            with tab:
                depth = MARKETS[v]["now"].drop(columns=[c for c in ("token_yes", "open_time") if c in MARKETS[v]["now"]]).copy()
                depth.attrs = {}
                depth["spread"] = depth["best_ask"] - depth["best_bid"]
                first = ["candidate", "probability", "price", "best_bid", "best_ask", "spread"]
                depth = depth[first + [c for c in depth.columns if c not in first]]
                cfg = {
                    "probability": st.column_config.NumberColumn("Probability", format="%.1f%%"),
                    "price": st.column_config.NumberColumn("Price (USD)", format="%.3f"),
                    "best_bid": st.column_config.NumberColumn("Best bid", format="%.3f"),
                    "best_ask": st.column_config.NumberColumn("Best ask", format="%.3f"),
                    "spread": st.column_config.NumberColumn("Spread", format="%.3f"),
                    "volume_usd": st.column_config.NumberColumn("Volume traded (USD)", format="%,.0f"),
                    "liquidity_usd": st.column_config.NumberColumn("Liquidity (USD)", format="%,.0f"),
                    "last_trade": st.column_config.NumberColumn("Last trade", format="%.3f"),
                    "volume_contracts": st.column_config.NumberColumn("Volume traded (contracts)", format="%,.0f"),
                    "volume_24h_contracts": st.column_config.NumberColumn("Volume last 24h (contracts)", format="%,.0f"),
                    "open_interest_contracts": st.column_config.NumberColumn("Open interest (contracts)", format="%,.0f"),
                }
                st.dataframe(depth, hide_index=True, width="stretch", column_config=cfg)
                raw = MARKETS[v]["now"].attrs.get("raw_sum")
                note = (f"Raw sum of all prices: {raw * 100:.1f}%. " if raw else "")
                if v == "Kalshi":
                    note += ("Each Kalshi contract pays 1 USD, so open interest in contracts is roughly "
                             "the most that could be paid out, in USD. ")
                st.caption(note + "A wide spread between best bid and best ask, low volume or low "
                           "liquidity means the price is less reliable.")
        download("market depth", current_sheets(AVAILABLE), venue_info(AVAILABLE),
                 "markets_depth.xlsx", "dl_pm_depth")

# ================================================================== 2. POLLS
st.header("Polls", anchor="polls")
st.markdown("National vote intention by political space, as published by each pollster. "
            "Each dot is one poll; the line is the monthly average across pollsters.")
try:
    polls_url = st.secrets.get("POLLS_CSV_URL") or None
    spaces_url = st.secrets.get("SPACES_CSV_URL") or None
except Exception:  # no secrets configured (e.g. running locally)
    polls_url = spaces_url = None
try:
    POLLS, SPACES, poll_problems, poll_origin = load_polls(
        polls_url, spaces_url, _stamp(POLLS_FILE) + _stamp(SPACES_FILE))
except Exception as e:
    POLLS, SPACES, poll_problems, poll_origin = pd.DataFrame(), pd.DataFrame(), [
        f"Could not read the polls sheet: {e}"], ""

for p in poll_problems:
    st.warning(p)

if POLLS is None or POLLS.empty:
    st.info("No polls loaded yet. Add rows to the polls sheet (one row per space reported by "
            "each poll) and they will appear here on the next refresh. Expected columns: "
            + ", ".join(polls_mod.COLUMNS) + ".")
else:
    @st.fragment
    def section_polls():
        c1, c2 = st.columns(2)
        level_label = c1.radio("Group by", ["Blocs (alliances added up)", "Individual parties"],
                               key="poll_level", help="Blocs add up the parties of each alliance, so "
                               "every poll can be compared. Individual parties only use polls that "
                               "report that party on its own.")
        basis = c2.radio("Show as", ["% of all respondents", "% of valid votes"], key="poll_basis",
                         help="Valid votes leaves out undecided and blank answers and rescales the "
                              "rest to 100%, like an election result.")
        pollsters = sorted(POLLS["pollster"].unique())
        period = period_picker("per_polls", "polls", "All")
        c3, c4 = st.columns([3, 1])
        chosen = c3.multiselect("Pollsters", pollsters, default=pollsters, key="poll_firms")
        smooth3 = c4.toggle("3-month average", value=False, key="poll_smooth",
                            help="Averages each month with the two before it.")
        d = POLLS[POLLS["pollster"].isin(chosen)]
        if d.empty:
            st.info("Select at least one pollster.")
            return
        level = "bloc" if level_label.startswith("Blocs") else "space"
        valid = basis == "% of valid votes"
        pp = polls_mod.per_poll(d, level, valid)
        mo = polls_mod.monthly(pp)
        line = polls_mod.smooth(mo, 3) if smooth3 else mo.pivot(index="date", columns="group", values="average")
        latest = mo[mo["date"] == mo["date"].max()].set_index("group")["average"]
        parties = pp.loc[pp["is_party"], "group"].unique()
        order = [g for g in latest.sort_values(ascending=False).index if g in parties]
        order += [g for g in mo.groupby("group")["average"].mean().sort_values(ascending=False).index
                  if g in parties and g not in order]
        pos = {p: i for i, p in enumerate(sorted(pp["pollster"].unique()))}
        spread = max(len(pos) - 1, 1)

        fig = go.Figure()
        for i, g in enumerate(order):
            col = party_color(g, i)
            dots = pp[pp["group"] == g].copy()
            # spread pollsters across the month so dots of the same month do not overlap
            dots["x"] = dots["date"] + pd.to_timedelta(
                dots["pollster"].map(pos) / spread * 20 - 10 + 14, unit="D")
            fig.add_scatter(
                x=dots["x"], y=dots["value"], mode="markers", name=g, legendgroup=g, showlegend=False,
                marker=dict(color=col, size=9, opacity=0.35),
                customdata=dots[["pollster"]].assign(m=dots["date"].dt.strftime("%b %Y")).values,
                hovertemplate="%{customdata[0]} (%{customdata[1]}): %{y:.1f}%<extra>" + g + "</extra>")
            if g in line:
                ln = line[g].dropna()
                fig.add_scatter(x=ln.index + pd.Timedelta(days=14), y=ln.values, mode="lines+markers",
                                name=g, legendgroup=g, line=dict(color=col, width=3),
                                marker=dict(size=5), hovertemplate="average %{y:.1f}%<extra>" + g + "</extra>")
        base_layout(fig, "Vote intention", height=460, y_suffix="%", period=period)
        fig.update_layout(hovermode="closest")
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, theme=None, key="polls_chart")
        if level == "space":
            st.caption("Individual parties: a poll that reports an alliance as a single figure "
                       "(e.g. \"LLA + PRO\") appears under that alliance, not under each party.")
        st.caption("The line is the monthly average: each pollster counts once per month, weighted "
                   "by the square root of its sample size (the median sample size when one is missing; "
                   "equal weights when none is reported).")

        st.markdown("**Monthly average**")
        table = mo[mo["group"].isin(order)].pivot(index="date", columns="group", values="average")[order]
        table = table.join(mo.groupby("date")["pollsters"].max()).sort_index(ascending=False).reset_index()
        table["date"] = table["date"].dt.strftime("%b %Y")
        st.dataframe(table.rename(columns={"date": "month"}), hide_index=True, width="stretch",
                     column_config={g: st.column_config.NumberColumn(format="%.1f%%") for g in order})
        source("compiled from the pollsters listed; links in the downloadable file.")
        download("polls", {
            "Monthly average": mo.rename(columns={"date": "month"}),
            "By poll": pp.drop(columns=["poll_id", "is_party"]).rename(columns={"date": "month"}),
            "Rows as loaded": d[["date", "pollster", "space", "bloc", "value", "sample_size", "source_url"]]
            .rename(columns={"date": "month"}),
            "Spaces and blocs": SPACES,
        }, {"Grouped by": level_label, "Shown as": basis, "Data from": poll_origin,
            "Monthly average": "Each pollster counts once per month, weighted by sqrt(sample size)."},
            "polls.xlsx", "dl_polls")

    section_polls()

# ================================================================== 3. ICG vs ICC
st.header("Government vs. consumer confidence", anchor="government-vs-consumer-confidence")
if ICG and ICC:
    @st.fragment
    def section_icg_icc():
        st.markdown("Confidence in the government (ICG) next to consumer confidence about the economy "
                    "(ICC), both surveyed monthly by Universidad Torcuato Di Tella.")
        c1, c2 = st.columns([1, 1.4])
        period = period_picker("per_cmp", "monthly", "5 years", c2)
        mode = c1.radio("Scale", ["Both on 0-100 (ICG x 20)", "Index, December 2023 = 100"],
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
        base_layout(fig, title, period=period)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, theme=None, key="cmp_chart")
        st.caption(note)
        source(f"{ICG_SOURCE} {ICC_SOURCE}")
        download("both series", {"ICG vs ICC": m}, {"ICG": ICG_SOURCE, "ICC": ICC_SOURCE,
                 "Note": "ICG ranges 0-5; 'ICG x 20' puts it on 0-100."}, "icg_vs_icc.xlsx", "dl_cmp")

    section_icg_icc()
else:
    st.info("Connect the ICG and ICC sheets (secrets ICG_CSV_URL and ICC_CSV_URL) to see this chart.")

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
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, theme=None, key="pres_chart")
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
        period = period_picker("per_icg_bd", "monthly", "5 years")
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
        base_layout(fig, "ICG (0-5)", height=440, period=period)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, theme=None, key="icg_bd_chart")
        if dim != "Components" and t["n"].notna().any():
            latest = t[t["date"] == t["date"].max()]
            st.caption(f"Respondents in {t['date'].max():%B %Y}: "
                       + "; ".join(f"{r.category} {int(r.n)}" for r in latest.itertuples())
                       + ". Groups with fewer than about 300 respondents move a lot from month to "
                         "month by chance alone.")
        elif dim != "Components":
            st.caption("Small groups (for example primary education or ages 18-29) have few respondents "
                       "and move a lot from month to month by chance alone; the 3-month average helps.")
        else:
            st.caption("The ICG is the average of these five components.")
        source(ICG_SOURCE)
        n_wide = t.pivot(index="date", columns="category", values="n")
        n_wide.columns = [f"n {c}" for c in n_wide.columns]
        download(f"ICG by {dim.lower()}", {
            f"By {dim}"[:31]: wide.reset_index(),
            "Long format": t,
            **({"Sample sizes": n_wide.reset_index()} if dim != "Components" and t["n"].notna().any() else {}),
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
        period = period_picker("per_icc_bd", "monthly", "5 years")
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
        base_layout(fig, "ICC (0-100)", height=440, period=period)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, theme=None, key="icc_bd_chart")
        if dim == "Components":
            st.caption("Personal situation, the macroeconomic situation and buying conditions for "
                       "durable goods and real estate are the three components of the ICC.")
        source(ICC_SOURCE)
        out = wide.join(nat.rename("National")).reset_index()
        download(f"ICC by {dim.lower()}", {f"By {dim}": out},
                 {"Source": ICC_SOURCE, "Breakdown": dim}, f"icc_{dim.lower()}.xlsx", "dl_icc_bd")

    section_icc_breakdown()
elif not icc_path:
    st.info("Connect the ICC sheet (secret ICC_CSV_URL) to see this section.")

st.divider()
st.caption("Built with public data. Polls and confidence indices refresh within 15 minutes of an edit to their sheets; "
           "prediction market prices refresh every 5 minutes.")
