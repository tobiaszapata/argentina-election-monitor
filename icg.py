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
