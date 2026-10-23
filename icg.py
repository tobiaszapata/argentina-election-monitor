"""Government Confidence Index (ICG, UTDT): microdata -> monthly series.

All means use the official weight `ponderacion_UTDT`, which reproduces the published
figures. Each respondent's ICG is the mean of five items recoded 0/5.
"""
import numpy as np
import pandas as pd

W = "ponderacion_UTDT"

COMPONENTS = {
    "eval_gob_rec": "Overall assessment of the government",
    "benef_gob_rec": "Concern for the general interest",
    "adm_gp_rec": "Efficiency in public spending",
    "cor_gob_rec": "Honesty of government officials",
    "resol_prob_rec": "Capacity to solve the country's problems",
}

# app label -> (variable in the .dta, {original value label: English label})
DIMENSIONS = {
    "Region": ("Zona", {"CABA": "CABA", "BsAs": "GBA", "Interior": "Interior"}),
    "Gender": ("sexo", {"Hombre": "Men", "Mujer": "Women"}),
    "Age": ("edad", {"18a29": "18-29", "30a49": "30-49", "más50": "50+"}),
    "Education": ("edu", {"Primario": "Primary", "Secundario": "Secondary",
                          "TercOUniv": "Tertiary / university"}),
    "Economic outlook": ("sit_ec", {"Mejor": "Better", "Igual": "Same",
                                    "Peor": "Worse", "NSNC": "Don't know"}),
}

# Inauguration dates. Fieldwork runs in the first days of each month, so the December
# wave of a transition year belongs to the outgoing president.
PRESIDENTS = [
    ("1999-12-10", "De la Rúa"),
    ("2002-01-01", "Duhalde"),
    ("2003-05-25", "N. Kirchner"),
    ("2007-12-10", "Fernández de Kirchner I"),
    ("2011-12-10", "Fernández de Kirchner II"),
    ("2015-12-10", "Macri"),
    ("2019-12-10", "A. Fernández"),
    ("2023-12-10", "Milei"),
]


def load(path) -> pd.DataFrame:
    reader = pd.io.stata.StataReader(path)
    df = reader.read(convert_categoricals=False)
    labels = reader.value_labels()
    lbl_of = dict(zip(df.columns, reader._lbllist))
    for dim, (var, mapping) in DIMENSIONS.items():
        codes = {int(k): v for k, v in labels.get(lbl_of.get(var), {}).items()}
        df[dim] = df[var].map(lambda x: mapping.get(codes.get(int(x))) if pd.notna(x) else None)
    df["date"] = pd.to_datetime(df["ola"])
    return df[["date", W, "ICG", *COMPONENTS, *DIMENSIONS]]


def _weighted(d: pd.DataFrame, by: list, cols=("ICG",)) -> pd.DataFrame:
    d = d.dropna(subset=by).copy()
    d["_w2"] = d[W] ** 2
    for c in cols:
        d["_wx_" + c] = d[W] * d[c]
    d["_wxx"] = d[W] * d["ICG"] ** 2
    g = d.groupby(by, observed=True)
    s = g[[W, "_w2", "_wxx"] + ["_wx_" + c for c in cols]].sum()
    out = pd.DataFrame(index=s.index)
    for c in cols:
        out[c] = s["_wx_" + c] / s[W]
    var = (s["_wxx"] / s[W] - out["ICG"] ** 2).clip(lower=0)
    se = np.sqrt(var / (s[W] ** 2 / s["_w2"]))  # Kish effective sample size
    out["ci_low"], out["ci_high"] = out["ICG"] - 1.96 * se, out["ICG"] + 1.96 * se
    out["n"] = g.size()
    return out.reset_index()


