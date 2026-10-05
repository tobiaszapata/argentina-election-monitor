"""Shared helpers: file discovery and Excel export."""
import io
import re
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def latest_file(folder: str, patterns: tuple, content_key=None) -> tuple:
    """Return (path, warning). If several files exist, prefer the one with the most recent
    data according to `content_key(path)`; otherwise the one whose name has the most recent
    MM-YY or YYYY-MM tag; otherwise the last one alphabetically."""
    files = [f for p in patterns for f in (DATA_DIR / folder).glob(p)]
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
        warn = (f"Found {len(files)} files in data/{folder}; using {files[-1].name}. "
                "Delete the older ones to avoid confusion.")
    return files[-1], warn


def to_excel(sheets: dict, info: dict) -> bytes:
    """Build an .xlsx in memory: one sheet per dataframe plus an 'About' sheet."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            out = df.copy()
            for c in out.columns:
                if isinstance(out[c].dtype, pd.DatetimeTZDtype):
                    out[c] = out[c].dt.tz_localize(None)
                if isinstance(out[c].dtype, pd.CategoricalDtype):
                    out[c] = out[c].astype(str)
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
