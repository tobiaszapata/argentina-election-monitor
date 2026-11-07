"""Poll tracker: national vote intention by political space.

Polls sheet (one row per space reported by a poll):
    year, month, pollster, space, value, sample_size, source_url, day
`day` is optional (the last column, so older sheets keep working): with it each poll sits on
its fieldwork date; without it, on the 15th of the month.
Spaces sheet (which bloc each space belongs to):
    space, bloc

Rule for alliances: spaces can be added up into a bloc, never split. A poll that
reports "LLA + PRO" together counts for the bloc, but not for LLA or PRO alone.
"""
import re

import numpy as np
import pandas as pd

COLUMNS = ["year", "month", "pollster", "space", "value", "sample_size", "source_url", "day"]
REQUIRED = ["year", "month", "pollster", "space", "value"]
SPACE_COLUMNS = ["space", "bloc"]
DEFAULT_N = 1000

# Used when the spaces sheet is not connected. Edit the sheet, not this list.
DEFAULT_SPACES = [
    ("LLA", "LLA + PRO"),
    ("PRO", "LLA + PRO"),
    ("LLA + PRO", "LLA + PRO"),
    ("Fuerza Patria", "Peronism"),
    ("Provincias Unidas", "Provincias Unidas / Others"),
    ("UCR", "Provincias Unidas / Others"),
    ("Otros", "Provincias Unidas / Others"),
    ("Provincias Unidas / Otros", "Provincias Unidas / Others"),
    ("FIT-U", "Left (FIT-U)"),
    ("Blanco", "Blank + undecided"),
    ("Indecisos", "Blank + undecided"),
    ("Blanco + Indecisos", "Blank + undecided"),
]
# Blocs that are not parties: left out of "% of valid votes" and not drawn as lines.
NON_PARTY = {"blank": "Blank", "blanco": "Blank", "en blanco": "Blank",
             "undecided": "Undecided", "indecisos": "Undecided", "ns/nc": "Undecided",
             "no sabe": "Undecided", "blank + undecided": "Blank + undecided",
             "blanco + indecisos": "Blank + undecided", "blanco + undecided": "Blank + undecided"}
NON_PARTY_BLOCS = {"Blank", "Undecided", "Blank + undecided"}
MONTHS = {"ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "ago": 8, "aug": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11,
          "dic": 12, "dec": 12}


def _num(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip().str.replace("%", "", regex=False)
    s = s.str.replace(r"(?<=\d)\.(?=\d{3}(\D|$))", "", regex=True).str.replace(",", ".", regex=False)
    return pd.to_numeric(s.replace({"": None, "nan": None, "None": None}), errors="coerce")


def _month(v):
    """Month number from 10, '10', 'oct', 'Octubre', or a date such as '2026-10' / '1/10/2026'."""
    s = str(v).strip().lower()
    if s in ("", "nan", "none"):
        return None, None
    if re.fullmatch(r"\d{1,2}(\.0)?", s):
        m = int(float(s))
        return (m, None) if 1 <= m <= 12 else (None, None)
    if s[:3] in MONTHS:
        return MONTHS[s[:3]], None
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})([-/]\d{1,2})?", s)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", s)  # day/month/year
    if m:
        y = int(m.group(3))
        return int(m.group(2)), (y + 2000 if y < 100 else y)
    return None, None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip()).lower()


def spaces_table(raw: pd.DataFrame | None) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(DEFAULT_SPACES, columns=SPACE_COLUMNS)
    t = raw.copy()
    t.columns = [_norm(c) for c in t.columns]
    if not set(SPACE_COLUMNS) <= set(t.columns):
        return pd.DataFrame(DEFAULT_SPACES, columns=SPACE_COLUMNS)
    t = t[SPACE_COLUMNS].dropna(subset=["space"])
    t["space"] = t["space"].astype(str).str.strip()
    t["bloc"] = t["bloc"].fillna(t["space"]).astype(str).str.strip()
    return t[t["space"] != ""].drop_duplicates("space", keep="last")