def presidents_for(dates) -> pd.DataFrame:
    cut = pd.to_datetime([p[0] for p in PRESIDENTS])
    names = [p[1] for p in PRESIDENTS]
    start = {n: (d if d.day == 1 else d + pd.offsets.MonthBegin(1)) for d, n in zip(cut, names)}
    pres = [names[cut.searchsorted(d, side="right") - 1] for d in dates]
    month = [(d.year - start[p].year) * 12 + d.month - start[p].month + 1 for d, p in zip(dates, pres)]
    return pd.DataFrame({"date": list(dates), "president": pres, "month_in_office": month})


def national(df) -> pd.DataFrame:
    t = _weighted(df, ["date"], cols=("ICG", *COMPONENTS)).rename(columns=COMPONENTS)
    t = t.merge(presidents_for(t["date"]), on="date")
    t["monthly_change_%"] = t["ICG"].pct_change() * 100
    return t


def by_presidency(nat: pd.DataFrame) -> pd.DataFrame:
    order = [p[1] for p in PRESIDENTS]
    w = nat.pivot(index="month_in_office", columns="president", values="ICG")
    return w[[p for p in order if p in w.columns]].reset_index()


def category_order(dimension: str) -> list:
    if dimension == "Components":
        return list(COMPONENTS.values())
    return list(DIMENSIONS[dimension][1].values())


def breakdown(df, nat, dimension: str) -> pd.DataFrame:
    """Long table: date, category, ICG, ci_low, ci_high, n."""
    if dimension == "Components":
        t = nat[["date", "n", *COMPONENTS.values()]].melt(
            id_vars=["date", "n"], var_name="category", value_name="ICG")
        t["ci_low"] = t["ci_high"] = np.nan
    else:
        t = _weighted(df, ["date", dimension]).rename(columns={dimension: "category"})
    t["category"] = pd.Categorical(t["category"], categories=category_order(dimension), ordered=True)
    return t.sort_values(["date", "category"])[["date", "category", "ICG", "ci_low", "ci_high", "n"]]


# ------------------------------------------------------------------ monthly sheet (Google Sheets)
# One row per month, typed from UTDT's monthly report. Header -> (dimension, category).
SHEET_COLUMNS = [
    ("ICG", "Total", "ICG"),
    ("Evaluacion general", "Components", COMPONENTS["eval_gob_rec"]),
    ("Interes general", "Components", COMPONENTS["benef_gob_rec"]),
    ("Eficiencia", "Components", COMPONENTS["adm_gp_rec"]),
    ("Honestidad", "Components", COMPONENTS["cor_gob_rec"]),
    ("Capacidad", "Components", COMPONENTS["resol_prob_rec"]),
    ("CABA", "Region", "CABA"),
    ("GBA", "Region", "GBA"),
    ("Interior", "Region", "Interior"),
    ("Varones", "Gender", "Men"),
    ("Mujeres", "Gender", "Women"),
    ("18-29", "Age", "18-29"),
    ("30-49", "Age", "30-49"),
    ("50+", "Age", "50+"),
    ("Primario", "Education", "Primary"),
    ("Secundario", "Education", "Secondary"),
    ("Terciario/Univ", "Education", "Tertiary / university"),
    ("Exp. Mejor", "Economic outlook", "Better"),
    ("Exp. Igual", "Economic outlook", "Same"),
    ("Exp. Peor", "Economic outlook", "Worse"),
    ("Exp. NS/NC", "Economic outlook", "Don't know"),
]
SHEET_HEADERS = ["year", "month"] + [h for h, _, _ in SHEET_COLUMNS]


def _key(s) -> str:
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9+/]", "", s)


