"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 15.10.2026.
    File        : var_backtest.py
    Project     : Project-103
    Description : Backtest of one-day VaR/ES models on daily returns built from
                  1-minute bars — either one continuous series (spot/CFD) or
                  per-contract futures bars, from which a roll-free series is
                  built (each day's return is measured inside the contract held
                  over that day). Companion to docs/writeups/04-gold-var.md.

    SPDX-License-Identifier: AGPL-3.0

"""

import argparse
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.research.var import ForecastConfig, backtest_table, rolling_forecasts  # noqa: E402

ROLL_STICKY = 2  # a new front contract must lead daily volume this many days in a row


def _trading_day(ts: pd.Series) -> pd.Series:
    """CME trading day: the session opening 18:00 New York belongs to the next date."""
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    et = ts.dt.tz_convert("America/New_York") + pd.Timedelta(hours=6)
    return et.dt.tz_localize(None).dt.normalize()


def _daily_table(bars: pd.DataFrame) -> pd.DataFrame:
    """Per (symbol, trading day): last close, volume and 5-minute realised variance."""
    bars = bars.sort_values(["symbol", "timestamp"])
    bars["tday"] = _trading_day(bars["timestamp"])
    bars["m5"] = bars["timestamp"].dt.floor("5min")
    c5 = bars.groupby(["symbol", "tday", "m5"], as_index=False)["close"].last()
    c5["r"] = np.log(c5["close"]).groupby([c5["symbol"], c5["tday"]]).diff()
    rv = (c5["r"] ** 2).groupby([c5["symbol"], c5["tday"]]).sum().rename("rv5")
    day = bars.groupby(["symbol", "tday"]).agg(close=("close", "last"), volume=("volume", "sum"))
    day = day.join(rv).reset_index()
    return day[day["tday"].dt.dayofweek < 5]


def daily_from_contracts(bars: pd.DataFrame) -> pd.DataFrame:
    """Roll-free daily log returns from per-contract minute bars (symbol, timestamp, close, volume)."""
    day = _daily_table(bars)
    leader = (
        day.sort_values(["tday", "volume"], ascending=[True, False])
        .groupby("tday")
        .head(1)
        .set_index("tday")["symbol"]
    )
    active, cur, cand, streak = {}, None, None, 0
    for t, sym in leader.items():
        if cur is None:
            cur = sym
        elif sym != cur:
            streak = streak + 1 if sym == cand else 1
            cand = sym
            if streak >= ROLL_STICKY:
                cur, cand, streak = sym, None, 0
        else:
            cand, streak = None, 0
        active[t] = cur

    px = day.set_index(["symbol", "tday"])
    days = list(active)
    rows = []
    for t0, t1 in zip(days[:-1], days[1:]):
        sym = active[t0]  # the contract held over t1 is the one that was front at t0's close
        if (sym, t0) in px.index and (sym, t1) in px.index:
            a, b = px.loc[(sym, t0)], px.loc[(sym, t1)]
            rows.append((t1, np.log(b["close"] / a["close"]), b["rv5"]))
    return pd.DataFrame(rows, columns=["date", "ret", "rv5"]).set_index("date")


def daily_from_series(bars: pd.DataFrame) -> pd.DataFrame:
    """Daily close-to-close log returns from one continuous minute series."""
    bars = bars.assign(symbol="X", volume=bars.get("volume", 1))
    day = _daily_table(bars).set_index("tday")
    day["ret"] = np.log(day["close"]).diff()
    return day[["ret", "rv5"]].dropna().rename_axis("date")


def load_bars(pattern: str) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(pattern)):
        d = pd.read_parquet(f) if f.endswith(".parquet") else pd.read_csv(f)
        d = d.rename(columns={"minute": "timestamp"})
        cols = [c for c in ("symbol", "timestamp", "close", "volume") if c in d.columns]
        frames.append(d[cols])
    if not frames:
        raise SystemExit(f"no files match {pattern}")
    bars = pd.concat(frames, ignore_index=True)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    if "symbol" in bars.columns:  # a contract split across files
        bars = bars.groupby(["symbol", "timestamp"], as_index=False).agg(
            close=("close", "last"), volume=("volume", "sum")
        )
    return bars


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--bars", help="continuous 1m CSV (timestamp, close[, volume]), UTC")
    src.add_argument("--contracts", help="glob of per-contract 1m files with a symbol column")
    ap.add_argument("--side", choices=("long", "short"), default="long")
    ap.add_argument("--drop-last", action="store_true", help="drop a partial final day")
    ap.add_argument("--save", type=Path, help="write the forecasts to this CSV")
    args = ap.parse_args()

    bars = load_bars(args.contracts or args.bars)
    daily = daily_from_contracts(bars) if args.contracts else daily_from_series(bars)
    if args.drop_last:
        daily = daily.iloc[:-1]
    print(f"{len(daily)} days {daily.index[0].date()} .. {daily.index[-1].date()}")

    cfg = ForecastConfig()
    fc = rolling_forecasts(daily["ret"].values * 100, daily["rv5"].values * 1e4, args.side, cfg)
    fc.index = daily.index[cfg.warmup :]
    if args.save:
        fc.to_csv(args.save)

    print(f"\n{args.side} position, {len(fc)} out-of-sample days")
    with pd.option_context(
        "display.width", 200, "display.max_columns", None, "display.precision", 3
    ):
        print(backtest_table(fc))

    models = [c[: -len("_VaR99")] for c in fc.columns if c.endswith("_VaR99")]
    hits = pd.DataFrame({m: fc["loss"] > fc[f"{m}_VaR99"] for m in models})
    print("\n99% exceptions by year")
    by_year = hits.groupby(fc.index.year).sum()
    by_year["days"] = hits.groupby(fc.index.year).size()
    print(by_year.to_string())

    print("\nlargest losses (%) against each model's VaR99")
    worst = fc.nlargest(8, "loss")
    print(worst[["loss"] + [f"{m}_VaR99" for m in models]].round(2).to_string())

    monday = fc.index.dayofweek == 0
    print("\n99% exception rate, Monday (weekend gap) vs other days")
    for m in models:
        print(
            f"{m:10s} Mon {100 * hits[m][monday].mean():5.2f}%  other {100 * hits[m][~monday].mean():5.2f}%"
        )


if __name__ == "__main__":
    main()
