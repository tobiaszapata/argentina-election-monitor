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
    from monitor.utils import find_file, latest_file, read_sheet, to_excel
except ModuleNotFoundError:
    try:
        import icc as icc_mod
        import icg as icg_mod
        import polls as polls_mod
        import polymarket as pm
        import kalshi as ks
        from utils import find_file, latest_file, read_sheet, to_excel
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
              ("15 days", 15), ("7 days", 7), ("Today", "today")],
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
    if period == "term":
        start = pd.Timestamp("2023-12-01")
    elif period == "today":
        start = end.normalize()
    else:
        start = end - pd.Timedelta(days=period)
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
                   color=INK, automargin=True),
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
    raw, notes = read_sheet(url, "ICG sheet")
    nat, parts, problems = icg_mod.from_sheet(raw)
    return nat, parts, notes + problems


@st.cache_data(ttl=600, show_spinner="Loading consumer confidence sheet...")
def load_icc_sheet(url: str):
    raw, notes = read_sheet(url, "ICC sheet")
    icc, problems = icc_mod.from_sheet(raw)
    return icc, notes + problems


@st.cache_data(show_spinner="Loading consumer confidence series...")
def load_icc(path: str, stamp: float):
    return icc_mod.load(path)


VENUES = {"Polymarket": pm, "Kalshi": ks}
VENUE_DASH = {"Polymarket": "solid", "Kalshi": "dash"}


@st.cache_data(ttl=300, show_spinner="Fetching prediction market prices...")
def load_venue_now(venue: str):
    return VENUES[venue].fetch_current()


@st.cache_data(ttl=1800, show_spinner="Fetching prediction market price history...")
def load_venue_history(venue: str, ids: pd.DataFrame):
    """Hourly history. `ids` holds only stable identifiers (candidate, token or ticker), so the
    cache lasts its full 30 minutes instead of resetting every time prices move."""
    return VENUES[venue].fetch_history(ids)


@st.cache_data(ttl=300, show_spinner="Fetching today's prices...")
def load_venue_intraday(venue: str, ids: pd.DataFrame, start_ts: int):
    """Today's prices since midnight in Argentina, at the finest step each market offers."""
    return VENUES[venue].fetch_intraday(ids, start_ts)


@st.cache_data(ttl=600, show_spinner="Loading polls...")
def load_polls(polls_url: str | None, spaces_url: str | None, stamp: float):
    if polls_url:
        raw, notes = read_sheet(polls_url, "polls sheet")
        origin = "Google Sheets"
    else:
        raw, notes = read_sheet(POLLS_FILE, "polls file") if POLLS_FILE else (pd.DataFrame(columns=polls_mod.COLUMNS), [])
        origin = POLLS_FILE.name if POLLS_FILE else "no polls file"
    raw_spaces = None
    if spaces_url:
        raw_spaces, n2 = read_sheet(spaces_url, "spaces sheet")
        notes += n2
    elif SPACES_FILE:
        raw_spaces, n2 = read_sheet(SPACES_FILE, "spaces file")
        notes += n2
    rows, spaces, problems = polls_mod.clean(raw, raw_spaces)
    problems = notes + problems
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


ART = "America/Argentina/Buenos_Aires"
ID_COLUMNS = {"Polymarket": ["candidate", "token_yes"], "Kalshi": ["candidate", "ticker", "open_time"]}


