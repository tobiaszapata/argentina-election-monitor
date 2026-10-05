"""Poll tracker: reads the polls sheet (Google Sheets CSV or data/polls/polls.csv).

One row per poll x candidate. Columns:
  pollster, fieldwork_start, fieldwork_end, sample_size, method, coverage,
  scenario, candidate, value, source_url
Rows whose candidate is Undecided / Blank / Other are kept for the "valid votes" option
but are not plotted as candidates.
"""
import re

import numpy as np
import pandas as pd

COLUMNS = ["pollster", "fieldwork_start", "fieldwork_end", "sample_size", "method",
           "coverage", "scenario", "candidate", "value", "source_url"]
REQUIRED = ["pollster", "scenario", "candidate", "value"]
NON_CANDIDATE = {
    "undecided": "Undecided", "indecisos": "Undecided", "ns/nc": "Undecided", "nsnc": "Undecided",
    "don't know": "Undecided", "blank": "Blank", "blanco": "Blank", "en blanco": "Blank",
    "none": "Blank", "ninguno": "Blank", "other": "Other", "others": "Other", "otros": "Other",
}
EXCLUDE_FOR_VALID = {"Undecided", "Blank"}
BANDWIDTH_DAYS = 14        # sd of the Gaussian kernel (~3-4 weeks of effective window)
POLLSTER_WINDOW_DAYS = 30  # a pollster's polls within this window share one vote
DEFAULT_N = 1000           # used when no poll in the scenario reports its sample size


def _num(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip().str.replace("%", "", regex=False)
    # "1.234" (thousands) vs "42,5" (decimal comma)
    s = s.str.replace(r"(?<=\d)\.(?=\d{3}(\D|$))", "", regex=True).str.replace(",", ".", regex=False)
    return pd.to_numeric(s.replace({"": None, "nan": None, "None": None}), errors="coerce")


def _date(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip().replace({"": None, "nan": None, "None": None, "NaT": None})
    iso = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    other = pd.to_datetime(s, dayfirst=True, errors="coerce", format="mixed")
    return iso.fillna(other)


def clean(raw: pd.DataFrame) -> tuple:
    """Return (clean dataframe, list of problems found)."""
    problems = []
    df = raw.copy()
    df.columns = [re.sub(r"\s+", "_", str(c).strip().lower()) for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        return pd.DataFrame(columns=COLUMNS + ["date", "kind"]), [f"Missing columns: {', '.join(missing)}"]
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[COLUMNS].dropna(how="all")
    for c in ("pollster", "method", "coverage", "scenario", "candidate", "source_url"):
        df[c] = df[c].astype(str).str.strip().replace({"nan": None, "None": None, "": None})
    df["value"] = _num(df["value"])
    df["sample_size"] = _num(df["sample_size"])
    df["fieldwork_start"] = _date(df["fieldwork_start"])
    df["fieldwork_end"] = _date(df["fieldwork_end"])
    if df["value"].notna().any() and df["value"].max() <= 1:
        df["value"] *= 100  # typed as fractions
    df["date"] = df["fieldwork_start"] + (df["fieldwork_end"] - df["fieldwork_start"]) / 2
    df["date"] = df["date"].fillna(df["fieldwork_end"]).fillna(df["fieldwork_start"]).dt.normalize()
    bad = df[REQUIRED + ["date"]].isna().any(axis=1)
    if bad.any():
        problems.append(f"{int(bad.sum())} row(s) skipped: missing pollster, scenario, candidate, "
                        "value or fieldwork date (sheet rows: "
                        + ", ".join(str(i + 2) for i in df.index[bad][:10]) + ").")
    df = df[~bad].copy()
    df["kind"] = df["candidate"].str.lower().map(NON_CANDIDATE).fillna("Candidate")
    df.loc[df["kind"] != "Candidate", "candidate"] = df["kind"]
    df["poll_id"] = df["pollster"] + " | " + df["date"].dt.strftime("%Y-%m-%d") + " | " + df["scenario"]
    return df.reset_index(drop=True), problems


def to_valid_votes(df: pd.DataFrame) -> pd.DataFrame:
    """Rescale each poll so that candidates (+ Other) add to 100, dropping undecided/blank."""
    d = df[~df["candidate"].isin(EXCLUDE_FOR_VALID)].copy()
    d["value"] = d["value"] / d.groupby("poll_id")["value"].transform("sum") * 100
    return d


def _weights(polls: pd.DataFrame) -> pd.Series:
    """sqrt(sample size), divided by how many polls the same pollster has nearby."""
    n = polls["sample_size"].copy()
    fill = n.median() if n.notna().any() else DEFAULT_N
    base = np.sqrt(n.fillna(fill))
    crowd = []
    for _, r in polls.iterrows():
        same = polls[(polls["pollster"] == r["pollster"])
                     & ((polls["date"] - r["date"]).abs().dt.days <= POLLSTER_WINDOW_DAYS)]
        crowd.append(len(same))
    return base / np.array(crowd)


def trend(df: pd.DataFrame) -> pd.DataFrame:
    """Kernel-weighted local average per candidate, with an approximate 95% band."""
    out = []
    polls = df.drop_duplicates("poll_id")[["poll_id", "pollster", "date", "sample_size"]].copy()
    polls["w"] = _weights(polls).values
    d = df[df["kind"] == "Candidate"].merge(polls[["poll_id", "w"]], on="poll_id")
    for cand, g in d.groupby("candidate"):
        if g["poll_id"].nunique() < 3:
            continue
        days = pd.date_range(g["date"].min(), g["date"].max(), freq="D")
        t = (days.values[:, None] - g["date"].values[None, :]) / np.timedelta64(1, "D")
        k = np.exp(-0.5 * (t / BANDWIDTH_DAYS) ** 2)
        w = k * g["w"].values[None, :]
        mu = (w * g["value"].values).sum(1) / w.sum(1)
        resid2 = (g["value"].values[None, :] - mu[:, None]) ** 2
        sd = np.sqrt((w * resid2).sum(1) / w.sum(1))
        n_eff = w.sum(1) ** 2 / (w ** 2).sum(1)
        se = sd / np.sqrt(np.maximum(n_eff - 1, 1))
        mu[k.sum(1) < 0.3] = np.nan  # no poll within ~3 weeks: don't draw through the gap
        out.append(pd.DataFrame({"date": days, "candidate": cand, "trend": mu,
                                 "low": mu - 1.96 * se, "high": mu + 1.96 * se}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["date", "candidate", "trend", "low", "high"])


def monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly average per candidate. Each pollster counts once per month (its polls are
    averaged first); pollsters are weighted by sqrt(sample size), using the scenario's
    median sample size when a pollster did not report it."""
    d = df.copy()
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    fill = d["sample_size"].median() if d["sample_size"].notna().any() else DEFAULT_N
    d["sample_size"] = d["sample_size"].fillna(fill)
    per = (d.groupby(["month", "candidate", "pollster"], as_index=False)
             .agg(value=("value", "mean"), n=("sample_size", "mean"), polls=("poll_id", "nunique")))
    per["w"] = np.sqrt(per["n"])
    g = per.groupby(["month", "candidate"])
    res = pd.DataFrame({
        "value": g.apply(lambda x: np.average(x["value"], weights=x["w"]), include_groups=False),
        "pollsters": g["pollster"].nunique(),
        "polls": g["polls"].sum(),
        "min": g["value"].min(),
        "max": g["value"].max(),
    }).reset_index()
    return res
