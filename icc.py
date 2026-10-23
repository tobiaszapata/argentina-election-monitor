"""Consumer Confidence Index (ICC, UTDT-CIF): parses the official 'Serie_ICC' workbook.

The workbook has a sheet by region and a sheet by sub-index. Some date cells are text
(e.g. 'Dic-07', 'abr.-22'), so dates are rebuilt as consecutive months from the first
valid date and cross-checked against every readable date.
"""
import datetime as dt
import re

import pandas as pd

REGION_KEYS = [("capital", "CABA"), ("gba", "GBA"), ("interior", "Interior"), ("nacional", "National")]
COMPONENT_KEYS = [("personal", "Personal situation"), ("macro", "Macroeconomic situation"),
                  ("durables", "Durable goods & real estate"), ("nacional", "National")]
MONTHS_ES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
             "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}


def _parse_date(v):
    if isinstance(v, (dt.datetime, pd.Timestamp)) and v.year > 1990:
        return pd.Timestamp(v.year, v.month, 1)
    m = re.match(r"([a-záéíóú]{3})\.?[-/ ](\d{2,4})", str(v).strip().lower())
    if m and m.group(1) in MONTHS_ES:
        y = int(m.group(2))
        return pd.Timestamp(y + 2000 if y < 100 else y, MONTHS_ES[m.group(1)], 1)
    return None


def _read_sheet(raw: pd.DataFrame, keys) -> pd.DataFrame:
    header = raw.iloc[:3].fillna("").astype(str).agg(" ".join).str.lower()
    cols = {}
    for j in range(1, raw.shape[1]):
        h = header[j]
        if "variaci" in h:
            continue
        for k, name in keys:
            if k in h.replace("bienes durables", "durables") and name not in cols.values():
                cols[j] = name
                break
    body = raw.iloc[3:]
    body = body[body[0].notna()].reset_index(drop=True)
    parsed = [_parse_date(v) for v in body[0]]
    first = next(i for i, d in enumerate(parsed) if d is not None)
    dates = pd.date_range(parsed[first] - pd.DateOffset(months=first), periods=len(body), freq="MS")
    if any(p is not None and p != d for p, d in zip(parsed, dates)):
        dates = pd.to_datetime(parsed)  # rows not consecutive: trust each cell
    out = pd.DataFrame({"date": dates})
    for j, name in cols.items():
        out[name] = pd.to_numeric(body[j], errors="coerce").values
    return out.dropna(subset=["date"])


def load(path) -> dict:
    sheets = pd.read_excel(path, sheet_name=None, header=None)
    out = {}
    for name, raw in sheets.items():
        n = name.lower()
        if "regi" in n:
            out["Region"] = _read_sheet(raw, REGION_KEYS)
        elif "sub" in n:
            out["Components"] = _read_sheet(raw, COMPONENT_KEYS)
    return out


def national(icc: dict) -> pd.DataFrame:
    base = icc.get("Region", icc.get("Components"))
    return base[["date", "National"]].dropna().rename(columns={"National": "ICC"})


def breakdown(icc: dict, dimension: str) -> pd.DataFrame:
    t = icc[dimension]
    preferred = ["CABA", "GBA", "Interior"]
    cats = [c for c in t.columns if c not in ("date", "National")]
    cats = [c for c in preferred if c in cats] + [c for c in cats if c not in preferred]
    long = t.melt(id_vars="date", value_vars=cats, var_name="category", value_name="ICC").dropna()
    long["category"] = pd.Categorical(long["category"], categories=cats, ordered=True)
    return long.sort_values(["date", "category"])


def dimensions(icc: dict) -> list:
    return [d for d in ("Region", "Components") if d in icc]


# ------------------------------------------------------------------ monthly sheet (Google Sheets)
# One row per month, typed from UTDT's monthly ICC report. Header -> (dimension, category).
SHEET_COLUMNS = [
    ("ICC", "National"),
    ("CABA", "CABA"),
    ("GBA", "GBA"),
    ("Interior", "Interior"),
    ("Situacion personal", "Personal situation"),
    ("Situacion macro", "Macroeconomic situation"),
    ("Bienes durables", "Durable goods & real estate"),
]
SHEET_HEADERS = ["year", "month"] + [h for h, _ in SHEET_COLUMNS]
_REGIONS = ["CABA", "GBA", "Interior"]
_COMPONENTS = ["Personal situation", "Macroeconomic situation", "Durable goods & real estate"]


