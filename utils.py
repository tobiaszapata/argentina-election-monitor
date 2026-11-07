"""Shared helpers: file discovery and Excel export."""
import io
import re
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
# Project root = the folder that holds app.py (utils.py may sit in monitor/ or next to app.py).
ROOT = _HERE if (_HERE / "app.py").exists() else _HERE.parent
DATA_DIR = ROOT / "data"


def find_file(folder: str, name: str):
    """data/<folder>/<name> if it exists, else <name> next to app.py, else None."""
    for f in (DATA_DIR / folder / name, ROOT / name):
        if f.exists():
            return f
    return None


def latest_file(folder: str, patterns: tuple, content_key=None, root_patterns: tuple = ()) -> tuple:
    """Return (path, warning). Looks in data/<folder>/ and, with `root_patterns`, also next to
    app.py (for repositories uploaded without folders). If several files exist, prefer the one
    with the most recent data according to `content_key(path)`; otherwise the one whose name
    has the most recent MM-YY or YYYY-MM tag; otherwise the last one alphabetically."""
    files = [f for p in patterns for f in (DATA_DIR / folder).glob(p)]
    files += [f for p in root_patterns for f in ROOT.glob(p) if f not in files]
    if not files:
        return None, None

    def key(f):
        if content_key is not None and len(files) > 1:
            try:
                return (content_key(f), f.name)
            except Exception:
                return (pd.Timestamp.min, f.name)
        return name_key(f)

    def name_key(f):
        m = re.search(r"(\d{4})[-_](\d{2})", f.stem)
        if m:
            return (int(m.group(1)), int(m.group(2)), f.name)
        m = re.search(r"(\d{2})[-_](\d{2})(?!\d)", f.stem)
        if m:
            return (2000 + int(m.group(2)), int(m.group(1)), f.name)
        return (0, 0, f.name)

    files = sorted(files, key=key)
    warn = None
    if len(files) > 1:
        warn = (f"Found {len(files)} {folder.upper()} files ({', '.join(f.name for f in files)}); "
                f"using {files[-1].name}. Delete the older ones to avoid confusion.")
    return files[-1], warn


def to_excel(sheets: dict, info: dict) -> bytes:
    """Build an .xlsx in memory: one sheet per dataframe plus an 'About' sheet."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            out = df.copy()
            out.attrs = {}
            for j in range(out.shape[1]):  # by position, so repeated column names are safe
                col = out.iloc[:, j]
                if isinstance(col.dtype, pd.DatetimeTZDtype):
                    out.isetitem(j, col.dt.tz_localize(None))
                elif isinstance(col.dtype, pd.CategoricalDtype):
                    out.isetitem(j, col.astype(str))
            out.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            for col in ws.columns:
                width = max(len(str(col[0].value or "")), 10)
                ws.column_dimensions[col[0].column_letter].width = min(width + 2, 45)
        pd.DataFrame(list(info.items()), columns=["Field", "Value"]).to_excel(
            xw, sheet_name="About", index=False)
        xw.sheets["About"].column_dimensions["A"].width = 18
        xw.sheets["About"].column_dimensions["B"].width = 110
    return buf.getvalue()


def sheet_csv_url(url: str) -> str:
    """Turn the usual Google Sheets links into a CSV download link for the same tab:
    published web page (pubhtml) -> published CSV; editor link (/edit#gid=..) -> CSV export."""
    url = str(url).strip()
    if "docs.google.com/spreadsheets" not in url:
        return url
    gid = re.search(r"[#?&]gid=(\d+)", url)
    if "/d/e/" in url:  # published link
        base = url.split("?")[0].split("#")[0].replace("/pubhtml", "/pub")
        q = "output=csv" + (f"&single=true&gid={gid.group(1)}" if gid else "")
        return f"{base}?{q}"
    doc = re.search(r"/spreadsheets/d/([\w-]+)", url)
    if doc and "output=csv" not in url and "format=csv" not in url:
        return (f"https://docs.google.com/spreadsheets/d/{doc.group(1)}/export?format=csv"
                + (f"&gid={gid.group(1)}" if gid else ""))
    return url


def read_sheet(src, name: str = "sheet", expected: list | None = None) -> tuple:
    """Read a published Google Sheets CSV (or a local CSV) tolerantly.

    Columns are the ones with a header in row 1. Cells written to the right of the last header
    (rows longer than the header) are ignored instead of breaking the read; the returned notes
    say in which sheet rows that happened. When `expected` columns are missing from row 1, it
    raises an error that shows what row 1 holds (usually: the link points to another tab).
    Returns (DataFrame of strings, notes)."""
    import csv

    if str(src).startswith(("http://", "https://")):
        import requests
        r = requests.get(sheet_csv_url(src), timeout=20)
        if "accounts.google.com" in r.url or "ServiceLogin" in r.url:
            raise ValueError(f"the {name} link asks for a Google sign-in, so the tab is not published. "
                             "In Google Sheets use File > Share > Publish to web, pick the tab and "
                             "'Comma-separated values (.csv)', and paste that link in the app's secrets.")
        r.raise_for_status()
        r.encoding = "utf-8"
        text = r.text
    else:
        text = Path(src).read_text(encoding="utf-8-sig")
    text = text.lstrip("\ufeff")
    if text.lstrip()[:1] == "<":
        raise ValueError(f"the {name} link returns a web page, not CSV. In Google Sheets use File > Share > "
                         "Publish to web, pick the tab (not 'Entire document') and 'Comma-separated values "
                         "(.csv)', then paste that link in the app's secrets.")
    rows = list(csv.reader(io.StringIO(text)))
    notes = []
    if not rows:
        return pd.DataFrame(columns=expected or []), notes
    header = [h.strip() for h in rows[0]]
    while header and not header[-1]:
        header.pop()
    if expected:
        have = {re.sub(r"\s+", "_", h.lower()) for h in header}
        missing = [c for c in expected if c not in have]
        if missing and len(rows) > 1:
            shown = ", ".join(h for h in header if h)[:120] or "nothing"
            raise ValueError(f"the {name} link does not point to the right tab: row 1 should have the "
                             f"columns {', '.join(expected)}, but it has: {shown}. Publish that tab on its "
                             "own (File > Share > Publish to web) and use its link.")
    width = len(header)
    extra = [i + 1 for i, r in enumerate(rows[1:], start=1) if any(c.strip() for c in r[width:])]
    if extra:
        notes.append(f"The {name} has cells to the right of its last column (sheet rows "
                     + ", ".join(str(i) for i in extra[:12]) + (" ..." if len(extra) > 12 else "")
                     + "); they were ignored. Delete them or move them to another tab.")
    body = [(r + [""] * width)[:width] for r in rows[1:]]
    return pd.DataFrame(body, columns=header, dtype=str), notes
