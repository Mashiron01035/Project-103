"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.05.2026.
    File        : backtest_mcp
    Project     : Project-103
    Description : MCP server exposing the backtest engine to Claude.
                  Lets Claude launch backtests and read structured results
                  directly, without manual session-log copy-paste.

    SPDX-License-Identifier: AGPL-3.0

"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_DIR = PROJECT_ROOT / "logs" / "backtest"
RUN_SCRIPT = PROJECT_ROOT / "scripts" / "run_backtest.py"
SESSION_RE = re.compile(r"session_(\d{8}_\d{6}_[0-9a-f]{6})")
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

mcp = FastMCP("project-103-backtest")

# session_id -> {"proc": Popen, "cmd": str, "out": Path}
_RUNS: dict[str, dict] = {}


# ----------------------------------------------------------------------- helpers


def _result_path(session_id: str) -> Path:
    return BACKTEST_DIR / f"results_{session_id}.json"


def _log_path(session_id: str) -> Path:
    return BACKTEST_DIR / f"session_{session_id}.log"


def _load_result(session_id: str) -> Optional[dict]:
    p = _result_path(session_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _tail(path: Path, nbytes: int = 4000) -> str:
    """Return roughly the last nbytes of a (possibly huge) text file."""
    if not path.exists():
        return "(no file)"
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        return f.read().decode("utf-8", errors="replace")


def _pct(wr: float) -> float:
    """Normalise a win-rate stored either as a 0-1 fraction or a 0-100 percent."""
    return wr * 100.0 if wr <= 1.0 else wr


def _summary_block(data: dict) -> str:
    s, r, t, rk = data["summary"], data["returns"], data["trades"], data["risk"]
    c = data.get("costs", {})
    return (
        f"Period      {s['start_date'][:10]} … {s['end_date'][:10]}  ({s['duration_days']}d)\n"
        f"Balance     ${s['initial_balance']:,.2f} -> ${s['final_balance']:,.2f}  "
        f"({r['total_return_pct']:+.2f}%)\n"
        f"Trades      {t['total_trades']}  |  WR {_pct(t['win_rate']):.1f}%  |  "
        f"PF {t['profit_factor']:.2f}  |  Exp ${t['expectancy']:.2f}\n"
        f"Risk        MaxDD {rk['max_drawdown_pct']:.2f}%  |  Sharpe {rk['sharpe_ratio']:.2f}  |  "
        f"Sortino {rk['sortino_ratio']:.2f}\n"
        f"Costs       commission ${c.get('total_commission', 0):,.2f}  |  "
        f"slippage ${c.get('total_slippage', 0):,.2f}"
    )


def _day_pnl(trades: list[dict]) -> dict[str, float]:
    """Aggregate net PnL per calendar day, keyed by trade exit date."""
    by_day: dict[str, float] = defaultdict(float)
    for tr in trades:
        d = tr["exit_time"][:10]
        pnl = tr.get("net_pnl")
        if pnl is None:
            pnl = tr.get("pnl", 0.0)
        by_day[d] += pnl
    return by_day


# ------------------------------------------------------------------------- tools


@mcp.tool()
def run_backtest(
    strategy: str,
    symbols: str = "XAUUSD.s",
    timeframe: str = "1m",
    days: Optional[int] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    balance: float = 1500.0,
    leverage: float = 500.0,
    balance_cap: float = 0.0,
    daily_profit_target: Optional[float] = None,
    spread: float = 0.25,
    tick_execution: bool = True,
    no_regime_switching: bool = True,
    data_source: str = "csv",
    note: bool = False,
    extra_args: str = "",
) -> str:
    """Launch a backtest as a background process and return its session_id.

    Does NOT block until completion — poll with check_backtest(session_id).
    Defaults match the standard XAUUSD.s 1m setup. `--risk` is deliberately
    NOT exposed: overriding it distorts position sizing (use the config
    default). Use `extra_args` for any flag not covered here — it is passed
    verbatim to run_backtest.py (e.g. "--regime-gate-rules \\"session_breakout:min=25:15m\\"").

    Args:
        strategy: comma-separated strategy name(s), required.
        days: lookback in days — supply this OR start/end.
        balance_cap: 0=off; >=100 dollar cap; <100 percent-of-balance cap.
        daily_profit_target: stop-new-trades threshold in percent (None=off).
    """
    if not days and not start:
        return "ERROR: specify either `days` or `start`."

    # The backtest must run in the project's poetry env (it needs src deps),
    # which is separate from this server's own isolated env.
    cmd = [
        "poetry",
        "run",
        "python",
        str(RUN_SCRIPT),
        "--symbols",
        symbols,
        "--timeframe",
        timeframe,
        "--strategy",
        strategy,
        "--balance",
        str(balance),
        "--leverage",
        str(leverage),
        "--data-source",
        data_source,
    ]
    if days:
        cmd += ["--days", str(days)]
    if start:
        cmd += ["--start", start]
    if end:
        cmd += ["--end", end]
    if balance_cap:
        cmd += ["--balance-cap", str(balance_cap)]
    if daily_profit_target is not None:
        cmd += ["--daily-profit-target", str(daily_profit_target)]
    if spread:
        cmd += ["--spread", str(spread)]
    if tick_execution:
        cmd += ["--tick-execution"]
    if no_regime_switching:
        cmd += ["--no-regime-switching"]
    if note:
        cmd += ["--note"]
    if extra_args:
        cmd += shlex.split(extra_args)

    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    out_path = BACKTEST_DIR / f"mcp_run_{datetime.now():%Y%m%d_%H%M%S}.out"
    out_f = open(out_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd, cwd=str(PROJECT_ROOT), stdout=out_f, stderr=subprocess.STDOUT, text=True
    )

    # run_backtest.py prints "Logging to: .../session_<id>.log" near startup.
    session_id = None
    deadline = time.time() + 30
    while time.time() < deadline:
        text = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
        m = SESSION_RE.search(text)
        if m:
            session_id = m.group(1)
            break
        if proc.poll() is not None:  # process exited before we saw the id
            break
        time.sleep(0.5)

    if not session_id:
        rc = proc.poll()
        tail = _tail(out_path, 2500)
        return (
            f"ERROR: could not capture session_id "
            f"({'process exited code ' + str(rc) if rc is not None else 'still starting'}).\n"
            f"--- output ---\n{tail}"
        )

    _RUNS[session_id] = {"proc": proc, "cmd": " ".join(cmd), "out": out_path}
    return (
        f"Backtest launched.\n"
        f"session_id: {session_id}\n"
        f"command:    {' '.join(cmd)}\n\n"
        f"Poll with check_backtest('{session_id}')."
    )


@mcp.tool()
def check_backtest(session_id: str) -> str:
    """Poll a backtest: returns running / completed / failed plus a short summary."""
    if _result_path(session_id).exists():
        data = _load_result(session_id)
        return (
            f"COMPLETED — {session_id}\n\n{_summary_block(data)}\n\n"
            f"-> get_backtest_result / daily_pnl for detail."
        )

    run = _RUNS.get(session_id)
    log_file = _log_path(session_id)

    if run is not None:
        rc = run["proc"].poll()
        if rc is None:
            return f"RUNNING — {session_id}\n" f"last log lines:\n{_tail(log_file, 1200)}"
        # process ended, no results json
        if rc == 0:
            return (
                f"FINISHED (no results JSON) — {session_id}\n"
                f"Exit code 0 but no results file: the run likely produced zero trades.\n"
                f"log tail:\n{_tail(log_file, 2000)}"
            )
        return (
            f"FAILED — {session_id} (exit code {rc})\n" f"output tail:\n{_tail(run['out'], 3000)}"
        )

    # no in-memory handle (server restarted) — infer from file freshness
    if log_file.exists():
        age = time.time() - log_file.stat().st_mtime
        state = "RUNNING (probably)" if age < 120 else "STALLED or FAILED"
        return (
            f"{state} — {session_id} (log idle {age:.0f}s, no results JSON)\n"
            f"log tail:\n{_tail(log_file, 2000)}"
        )
    return f"UNKNOWN — no log or results found for {session_id}."


@mcp.tool()
def get_backtest_result(session_id: str) -> str:
    """Full metrics for a finished backtest: summary, returns, per-strategy and per-symbol."""
    data = _load_result(session_id)
    if data is None:
        return f"No results for {session_id} yet — check_backtest('{session_id}') first."

    out = [f"BACKTEST RESULT — {session_id}", "", _summary_block(data)]

    cfg = data.get("config", {})
    out.append("")
    out.append(f"Strategies: {', '.join(cfg.get('strategy_names', [])) or 'n/a'}")
    out.append(
        f"Symbols:    {', '.join(cfg.get('symbols', [])) or 'n/a'}  "
        f"| TF {', '.join(cfg.get('timeframes', [])) or 'n/a'}  "
        f"| leverage {cfg.get('leverage', 'n/a')}x"
    )

    sp = data.get("strategy_performance", {})
    if sp:
        out.append("")
        out.append("Per-strategy:")
        out.append(f"  {'strategy':<26}{'trades':>8}{'WR%':>8}{'PF':>8}{'PnL $':>14}")
        for name, m in sorted(sp.items(), key=lambda kv: kv[1]["total_pnl"], reverse=True):
            out.append(
                f"  {name:<26}{m['total_trades']:>8}{_pct(m['win_rate']):>8.1f}"
                f"{m['profit_factor']:>8.2f}{m['total_pnl']:>14,.2f}"
            )

    syp = data.get("symbol_performance", {})
    if syp:
        out.append("")
        out.append("Per-symbol:")
        for sym, m in syp.items():
            out.append(
                f"  {sym:<14}{m['total_trades']:>6} trades  WR {_pct(m['win_rate']):>5.1f}%  "
                f"PnL ${m['total_pnl']:,.2f}"
            )

    n_trades = len(data.get("trades_list", []))
    out.append("")
    out.append(f"trades_list holds {n_trades} trades — use daily_pnl() for the day-level view.")
    return "\n".join(out)


@mcp.tool()
def daily_pnl(session_id: str) -> str:
    """Per-day PnL table plus run-length and day-of-week analysis.

    Reconstructs equity to give PnL both in $ and in % of start-of-day balance
    (the de-inflated view). Compares observed win/loss run lengths against the
    random expectation so apparent "cycles" can be judged.
    """
    data = _load_result(session_id)
    if data is None:
        return f"No results for {session_id} yet — check_backtest('{session_id}') first."
    trades = data.get("trades_list", [])
    if not trades:
        return f"results_{session_id}.json has no trades_list (zero-trade run?)."

    by_day = _day_pnl(trades)
    days = sorted(by_day)
    init_bal = data["summary"]["initial_balance"]

    rows = ["date         dow        PnL $        PnL %        cumulative $"]
    equity, cum = init_bal, 0.0
    for d in days:
        pnl = by_day[d]
        pct = (pnl / equity * 100.0) if equity else 0.0
        cum += pnl
        rows.append(
            f"{d}   {DOW[date.fromisoformat(d).weekday()]}  {pnl:>13,.2f}  "
            f"{pct:>+9.2f}%  {cum:>16,.2f}"
        )
        equity += pnl

    # run-length analysis
    wins = [by_day[d] > 0 for d in days]
    n = len(days)
    npos = sum(wins)
    p = npos / n if n else 0.0
    runs_pos: list[int] = []
    runs_neg: list[int] = []
    i = 0
    while i < n:
        j = i
        while j < n and wins[j] == wins[i]:
            j += 1
        (runs_pos if wins[i] else runs_neg).append(j - i)
        i = j
    avg_pos = sum(runs_pos) / len(runs_pos) if runs_pos else 0.0
    avg_neg = sum(runs_neg) / len(runs_neg) if runs_neg else 0.0
    exp_pos = 1.0 / (1.0 - p) if 0.0 < p < 1.0 else float("inf")
    exp_neg = 1.0 / p if p > 0.0 else float("inf")

    # day-of-week
    dow_pnl: dict[int, float] = defaultdict(float)
    dow_cnt: dict[int, int] = defaultdict(int)
    dow_loss: dict[int, float] = defaultdict(float)
    for d in days:
        w = date.fromisoformat(d).weekday()
        dow_pnl[w] += by_day[d]
        dow_cnt[w] += 1
        if by_day[d] <= 0:
            dow_loss[w] += by_day[d]
    dow_rows = ["  dow   days     total PnL $      avg/day $       losses $"]
    for w in range(7):
        if dow_cnt[w]:
            dow_rows.append(
                f"  {DOW[w]}   {dow_cnt[w]:>4}  {dow_pnl[w]:>15,.2f}  "
                f"{dow_pnl[w] / dow_cnt[w]:>13,.2f}  {dow_loss[w]:>14,.2f}"
            )

    return "\n".join(
        [
            f"DAILY PnL — {session_id}",
            "",
            "\n".join(rows),
            "",
            f"Trading days: {n}   positive: {npos} ({p * 100:.1f}%)",
            f"Win-run  avg {avg_pos:.2f} days   (random expectation {exp_pos:.2f})",
            f"Loss-run avg {avg_neg:.2f} days   (random expectation {exp_neg:.2f})",
            "If observed ~= expectation, day sequencing is random — no exploitable cycle.",
            "",
            "By day of week:",
            "\n".join(dow_rows),
        ]
    )


@mcp.tool()
def list_backtest_sessions(limit: int = 15) -> str:
    """List the most recent finished backtest sessions (newest first)."""
    files = sorted(
        BACKTEST_DIR.glob("results_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    if not files:
        return "No finished backtests found in logs/backtest/."
    rows = [f"{'session_id':<26}{'return%':>10}{'trades':>9}{'PF':>8}  finished"]
    for f in files:
        sid = f.stem.replace("results_", "")
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            ret = d["returns"]["total_return_pct"]
            tr = d["trades"]["total_trades"]
            pf = d["trades"]["profit_factor"]
            ts = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            rows.append(f"{sid:<26}{ret:>+9.1f}%{tr:>9}{pf:>8.2f}  {ts}")
        except Exception as exc:  # noqa: BLE001 - listing must not fail on one bad file
            rows.append(f"{sid:<26}  (unreadable: {exc})")
    return "\n".join(rows)


@mcp.tool()
def grep_backtest_log(session_id: str, pattern: str, max_lines: int = 80) -> str:
    """Search the raw session log of a backtest for a regex pattern (case-insensitive)."""
    log_file = _log_path(session_id)
    if not log_file.exists():
        return f"No session log for {session_id}."
    rx = re.compile(pattern, re.IGNORECASE)
    hits: list[str] = []
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if rx.search(line):
                hits.append(line.rstrip())
                if len(hits) >= max_lines:
                    hits.append(f"... (capped at {max_lines} matches)")
                    break
    if not hits:
        return f"No matches for /{pattern}/ in session_{session_id}.log."
    return "\n".join(hits)


if __name__ == "__main__":
    mcp.run()
