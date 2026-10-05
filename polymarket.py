"""Polymarket: 'Argentina Presidential Election Winner' market (public APIs, no key needed).

Each candidate is a Yes/No market; the Yes price (0-1 USD) reads as a probability.
Prices across candidates add up to slightly more than 100%, so the app shows them
normalized to sum 100% and reports the raw sum.
"""
import json
import time

import pandas as pd
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
EVENT_SLUG = "argentina-presidential-election-winner"
EVENT_URL = f"https://polymarket.com/event/{EVENT_SLUG}"
TIMEOUT = 15

# Used only for default groupings in the matchup builder (matched by surname).
PERONIST_SURNAMES = ["kicillof", "massa", "grabois", "kirchner", "quintela", "jaldo",
                     "mayans", "guillermo moreno", "uñac", "zamora", "kicilof"]


def _json_field(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return []
    return v or []


def _name(m: dict) -> str:
    if m.get("groupItemTitle"):
        return m["groupItemTitle"].strip()
    q = m.get("question", "")
    if q.lower().startswith("will ") and " win" in q:
        return q[5:q.index(" win")].strip()
    return q


def fetch_current() -> pd.DataFrame:
    """One row per candidate with price, bid/ask, volume, liquidity and the Yes token id."""
    r = requests.get(f"{GAMMA}/events", params={"slug": EVENT_SLUG}, timeout=TIMEOUT)
    r.raise_for_status()
    events = r.json()
    if not events:
        raise RuntimeError(f"Event '{EVENT_SLUG}' not found on Polymarket.")
    rows = []
    for m in events[0].get("markets", []):
        if m.get("closed"):
            continue
        prices = [float(p) for p in _json_field(m.get("outcomePrices"))] or [None]
        tokens = _json_field(m.get("clobTokenIds"))
        bid, ask = m.get("bestBid"), m.get("bestAsk")
        last = m.get("lastTradePrice")
        if bid is not None and ask is not None and 0 < float(ask) - float(bid) <= 0.10:
            price = (float(bid) + float(ask)) / 2
        elif last not in (None, 0):
            price = float(last)
        else:
            price = prices[0]
        rows.append({
            "candidate": _name(m),
            "price": price,
            "best_bid": float(bid) if bid is not None else None,
            "best_ask": float(ask) if ask is not None else None,
            "volume_usd": float(m.get("volumeNum") or m.get("volume") or 0),
            "liquidity_usd": float(m.get("liquidityNum") or m.get("liquidity") or 0),
            "token_yes": tokens[0] if tokens else None,
        })
    df = pd.DataFrame(rows).dropna(subset=["price"])
    total = df["price"].sum()
    df["probability"] = df["price"] / total * 100 if total else None
    df.attrs["raw_sum"] = total
    df.attrs["fetched_at"] = pd.Timestamp.now(tz="UTC")
    return df.sort_values("probability", ascending=False).reset_index(drop=True)


def _history_one(token: str) -> pd.DataFrame:
    params = {"market": token, "interval": "max", "fidelity": 1440}
    r = requests.get(f"{CLOB}/prices-history", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    hist = r.json().get("history", [])
    if not hist:  # some markets only answer with explicit time windows
        end, hist = int(time.time()), []
        for _ in range(24):  # up to ~2 years in 30-day chunks
            start = end - 30 * 86400
            rr = requests.get(f"{CLOB}/prices-history", timeout=TIMEOUT,
                              params={"market": token, "startTs": start, "endTs": end, "fidelity": 1440})
            if rr.ok:
                chunk = rr.json().get("history", [])
                if not chunk and hist:
                    break
                hist = chunk + hist
            end = start
    if not hist:
        return pd.DataFrame(columns=["date", "price"])
    h = pd.DataFrame(hist)
    h["date"] = pd.to_datetime(h["t"], unit="s").dt.normalize()
    return h.groupby("date", as_index=False)["p"].last().rename(columns={"p": "price"})


def fetch_history(current: pd.DataFrame) -> pd.DataFrame:
    """Daily history, wide: one column per candidate (raw price) -> also normalized long table."""
    series = {}
    for _, row in current.iterrows():
        if row["token_yes"]:
            h = _history_one(row["token_yes"])
            if not h.empty:
                series[row["candidate"]] = h.set_index("date")["price"]
    if not series:
        return pd.DataFrame()
    wide = pd.DataFrame(series).sort_index()
    wide = wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq="D")).ffill()
    wide.index.name = "date"
    return wide


def normalize(wide: pd.DataFrame) -> pd.DataFrame:
    """Rescale each day so candidates sum to 100%."""
    total = wide.sum(axis=1, min_count=1)
    return wide.div(total, axis=0) * 100


def default_peronists(candidates: list) -> list:
    return [c for c in candidates if any(s in c.lower() for s in PERONIST_SURNAMES)]
