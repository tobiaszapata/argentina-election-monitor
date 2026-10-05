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