def _key(s) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def sheet_template(icc: dict) -> pd.DataFrame:
    """One row per month from the official workbook (dict returned by load)."""
    r = icc["Region"].set_index("date")
    c = icc.get("Components", pd.DataFrame(columns=["date"])).set_index("date")
    dates = r.index.union(c.index)
    out = pd.DataFrame(index=dates)
    out["ICC"] = r["National"].reindex(dates).fillna(c.get("National", pd.Series(dtype=float)).reindex(dates))
    for h, cat in SHEET_COLUMNS[1:]:
        src = r if cat in _REGIONS else c
        out[h] = src[cat].reindex(dates) if cat in src else None
    out = out.round(2).reset_index(names="date")
    out.insert(0, "month", out["date"].dt.month)
    out.insert(0, "year", out["date"].dt.year)
    return out.drop(columns="date")


def from_sheet(raw: pd.DataFrame) -> tuple:
    """Read the monthly ICC sheet. Returns (dict like load(), problems)."""
    problems = []
    cols = {_key(c): c for c in raw.columns}
    missing = [h for h in ["year", "month", "ICC"] if _key(h) not in cols]
    if missing:
        raise ValueError("Missing columns in the ICC sheet: " + ", ".join(missing))

    def num(s):
        s = s.astype(str).str.strip().str.replace(",", ".", regex=False)
        return pd.to_numeric(s.replace({"": None, "nan": None, "None": None, "-": None}), errors="coerce")

    df = pd.DataFrame({h: num(raw[cols[_key(h)]]) if _key(h) in cols else pd.Series(dtype=float, index=raw.index)
                       for h in SHEET_HEADERS}).dropna(how="all")
    bad = df[["year", "month", "ICC"]].isna().any(axis=1) | ~df["month"].between(1, 12)
    if bad.any():
        problems.append(f"{int(bad.sum())} row(s) of the ICC sheet skipped: missing year, month or ICC "
                        "(sheet rows: " + ", ".join(str(i + 2) for i in df.index[bad][:12]) + ").")
    df = df[~bad].copy()
    df["date"] = pd.to_datetime(dict(year=df["year"].astype(int), month=df["month"].astype(int), day=1))
    dup = df["date"].duplicated(keep="last")
    if dup.any():
        problems.append("Some months appear twice in the ICC sheet; the lower row was used for: "
                        + ", ".join(f"{d:%b %Y}" for d in df.loc[dup, "date"]) + ".")
        df = df[~dup]
    df = df.sort_values("date").reset_index(drop=True)
    vals = df[[h for h, _ in SHEET_COLUMNS]]
    off = ((vals < 0) | (vals > 100)).any(axis=1)
    if off.any():
        problems.append("Values outside 0-100 in the ICC sheet (check for typos): "
                        + ", ".join(f"{d:%b %Y}" for d in df.loc[off, "date"]) + ".")
    comp = [h for h, cat in SHEET_COLUMNS if cat in _COMPONENTS]
    full = df[comp].notna().all(axis=1)
    gap = (df.loc[full, comp].mean(axis=1) - df.loc[full, "ICC"]).abs()
    if (gap > 0.3).any():
        problems.append("In these months the ICC is not the average of its three components, "
                        "which usually means a typo: "
                        + ", ".join(f"{d:%b %Y}" for d in df.loc[gap[gap > 0.3].index, "date"]) + ".")
    named = df.rename(columns={h: cat for h, cat in SHEET_COLUMNS})
    out = {"Region": named[["date", *_REGIONS, "National"]].copy()}
    if named[_COMPONENTS].notna().any().any():
        out["Components"] = named[["date", *_COMPONENTS, "National"]].copy()
    return out, problems
