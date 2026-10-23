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
