"""Kalshi: 'Who will win the next Argentine presidential election?' (public API, no key needed).

Each candidate is a Yes/No contract that pays 1 USD; the Yes price reads as a probability.
Market data endpoints are public: https://docs.kalshi.com/getting_started/quick_start_market_data
"""
import time

import pandas as pd
import requests

BASES = ["https://api.elections.kalshi.com/trade-api/v2",
         "https://external-api.kalshi.com/trade-api/v2"]
SERIES = "KXARGENTINAPRES"
EVENT = "KXARGENTINAPRES-27"
EVENT_URL = "https://kalshi.com/markets/kxargentinapres"
TIMEOUT = 15
CLOSED = {"closed", "determined", "disputed", "amended", "finalized", "settled"}


def _get(path: str, params: dict | None = None) -> dict:
    """GET against the first Kalshi host that answers."""
    last = None
    for base in BASES:
        try:
            r = requests.get(base + path, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # try the next host
            last = e
    raise last


def _f(v):
    try:
        x = float(v)
        return x
    except (TypeError, ValueError):
        return None


def _price(bid, ask, last):
    """Mid-point of bid and ask when the spread is 10 cents or less; otherwise last trade."""
    if bid and ask and 0 < ask - bid <= 0.10:
        return (bid + ask) / 2
    if last:
        return last
    if bid and ask:
        return (bid + ask) / 2
    return bid or ask


def fetch_current() -> pd.DataFrame:
    """One row per candidate: price, bid/ask, volume and open interest (in contracts of 1 USD)."""
    markets, cursor = [], None
    for params in ({"event_ticker": EVENT}, {"series_ticker": SERIES}):
        while True:
            q = dict(params, limit=1000, **({"cursor": cursor} if cursor else {}))
            data = _get("/markets", q)
            markets += data.get("markets", [])
            cursor = data.get("cursor")
            if not cursor:
                break
        if markets:
            break
    if not markets:
        raise RuntimeError(f"No Kalshi markets found for {EVENT}.")
    rows = []
    for m in markets:
        if str(m.get("status", "")).lower() in CLOSED:
            continue
        bid, ask, last = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars")), _f(m.get("last_price_dollars"))
        rows.append({
            "candidate": (m.get("yes_sub_title") or m.get("subtitle") or m["ticker"]).strip(),
            "price": _price(bid, ask, last),
            "best_bid": bid,
            "best_ask": ask,
            "last_trade": last,
            "volume_contracts": _f(m.get("volume_fp")) or 0.0,
            "volume_24h_contracts": _f(m.get("volume_24h_fp")) or 0.0,
            "open_interest_contracts": _f(m.get("open_interest_fp")) or 0.0,
            "ticker": m["ticker"],
            "open_time": m.get("open_time"),
        })
    df = pd.DataFrame(rows).dropna(subset=["price"])
    df = df[df["price"] > 0]
    total = df["price"].sum()
    df["probability"] = df["price"] / total * 100 if total else None
    df.attrs["raw_sum"] = total
    df.attrs["fetched_at"] = pd.Timestamp.now(tz="UTC")
    return df.sort_values("probability", ascending=False).reset_index(drop=True)


def _history_one(ticker: str, open_time) -> pd.Series:
    """Hourly closes, indexed by UTC hour. Each candle is stamped one second before it ends,
    so a candle closing exactly at midnight counts as the close of the previous day."""
    end = int(time.time())
    start = int(pd.Timestamp(open_time).timestamp()) if open_time and str(open_time) != "nan" else end - 730 * 86400
    candles = []
    while start < end:  # 30-day chunks of hourly candles (720 per request)
        chunk_end = min(start + 30 * 86400, end)
        data = _get(f"/series/{SERIES}/markets/{ticker}/candlesticks",
                    {"start_ts": start, "end_ts": chunk_end, "period_interval": 60})
        candles += data.get("candlesticks", [])
        start = chunk_end
    rows = []
    for c in candles:
        bid = _f((c.get("yes_bid") or {}).get("close_dollars"))
        ask = _f((c.get("yes_ask") or {}).get("close_dollars"))
        trade = _f((c.get("price") or {}).get("close_dollars")) or _f((c.get("price") or {}).get("previous_dollars"))
        p = _price(bid, ask, trade)
        if p:
            rows.append((pd.to_datetime(c["end_period_ts"] - 1, unit="s", utc=True).floor("h"), p))
    if not rows:
        return pd.Series(dtype=float)
    return pd.DataFrame(rows, columns=["time", "price"]).groupby("time")["price"].last()


def fetch_intraday(current: pd.DataFrame, start_ts: int) -> pd.DataFrame:
    """One-minute candles since `start_ts` (Unix seconds), wide: one column per candidate (raw
    price), stamped at the end of each minute (UTC), forward-filled."""
    end = int(time.time())
    series = {}
    for _, row in current.iterrows():
        try:
            data = _get(f"/series/{SERIES}/markets/{row['ticker']}/candlesticks",
                        {"start_ts": start_ts, "end_ts": end, "period_interval": 1})
        except Exception:
            continue
        rows = []
        for c in data.get("candlesticks", []):
            bid = _f((c.get("yes_bid") or {}).get("close_dollars"))
            ask = _f((c.get("yes_ask") or {}).get("close_dollars"))
            trade = _f((c.get("price") or {}).get("close_dollars")) or _f((c.get("price") or {}).get("previous_dollars"))
            p = _price(bid, ask, trade)
            if p:
                rows.append((pd.to_datetime(c["end_period_ts"], unit="s", utc=True), p))
        if rows:
            series[row["candidate"]] = pd.DataFrame(rows, columns=["time", "price"]).groupby("time")["price"].last()
    if not series:
        return pd.DataFrame()
    wide = pd.DataFrame(series).sort_index().ffill()
    wide.index.name = "time"
    return wide


def fetch_history(current: pd.DataFrame) -> pd.DataFrame:
    """Hourly history, wide: one column per candidate (raw price), UTC hourly index, forward-filled.
    `current` only needs the columns candidate, ticker and open_time."""
    series = {}
    for _, row in current.iterrows():
        try:
            s = _history_one(row["ticker"], row.get("open_time"))
        except Exception:
            continue
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