def clean(raw: pd.DataFrame, raw_spaces: pd.DataFrame | None = None) -> tuple:
    """Return (rows, spaces mapping, list of problems)."""
    problems = []
    spaces = spaces_table(raw_spaces)
    df = raw.copy()
    df.columns = [re.sub(r"\s+", "_", _norm(c)) for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        return pd.DataFrame(), spaces, [f"Missing columns in the polls sheet: {', '.join(missing)}"]
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[COLUMNS].dropna(how="all")
    if df.empty:  # sheet with headers only: nothing loaded yet
        return pd.DataFrame(), spaces, problems
    df = df[~df.astype(str).apply(lambda r: all(str(v).strip() in ("", "nan", "None", "<NA>") for v in r), axis=1)]
    df["day"] = _num(df["day"])
    for c in ("pollster", "space", "source_url"):
        df[c] = df[c].astype(str).str.strip().replace({"nan": None, "None": None, "": None})
    df["value"] = _num(df["value"])
    df["sample_size"] = _num(df["sample_size"])
    parsed = df["month"].map(_month)
    df["month_n"] = parsed.map(lambda x: x[0])
    year = _num(df["year"])
    df["year_n"] = year.fillna(parsed.map(lambda x: x[1]))
    if df["value"].notna().any() and df["value"].max() <= 1:
        df["value"] *= 100

    bad = df[["pollster", "space", "value", "month_n", "year_n"]].isna().any(axis=1)
    if bad.any():
        problems.append(f"{int(bad.sum())} row(s) skipped: missing or unreadable year, month, pollster, "
                        "space or value (sheet rows: "
                        + ", ".join(str(i + 2) for i in df.index[bad][:12]) + ").")
    df = df[~bad].copy()
    df["month_start"] = pd.to_datetime(dict(year=df["year_n"].astype(int), month=df["month_n"].astype(int), day=1))
    days_in = df["month_start"].dt.days_in_month
    bad_day = df["day"].notna() & ((df["day"] < 1) | (df["day"] > days_in))
    if bad_day.any():
        problems.append(f"{int(bad_day.sum())} row(s) have a day that does not exist in that month; "
                        "they were placed on the 15th (sheet rows: "
                        + ", ".join(str(i + 2) for i in df.index[bad_day][:12]) + ").")
    day = df["day"].where(~bad_day).fillna(15).astype(int)
    df["date"] = df["month_start"] + pd.to_timedelta(day - 1, unit="D")

    lookup = dict(zip(spaces["space"].map(_norm), spaces["bloc"]))
    names = dict(zip(spaces["space"].map(_norm), spaces["space"]))
    key = df["space"].map(_norm)
    unknown = sorted(df.loc[~key.isin(lookup), "space"].unique())
    if unknown:
        problems.append("Spaces not listed in the spaces sheet (shown as their own bloc): "
                        + ", ".join(unknown) + ".")
    df["space"] = key.map(names).fillna(df["space"])
    df["bloc"] = key.map(lookup).fillna(df["space"])
    df["bloc"] = df["bloc"].map(lambda b: NON_PARTY.get(_norm(b), b))
    df["is_party"] = ~df["bloc"].isin(NON_PARTY_BLOCS)
    df["poll_id"] = df["pollster"] + " | " + df["date"].dt.strftime("%Y-%m-%d")
    # Same pollster, date and space loaded twice: average them.
    dup = df.duplicated(["poll_id", "space"], keep=False)
    if dup.any():
        problems.append(f"{df.loc[dup, 'poll_id'].nunique()} poll(s) have the same space loaded "
                        "twice on the same date; those values were averaged.")
        df = (df.groupby(["poll_id", "date", "month_start", "pollster", "space", "bloc", "is_party"],
                         as_index=False)
                .agg(value=("value", "mean"), sample_size=("sample_size", "mean"),
                     source_url=("source_url", "first")))
    return df.reset_index(drop=True), spaces, problems


def per_poll(df: pd.DataFrame, level, valid_votes: bool) -> pd.DataFrame:
    """One value per poll and group.
    level: 'bloc', 'space', or a dict {group name: [spaces]} for groups built in the app.
    A group's value in a poll is the sum of the spaces that poll reported."""
    d = df.copy()
    # Denominator for valid votes: every party space the poll reported, grouped or not.
    party_total = df[df["is_party"]].groupby("poll_id")["value"].sum()
    if isinstance(level, dict):
        owner = {sp: name for name, members in level.items() for sp in members}
        d["_group"] = d["space"].map(owner)
        d = d.dropna(subset=["_group"])
        d["is_party"] = d["_group"].map(lambda g: not set(level[g]) <= set(df.loc[~df["is_party"], "space"]))
        level = "_group"
    if valid_votes:
        d = d[d["is_party"]]
    keys = ["poll_id", "date", "month_start", "pollster", level, "is_party"]
    g = d.groupby(keys, as_index=False).agg(value=("value", "sum"), sample_size=("sample_size", "mean"))
    if valid_votes:
        g["value"] = g["value"] / g["poll_id"].map(party_total) * 100
    return g.rename(columns={level: "group"})


def _weights(d: pd.DataFrame) -> pd.Series:
    fill = d["sample_size"].median() if d["sample_size"].notna().any() else DEFAULT_N
    return np.sqrt(d["sample_size"].fillna(fill))


def trend(pp: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Daily trend: a weighted average of nearby polls, smooth instead of step-like.

    Each poll's weight falls with its distance in days (Gaussian kernel whose width is
    `window_days`: about 95% of the weight comes from polls within +/- window/2), times the
    square root of its sample size. Each pollster counts once per day: its nearby polls are
    averaged first, so prolific pollsters do not dominate. Days with no poll within the
    window are left empty. Wide table: one column per group."""
    if pp.empty:
        return pd.DataFrame()
    d = pp.assign(w=_weights(pp))
    days = pd.date_range(d["date"].min(), d["date"].max(), freq="D")
    t = days.values.astype("datetime64[D]").astype(float)
    sd = window_days / 4
    out = {}
    for g, x in d.groupby("group"):
        num = np.zeros(len(days))
        den = np.zeros(len(days))
        for _, p in x.groupby("pollster"):
            dist = t[:, None] - p["date"].values.astype("datetime64[D]").astype(float)[None, :]
            k = np.exp(-0.5 * (dist / sd) ** 2)
            k[np.abs(dist) > window_days] = 0
            ksum = k.sum(axis=1)
            with np.errstate(invalid="ignore"):
                v = (k * p["value"].values).sum(axis=1) / ksum      # pollster's local average
            weight = k.max(axis=1) * p["w"].mean()                  # one vote per pollster
            ok = ksum > 0
            num[ok] += weight[ok] * v[ok]
            den[ok] += weight[ok]
        with np.errstate(invalid="ignore"):
            out[g] = np.where(den > 1e-3, num / den, np.nan)
    return pd.DataFrame(out, index=days)


def monthly(pp: pd.DataFrame) -> pd.DataFrame:
    """Monthly average per group across pollsters, weighted by sqrt(sample size).
    Missing sample sizes take the median of the others; with none reported, equal weights."""
    d = pp.assign(w=_weights(pp))
    by_firm = (d.groupby(["month_start", "group", "pollster"], as_index=False)
                 .agg(value=("value", "mean"), w=("w", "mean")))
    g = by_firm.groupby(["month_start", "group"])
    out = pd.DataFrame({
        "average": g.apply(lambda x: np.average(x["value"], weights=x["w"]), include_groups=False),
        "pollsters": g["pollster"].nunique(),
        "lowest": g["value"].min(),
        "highest": g["value"].max(),
    }).reset_index().rename(columns={"month_start": "date"})
    return out.sort_values(["date", "average"], ascending=[True, False])


def smooth(mo: pd.DataFrame, months: int = 3) -> pd.DataFrame:
    """Rolling average of the monthly averages over the last `months` calendar months."""
    w = mo.pivot(index="date", columns="group", values="average")
    w = w.reindex(pd.date_range(w.index.min(), w.index.max(), freq="MS"))
    return w.rolling(months, min_periods=1).mean()
