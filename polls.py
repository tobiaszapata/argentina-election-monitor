"""Poll tracker: national vote intention by political space, one value per month.

Polls sheet (one row per space reported by a poll):
    year, month, pollster, space, value, sample_size, source_url
Spaces sheet (which bloc each space belongs to):
    space, bloc

Rule for alliances: spaces can be added up into a bloc, never split. A poll that
reports "LLA + PRO" together counts for the bloc, but not for LLA or PRO alone.
"""
import re

import numpy as np
import pandas as pd

COLUMNS = ["year", "month", "pollster", "space", "value", "sample_size", "source_url"]
REQUIRED = ["year", "month", "pollster", "space", "value"]
SPACE_COLUMNS = ["space", "bloc"]
DEFAULT_N = 1000

# Used when the spaces sheet is not connected. Edit the sheet, not this list.
DEFAULT_SPACES = [
    ("LLA", "LLA + PRO"),
    ("PRO", "LLA + PRO"),
    ("LLA + PRO", "LLA + PRO"),
    ("Fuerza Patria", "Peronism"),
    ("Provincias Unidas", "Provincias Unidas"),
    ("UCR", "Others"),
    ("FIT-U", "Left (FIT-U)"),
    ("Otros", "Others"),
    ("Blanco", "Blank"),
    ("Indecisos", "Undecided"),
]
# Blocs that are not parties: left out of "% of valid votes" and not drawn as lines.
NON_PARTY = {"blank": "Blank", "blanco": "Blank", "en blanco": "Blank",
             "undecided": "Undecided", "indecisos": "Undecided", "ns/nc": "Undecided",
             "no sabe": "Undecided"}
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
    df["date"] = pd.to_datetime(dict(year=df["year_n"].astype(int), month=df["month_n"].astype(int), day=1))

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
    df["is_party"] = ~df["bloc"].isin(["Blank", "Undecided"])
    df["poll_id"] = df["pollster"] + " | " + df["date"].dt.strftime("%Y-%m")
    # Same pollster, month and space loaded twice (e.g. two polls that month): average them.
    dup = df.duplicated(["poll_id", "space"], keep=False)
    if dup.any():
        problems.append(f"{df.loc[dup, 'poll_id'].nunique()} pollster(s) have the same space loaded "
                        "twice in one month; those values were averaged.")
        df = (df.groupby(["poll_id", "date", "pollster", "space", "bloc", "is_party"], as_index=False)
                .agg(value=("value", "mean"), sample_size=("sample_size", "mean"),
                     source_url=("source_url", "first")))
    return df.reset_index(drop=True), spaces, problems


def per_poll(df: pd.DataFrame, level: str, valid_votes: bool) -> pd.DataFrame:
    """One value per poll and group. level: 'bloc' or 'space'."""
    d = df[df["is_party"]] if valid_votes else df
    g = (d.groupby(["poll_id", "date", "pollster", level, "is_party"], as_index=False)
           .agg(value=("value", "sum"), sample_size=("sample_size", "mean")))
    if valid_votes:
        g["value"] = g["value"] / g.groupby("poll_id")["value"].transform("sum") * 100
    return g.rename(columns={level: "group"})


def monthly(pp: pd.DataFrame) -> pd.DataFrame:
    """Monthly average per group across pollsters, weighted by sqrt(sample size).
    Missing sample sizes take the median of the others; with none reported, equal weights."""
    d = pp.copy()
    fill = d["sample_size"].median() if d["sample_size"].notna().any() else DEFAULT_N
    d["w"] = np.sqrt(d["sample_size"].fillna(fill))
    g = d.groupby(["date", "group"])
    out = pd.DataFrame({
        "average": g.apply(lambda x: np.average(x["value"], weights=x["w"]), include_groups=False),
        "pollsters": g["pollster"].nunique(),
        "lowest": g["value"].min(),
        "highest": g["value"].max(),
    }).reset_index()
    return out.sort_values(["date", "average"], ascending=[True, False])


def smooth(mo: pd.DataFrame, months: int = 3) -> pd.DataFrame:
    """Rolling average of the monthly averages over the last `months` calendar months."""
    w = mo.pivot(index="date", columns="group", values="average")
    w = w.reindex(pd.date_range(w.index.min(), w.index.max(), freq="MS"))
    return w.rolling(months, min_periods=1).mean()