def to_art(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """UTC timestamps -> Argentine local time, without time zone (for the charts)."""
    idx = index if index.tz is not None else index.tz_localize("UTC")
    return idx.tz_convert(ART).tz_localize(None)


def build_series(hourly_utc: pd.DataFrame, live: pd.Series, live_at: pd.Timestamp) -> tuple:
    """Daily closes in Argentine time: for each day, the price at the last time available before
    midnight in Argentina, rescaled so candidates sum to 100%. Today's row is the live price.
    Also returns yesterday's raw close (the starting point of today's intraday line)."""
    raw = hourly_utc.copy()
    raw.index = to_art(raw.index)
    raw = raw.ffill()
    daily_raw = raw.groupby(raw.index.floor("D")).last()
    today = live_at.normalize()
    past = daily_raw[daily_raw.index < today]
    daily = pd.concat([pm.normalize(past), pd.DataFrame([live], index=[today])])
    daily.index.name = "date (Argentina)"
    return daily, (past.iloc[-1] if not past.empty else None)


def build_intraday(intra_utc: pd.DataFrame, prev_close, live: pd.Series, live_at: pd.Timestamp) -> pd.DataFrame:
    """Today since 00:00 in Argentina: starts at yesterday's close, then every price recorded
    today, and ends with the live price."""
    today = live_at.normalize()
    parts = []
    if prev_close is not None:
        parts.append(pd.DataFrame([prev_close], index=[today]))
    if intra_utc is not None and not intra_utc.empty:
        raw = intra_utc.copy()
        raw.index = to_art(raw.index)
        parts.append(raw[(raw.index > today) & (raw.index < live_at)])
    out = pm.normalize(pd.concat(parts).sort_index().ffill()) if parts else pd.DataFrame()
    out = pd.concat([out, pd.DataFrame([live], index=[live_at])])
    out.index.name = "time (Argentina)"
    return out


MARKETS, DISPLAY = {}, {}
for venue in VENUES:
    entry = {"now": None, "norm": pd.DataFrame(), "intraday": pd.DataFrame(), "live_at": None, "error": None}
    try:
        now = load_venue_now(venue).copy()
        attrs = dict(now.attrs)
        for c in now["candidate"]:
            DISPLAY.setdefault(canon(c), c)
        now["candidate"] = now["candidate"].map(lambda c: DISPLAY[canon(c)])
        now.attrs = attrs
        entry["now"] = now
        live_at = to_art(pd.DatetimeIndex([attrs["fetched_at"]]))[0].floor("min")
        entry["live_at"] = live_at
        live = now.groupby("candidate")["probability"].sum()
        try:
            raw_now = load_venue_now(venue)
            ids = raw_now[[c for c in ID_COLUMNS[venue] if c in raw_now]].copy()
            ids.attrs = {}
            fix = lambda t: t.rename(columns=lambda c: DISPLAY.get(canon(c), c)).T.groupby(level=0).sum(
                min_count=1).T  # same display names; merge duplicated names
            hist = load_venue_history(venue, ids)
            prev_close = None
            if hist is not None and not hist.empty:
                entry["norm"], prev_close = build_series(fix(hist), live, live_at)
            midnight = pd.Timestamp(live_at.normalize()).tz_localize(ART)
            try:
                intra = load_venue_intraday(venue, ids, int(midnight.timestamp()))
                intra = fix(intra) if intra is not None and not intra.empty else None
            except Exception:
                intra = None
            entry["intraday"] = build_intraday(intra, prev_close, live, live_at)
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
    """Live probability minus the close of exactly 7 days ago (Argentine time)."""
    norm = MARKETS[v]["norm"]
    if norm.empty or c not in norm or c not in prob(v):
        return None
    past = norm[c].get(MARKETS[v]["live_at"].normalize() - pd.Timedelta(days=7))
    return None if past is None or pd.isna(past) else prob(v)[c] - past


def series_for(v: str, period) -> pd.DataFrame:
    """Today's intraday prices for "Today"; daily closes for every other period."""
    return MARKETS[v]["intraday"] if period == "today" else MARKETS[v]["norm"]


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


def market_chart(lines: list, key: str, period=None, height: int = 420, y_title="Probability of winning",
                 suffix="%", fmt="%{y:.1f}%"):
    """lines: (name, series, color, dash). The last point of each line is the live price and is
    marked with a dot."""
    today = period == "today"
    fig = go.Figure()
    for name, s, color, dash in lines:
        s = s.dropna()
        if s.empty:
            continue
        fig.add_scatter(x=s.index, y=s.values, name=name, mode="lines", legendgroup=name,
                        line=dict(color=color, width=2.2 if today else 2.6, dash=dash), hovertemplate=fmt)
        fig.add_scatter(x=[s.index[-1]], y=[s.values[-1]], mode="markers", legendgroup=name, showlegend=False,
                        marker=dict(color=color, size=9, line=dict(color="white", width=1.5)),
                        hovertemplate=fmt + " (live)<extra>" + name + "</extra>")
    base_layout(fig, y_title, height=height, y_suffix=suffix, period=period)
    if today:
        fig.update_xaxes(tickformat="%H:%M")
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, theme=None, key=key)


def time_caption(venues: list, period):
    live = max(MARKETS[v]["live_at"] for v in venues)
    freq = ("today's prices since midnight (about every 10 minutes on Polymarket, every minute on Kalshi), "
            "starting from yesterday's close" if period == "today"
            else "one point per day: the probability at the last time available before midnight")
    st.caption(f"Argentine time (UTC-3). The line shows {freq}; the dot at the end is the live probability, "
               f"refreshed every 5 minutes (last update {live:%d %b %H:%M}).")