def _num(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip().str.replace(",", ".", regex=False)
    return pd.to_numeric(s.replace({"": None, "nan": None, "None": None, "-": None}), errors="coerce")


def sheet_template(nat: pd.DataFrame, parts: dict) -> pd.DataFrame:
    """Build the sheet (one row per month) from series computed from the microdata."""
    out = pd.DataFrame({"date": nat["date"]})
    for header, dim, cat in SHEET_COLUMNS:
        if dim == "Total":
            s = nat.set_index("date")["ICG"]
        elif dim == "Components":
            s = nat.set_index("date")[cat]
        else:
            t = parts[dim]
            s = t[t["category"].astype(str) == cat].set_index("date")["ICG"]
        out[header] = out["date"].map(s).round(2)
    out.insert(0, "month", out["date"].dt.month)
    out.insert(0, "year", out["date"].dt.year)
    return out.drop(columns="date")


def from_sheet(raw: pd.DataFrame) -> tuple:
    """Read the monthly sheet. Returns (national table, {dimension: long table}, problems)."""
    problems = []
    cols = {_key(c): c for c in raw.columns}
    missing = [h for h in ["year", "month", "ICG"] if _key(h) not in cols]
    if missing:
        raise ValueError("Missing columns in the ICG sheet: " + ", ".join(missing))
    df = pd.DataFrame({h: _num(raw[cols[_key(h)]]) if _key(h) in cols else pd.Series(dtype=float, index=raw.index)
                       for h in SHEET_HEADERS})
    df = df.dropna(how="all")
    bad = df[["year", "month", "ICG"]].isna().any(axis=1) | ~df["month"].between(1, 12)
    if bad.any():
        problems.append(f"{int(bad.sum())} row(s) of the ICG sheet skipped: missing year, month or ICG "
                        "(sheet rows: " + ", ".join(str(i + 2) for i in df.index[bad][:12]) + ").")
    df = df[~bad].copy()
    df["date"] = pd.to_datetime(dict(year=df["year"].astype(int), month=df["month"].astype(int), day=1))
    dup = df["date"].duplicated(keep="last")
    if dup.any():
        problems.append("Some months appear twice in the ICG sheet; the lower row was used for: "
                        + ", ".join(f"{d:%b %Y}" for d in df.loc[dup, "date"]) + ".")
        df = df[~df["date"].duplicated(keep="last")]
    df = df.sort_values("date").reset_index(drop=True)

    values = df[[h for h, _, _ in SHEET_COLUMNS]]
    out_of_range = (values < 0) | (values > 5)
    if out_of_range.any().any():
        rows = [f"{d:%b %Y}" for d in df.loc[out_of_range.any(axis=1), "date"]]
        problems.append("Values outside 0-5 in the ICG sheet (check for typos): " + ", ".join(rows) + ".")
    comp = [h for h, dim, _ in SHEET_COLUMNS if dim == "Components"]
    full = df[comp].notna().all(axis=1)
    gap = (df.loc[full, comp].mean(axis=1) - df.loc[full, "ICG"]).abs()
    off = gap[gap > 0.03]
    if len(off):
        problems.append("In these months the ICG is not the average of its five components, "
                        "which usually means a typo: " + ", ".join(f"{d:%b %Y}" for d in df.loc[off.index, "date"]) + ".")

    nat = df[["date", "ICG"]].copy()
    for h, dim, cat in SHEET_COLUMNS:
        if dim == "Components":
            nat[cat] = df[h]
    nat["ci_low"] = nat["ci_high"] = nat["n"] = np.nan
    nat = nat.merge(presidents_for(nat["date"]), on="date")
    nat["monthly_change_%"] = nat["ICG"].pct_change() * 100

    parts = {}
    for dim in [*DIMENSIONS, "Components"]:
        heads = [(h, cat) for h, d, cat in SHEET_COLUMNS if d == dim]
        t = pd.concat([pd.DataFrame({"date": df["date"], "category": cat, "ICG": df[h]}) for h, cat in heads])
        t = t.dropna(subset=["ICG"])
        t["ci_low"] = t["ci_high"] = t["n"] = np.nan
        t["category"] = pd.Categorical(t["category"], categories=category_order(dim), ordered=True)
        parts[dim] = t.sort_values(["date", "category"]).reset_index(drop=True)
    return nat, parts, problems
