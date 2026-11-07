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


def _prices(params: dict) -> list:
    r = requests.get(f"{CLOB}/prices-history", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("history", [])


def _history_one(token: str) -> pd.Series:
    """Hourly prices, indexed by UTC hour (last price within each hour). Each snapshot is stamped
    one second earlier, so a price taken exactly at midnight counts as the previous day's close."""
    hist = []
    try:
        hist = _prices({"market": token, "interval": "max", "fidelity": 60})
    except Exception:
        hist = []
    ts = [h["t"] for h in hist]
    coarse = len(ts) > 2 and pd.Series(ts).diff().median() > 3 * 3600
    if not hist or coarse:  # ask in explicit 15-day windows, newest first, until the market's start
        end, hist = int(time.time()), []
        for _ in range(60):  # up to ~2.5 years
            start = end - 15 * 86400
            try:
                chunk = _prices({"market": token, "startTs": start, "endTs": end, "fidelity": 60})
            except Exception:
                chunk = []
            if not chunk and hist:
                break
            hist = chunk + hist
            end = start
    if not hist:
        return pd.Series(dtype=float)
    h = pd.DataFrame(hist)
    h["time"] = pd.to_datetime(h["t"] - 1, unit="s", utc=True).dt.floor("h")
    return h.groupby("time")["p"].last()


def fetch_intraday(current: pd.DataFrame, start_ts: int) -> pd.DataFrame:
    """Prices since `start_ts` (Unix seconds) at the finest step Polymarket keeps (about every
    10 minutes), wide: one column per candidate (raw price), exact UTC times, forward-filled."""
    end = int(time.time())
    series = {}
    for _, row in current.iterrows():
        if not row["token_yes"]:
            continue
        try:
            hist = _prices({"market": row["token_yes"], "startTs": start_ts, "endTs": end, "fidelity": 10})
        except Exception:
            continue
        if hist:
            h = pd.DataFrame(hist)
            series[row["candidate"]] = h.groupby(pd.to_datetime(h["t"], unit="s", utc=True))["p"].last()
    if not series:
        return pd.DataFrame()
    wide = pd.DataFrame(series).sort_index().ffill()
    wide.index.name = "time"
    return wide


def fetch_history(current: pd.DataFrame) -> pd.DataFrame:
    """Hourly history, wide: one column per candidate (raw price), UTC hourly index, forward-filled.
    `current` only needs the columns candidate and token_yes."""
    series = {}
    for _, row in current.iterrows():
        if row["token_yes"]:
            s = _history_one(row["token_yes"])
            if not s.empty:
                series[row["candidate"]] = s
    if not series:
        return pd.DataFrame()
    wide = pd.DataFrame(series).sort_index()
    wide = wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq="h")).ffill()
    wide.index.name = "time"
    return wide


def normalize(wide: pd.DataFrame) -> pd.DataFrame:
    """Rescale each row (hour or day) so candidates sum to 100%."""
    total = wide.sum(axis=1, min_count=1)
    return wide.div(total, axis=0) * 100


def default_peronists(candidates: list) -> list:
    return [c for c in candidates if any(s in c.lower() for s in PERONIST_SURNAMES)]