def venue_info(venues: list) -> dict:
    info = {f"Source ({v})": VENUE_SOURCE[v] for v in venues}
    info["Price"] = ("Mid-point between best bid and best ask when the spread is 10 cents or less; "
                     "otherwise last traded price.")
    info["Probability"] = "Prices rescaled so that each market's candidates sum to 100% at each time."
    info["Time zone"] = ("Argentina (UTC-3). Daily close = probability at the last time available before "
                         "midnight in Argentina; it can differ slightly from the market's own daily close "
                         "(UTC). Today's value is the live probability. The 'Today' sheet has today's prices "
                         "since midnight, starting from yesterday's close.")
    info["Retrieved"] = " / ".join(f"{v} {MARKETS[v]['live_at']:%Y-%m-%d %H:%M} (Argentina)" for v in venues)
    return info


def history_sheet(venues: list, group: list, table: str = "norm") -> pd.DataFrame:
    parts = []
    for v in venues:
        n = MARKETS[v][table]
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
        lines = [(line_name(c, v, venues), series_for(v, period)[c], color_for(c, j), VENUE_DASH[v])
                 for v in venues for j, c in enumerate(pair) if c in series_for(v, period)]
        if lines:
            market_chart(lines, "pm_h2h", period)
        gap = None
        if len(venues) > 1:
            n_pm, n_ks = series_for("Polymarket", period), series_for("Kalshi", period)
            idx = n_pm.index.union(n_ks.index)  # live points can differ by a few minutes
            n_pm, n_ks = n_pm.reindex(idx).ffill(), n_ks.reindex(idx).ffill()
            gap = pd.DataFrame({c: n_ks[c] - n_pm[c] for c in pair if c in n_pm and c in n_ks}).dropna(how="all")
            if not gap.empty:
                st.markdown("**Gap between markets** (Kalshi minus Polymarket, percentage points)")
                market_chart([(c, gap[c], color_for(c, j), "solid") for j, c in enumerate(gap.columns)],
                             "pm_gap", period, height=280, y_title="Kalshi - Polymarket (pts)", suffix="",
                             fmt="%{y:+.1f} pts")
                st.caption("Above zero, Kalshi gives the candidate a higher chance than Polymarket.")
        for v in venues:
            source(VENUE_SOURCE[v])
        time_caption(venues, period)
        sheets = {"Daily close (Argentina)": history_sheet(venues, pair),
                  "Today (Argentina)": history_sheet(venues, pair, "intraday")}
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
        lines = [(line_name(c, v, venues), series_for(v, period)[c], color_for(c, j), VENUE_DASH[v])
                 for v in venues for j, c in enumerate(top) if c in series_for(v, period)]
        if lines:
            market_chart(lines, "pm_top4", period)
        if len(venues) > 1:
            st.caption("Ranked by the average of both markets. A candidate listed in only one market "
                       "shows a blank for the other.")
        for v in venues:
            source(VENUE_SOURCE[v])
        time_caption(venues, period)
        sheets = {"Daily close (Argentina)": history_sheet(venues, top),
                  "Today (Argentina)": history_sheet(venues, top, "intraday")}
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
        now_rows, lines, hist, hist_h = [], [], {}, {}
        for v in venues:
            p = prob(v)
            rest = [c for c in p.index if c not in side_a + side_b]
            g = dict(groups, **({"Everyone else": rest} if rest else {}))
            now_rows.append({"Market": v, **{k: p.reindex(m).sum() for k, m in g.items()}})
            norm, intra, shown = MARKETS[v]["norm"], MARKETS[v]["intraday"], series_for(v, period)
            for j, (k, m) in enumerate(g.items()):
                cols = [c for c in m if c in norm]
                if cols:
                    hist[line_name(k, v, venues)] = norm[cols].sum(axis=1, min_count=1)
                    hist_h[line_name(k, v, venues)] = intra[[c for c in cols if c in intra]].sum(axis=1, min_count=1)
                    lines.append((line_name(k, v, venues), shown[[c for c in cols if c in shown]].sum(axis=1, min_count=1),
                                  PALETTE[[0, 1, 5][j]], VENUE_DASH[v]))
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
            time_caption(venues, period)
        for v in venues:
            source(VENUE_SOURCE[v])
        sides = pd.DataFrame({"side": [name_a] * len(side_a) + [name_b] * len(side_b),
                              "candidate": side_a + side_b})
        download("this matchup", {
            "Daily close (Argentina)": pd.DataFrame(hist).reset_index() if hist else pd.DataFrame(),
            "Today (Argentina)": pd.DataFrame(hist_h).reset_index() if hist_h else pd.DataFrame(),
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
            "Each dot is one poll; the dotted line is the trend across pollsters.")
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

# Colors offered for poll groups: name -> hex. The first ones follow the reference chart.
GROUP_COLORS = {
    "Violet": "#7B61C9", "Light blue": "#4FA8E0", "Blue": "#1F5FAD", "Yellow": "#E3B505",
    "Orange": "#F08A24", "Green": "#3F9C7B", "Red": "#D0544B", "Pink": "#D46BA8",
    "Brown": "#8D6E63", "Grey": "#9AA3B2",
}
# Suggested color by keyword in the group name (first match wins); the user can change it.
COLOR_HINTS = [("provincias", "Orange"), ("lla", "Violet"), ("libertad", "Violet"),
               ("peron", "Light blue"), ("fuerza patria", "Light blue"), ("patria", "Light blue"),
               ("left", "Red"), ("fit", "Red"), ("izquierda", "Red"), ("ucr", "Green"),
               ("pro", "Yellow"), ("juntos", "Yellow"), ("blank", "Grey"), ("blanco", "Grey"),
               ("undecided", "Grey"), ("indecisos", "Grey"), ("other", "Brown"), ("otros", "Brown"),
               ("rest", "Brown"), ("resto", "Brown")]
TREND_WINDOWS = {"7 days": 7, "15 days": 15, "30 days": 30, "60 days": 60}
GROUPING_HELP = {
    "Blocs": "Adds up the parties of each alliance, as set in the spaces sheet. Every poll can be "
             "compared, whether it reports the parties separately or together.",
    "Individual parties": "Each space as it was loaded. A party's line only uses the polls that "
                          "report it on its own.",
    "Custom groups": "Build your own groups: choose how many, name them and pick the spaces each one adds up.",
}


def suggested_colors(names: list) -> dict:
    """A suggested color per group: by keyword first, then the unused colors in order."""
    out, used = {}, set()
    for n in names:
        low = n.lower()
        hint = next((c for k, c in COLOR_HINTS if re.search(rf"\b{re.escape(k)}", low)), None)
        if hint and hint not in used:
            out[n] = hint
            used.add(hint)
    free = [c for c in GROUP_COLORS if c not in used and c != "Grey"] + ["Grey"]
    for n in names:
        if n not in out:
            out[n] = free.pop(0) if free else "Grey"
    return out


def swatch(hex_color: str) -> str:
    return (f"<div style='width:1.4rem;height:1.4rem;border-radius:50%;margin-bottom:1rem;"
            f"background:{hex_color};'></div>")


def default_custom_groups(polls: pd.DataFrame) -> list:
    """Starting point for custom groups: LLA + PRO, Peronism, and the other parties."""
    present = polls.drop_duplicates("space").set_index("space")
    parties = present[present["is_party"]]
    lla = [s for s in parties.index if parties.loc[s, "bloc"] == "LLA + PRO"]
    per = [s for s in parties.index if parties.loc[s, "bloc"] == "Peronism"]
    rest = [s for s in parties.index if s not in lla + per]
    return [("LLA + PRO", lla), ("Peronism", per), ("Rest", rest)]


if POLLS is None or POLLS.empty:
    st.info("No polls loaded yet. Add rows to the polls sheet (one row per space reported by "
            "each poll) and they will appear here on the next refresh. Expected columns: "
            + ", ".join(polls_mod.COLUMNS) + ".")
else:
    @st.fragment
    def section_polls():
        # ---- 1. how to group
        st.markdown("**1. How to group the polls**")
        mode = st.radio("How to group the polls", list(GROUPING_HELP), horizontal=True,
                        key="poll_level", label_visibility="collapsed")
        st.caption(GROUPING_HELP[mode])

        # ---- 2. groups and colors (depends on 1)
        st.markdown("**2. Groups and colors**")
        color_names = list(GROUP_COLORS)
        rows = []  # (name, members or None, show, color)
        box = st.container(border=True)
        if mode == "Custom groups":
            defaults = default_custom_groups(POLLS)
            spaces_present = sorted(POLLS["space"].unique())
            n = box.columns([1, 3])[0].number_input("Number of groups", min_value=1, max_value=8,
                                                    value=len(defaults), step=1, key="cg_count")
            slot_names = [defaults[i][0] if i < len(defaults) else f"Group {i + 1}" for i in range(8)]
            sugg = suggested_colors(slot_names)  # fixed per slot, so a chosen color never jumps
            h = box.columns([0.6, 1.6, 3, 1.3, 0.3])
            for col, label in zip(h, ["Show", "Name", "Spaces it adds up", "Color", ""]):
                col.caption(label)
            for i in range(n):
                dname, dsp = defaults[i] if i < len(defaults) else ("", [])
                c = box.columns([0.6, 1.6, 3, 1.3, 0.3], vertical_alignment="center")
                show = c[0].checkbox("Show", value=True, key=f"cg_show_{i}", label_visibility="collapsed")
                name = c[1].text_input("Name", dname, key=f"cg_name_{i}", placeholder=f"Group {i + 1}",
                                       label_visibility="collapsed").strip()
                members = c[2].multiselect("Spaces", spaces_present, default=dsp, key=f"cg_sp_{i}",
                                           label_visibility="collapsed", placeholder="Pick spaces")
                label = name or (" + ".join(members) if members else f"Group {i + 1}")
                color = c[3].selectbox("Color", color_names, key=f"cg_color_{i}",
                                       index=color_names.index(sugg[slot_names[i]]),
                                       label_visibility="collapsed")
                c[4].markdown(swatch(GROUP_COLORS[color]), unsafe_allow_html=True)
                if members:
                    rows.append((label, members, show, color))
            used = [sp for _, m, _, _ in rows for sp in m]
            twice = sorted({sp for sp in used if used.count(sp) > 1})
            if twice:
                box.warning("These spaces are in more than one group and count in each: " + ", ".join(twice) + ".")
            box.caption("Spaces left out of every group are not drawn, but still count in the total "
                        "for % of valid votes.")
            if not rows:
                st.info("Pick the spaces of at least one group.")
                return
            level = {name: m for name, m, _, _ in rows}
        else:
            level = "bloc" if mode == "Blocs" else "space"
            base = polls_mod.per_poll(POLLS, level, False)
            size = base.groupby("group")["value"].mean().sort_values(ascending=False)
            party = set(base.loc[base["is_party"], "group"])
            names = [g for g in size.index if g in party] + [g for g in size.index if g not in party]
            sugg = suggested_colors(names)
            h = box.columns([0.6, 4.6, 1.3, 0.3])
            for col, label in zip(h, ["Show", "Group" if mode == "Blocs" else "Party", "Color", ""]):
                col.caption(label)
            for g in names:
                c = box.columns([0.6, 4.6, 1.3, 0.3], vertical_alignment="center")
                show = c[0].checkbox("Show", value=g in party, key=f"show_{mode}_{g}",
                                     label_visibility="collapsed")
                c[1].markdown(g + ("" if g in party else "  \n<span style='opacity:.6;font-size:.85em'>"
                                   "not a party: hidden with % of valid votes</span>"), unsafe_allow_html=True)
                color = c[2].selectbox("Color", color_names, index=color_names.index(sugg[g]),
                                       key=f"color_{mode}_{g}", label_visibility="collapsed")
                c[3].markdown(swatch(GROUP_COLORS[color]), unsafe_allow_html=True)
                rows.append((g, None, show, color))

        # ---- 3. options
        st.markdown("**3. Options**")
        o1, o2 = st.columns([1, 1.2])
        basis = o1.radio("Show as", ["% of all respondents", "% of valid votes"], horizontal=True,
                         key="poll_basis", help="Valid votes leaves out blank and undecided answers and "
                                                "rescales the parties to 100%, like an election result.")
        trend_label = o2.segmented_control("Trend line window", list(TREND_WINDOWS),
                                           default="30 days", key="poll_trend") or "30 days"
        o3, o4 = st.columns([1, 1.2])
        with o3:
            period = period_picker("per_polls", "polls", "All")
        pollsters = sorted(POLLS["pollster"].unique())
        with o4.expander(f"Pollsters ({len(pollsters)})"):
            chosen = st.multiselect("Pollsters", pollsters, default=pollsters, key="poll_firms",
                                    label_visibility="collapsed")
        window = TREND_WINDOWS[trend_label]
        d = POLLS[POLLS["pollster"].isin(chosen)]
        if d.empty:
            st.info("Select at least one pollster.")
            return
        valid = basis == "% of valid votes"
        pp = polls_mod.per_poll(d, level, valid)
        if pp.empty:
            st.info("None of the selected polls report these spaces.")
            return
        mo = polls_mod.monthly(pp)
        tr = polls_mod.trend(pp, window)
        present = set(pp["group"])
        shown = [(g, GROUP_COLORS[c]) for g, _, show, c in rows if show and g in present]
        if not shown:
            st.info("Tick at least one group in step 2.")
            return

        fig = go.Figure()
        for g, col in shown:
            dots = pp[pp["group"] == g]
            fig.add_scatter(
                x=dots["date"], y=dots["value"], mode="markers", name=g, legendgroup=g, showlegend=True,
                marker=dict(color=col, size=9, opacity=0.45, line=dict(width=0)),
                customdata=dots[["pollster"]].assign(d=dots["date"].dt.strftime("%d %b %Y")).values,
                hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}: %{y:.1f}%<extra>" + g + "</extra>")
            if g in tr:
                ln = tr[g].dropna()
                fig.add_scatter(x=ln.index, y=ln.values, mode="lines", name=f"{g} trend", legendgroup=g,
                                showlegend=False, connectgaps=True,
                                line=dict(color=col, width=3.2, dash="dot"),
                                hovertemplate="trend: %{y:.1f}%<extra>" + g + "</extra>")
        base_layout(fig, "", height=500, period=period)
        unit = "% of valid votes" if valid else "% of all respondents"
        fig.update_layout(
            hovermode="closest", margin=dict(l=45, r=10, t=95, b=55),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                        font=dict(size=12, color=INK), itemsizing="constant"),
            annotations=[
                dict(text="<b>Presidential election polls</b>", xref="paper", yref="paper", x=0, y=1.21,
                     xanchor="left", yanchor="bottom", showarrow=False, font=dict(size=16, color=INK)),
                dict(text=f"{unit}, time-varying trend, {window}-day weighted average", xref="paper",
                     yref="paper", x=0, y=1.14, xanchor="left", yanchor="bottom", showarrow=False,
                     font=dict(size=12, color=MUTED)),
                dict(text=f"Source: various media polls ({pp['pollster'].nunique()} pollsters, "
                          f"{pp['poll_id'].nunique()} polls).", xref="paper", yref="paper", x=0, y=-0.11,
                     xanchor="left", yanchor="top", showarrow=False, font=dict(size=11, color=MUTED)),
            ])
        fig.update_xaxes(tickformat="%d-%b-%y", showgrid=False)
        fig.update_yaxes(automargin=True, ticksuffix="")
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, theme=None, key="polls_chart")
        notes = [f"Each dot is one poll. The dotted line is a weighted average of the polls around each "
                 f"date: the closer a poll is, the more it weighs, and almost all the weight comes from "
                 f"polls within {window // 2} days on either side. Each pollster counts once, weighted by "
                 "the square root of its sample size when it is reported."]
        if mode == "Individual parties":
            notes.append("A poll that reports an alliance as a single figure (e.g. \"LLA + PRO\") appears "
                         "under that alliance, not under each party.")
        st.caption(" ".join(notes))

        st.markdown("**Monthly average**")
        cols_order = [g for g, _ in shown if g in set(mo["group"])]
        table = mo[mo["group"].isin(cols_order)].pivot(index="date", columns="group", values="average")[cols_order]
        table = table.join(mo.groupby("date")["pollsters"].max()).sort_index(ascending=False).reset_index()
        table["date"] = table["date"].dt.strftime("%b %Y")
        st.dataframe(table.rename(columns={"date": "month"}), hide_index=True, width="stretch",
                     column_config={g: st.column_config.NumberColumn(format="%.1f%%") for g in cols_order})
        st.caption("Monthly average: each pollster counts once per month.")
        source("compiled from the pollsters listed; links in the downloadable file.")
        groups_sheet = (pd.DataFrame([(g, sp) for g, m in level.items() for sp in m], columns=["group", "space"])
                        if isinstance(level, dict) else SPACES)
        download("polls", {
            "By poll": pp[["date", "pollster", "group", "value", "sample_size"]].sort_values(["date", "pollster"]),
            f"Trend {window}-day": tr.reset_index(names="date"),
            "Monthly average": mo.rename(columns={"date": "month"}),
            "Rows as loaded": d[["date", "pollster", "space", "bloc", "value", "sample_size", "source_url"]],
            "Groups": groups_sheet,
        }, {"Grouped by": mode, "Shown as": basis, "Data from": poll_origin,
            "Trend": f"Gaussian-weighted average of nearby polls ({window}-day window: ~95% of the "
                     "weight within +/- half the window); each pollster counts once, weighted by "
                     "sqrt(sample size)."},
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
