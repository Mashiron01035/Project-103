"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 11.01.2026.
    File        : equity_chart.py
    Project     : Project-103
    Description : Equity curve chart generator from stored backtest/live results.

    SPDX-License-Identifier: AGPL-3.0

"""

import argparse
import sys
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.core.config import get_config
from src.data.storage.models import SignalModel
from src.utils.datetime_utils import utcnow


def get_closed_signals(
    session, hours: Optional[int] = None, symbol: Optional[str] = None
) -> List[Dict]:
    """Fetch closed signals from database."""
    query = select(SignalModel).where(
        SignalModel.status == "CLOSED",
        SignalModel.closed_at.isnot(None),
        SignalModel.pnl.isnot(None),
    )

    if hours:
        start_time = utcnow() - timedelta(hours=hours)
        query = query.where(SignalModel.closed_at >= start_time)

    if symbol:
        query = query.where(SignalModel.symbol == symbol)

    query = query.order_by(SignalModel.closed_at.asc())

    results = session.execute(query).scalars().all()

    signals = []
    for s in results:
        signals.append(
            {
                "signal_id": s.signal_id,
                "symbol": s.symbol,
                "side": s.signal_type,
                "entry_price": s.entry_price,
                "exit_price": s.exit_price,
                "exit_reason": s.exit_reason,
                "pnl": s.pnl or 0,
                "pnl_percent": s.pnl_percent or 0,
                "closed_at": s.closed_at,
                "executed_at": s.executed_at,
            }
        )

    return signals


def calculate_equity_curve(signals: List[Dict], initial_balance: float = 10000.0) -> Dict:
    """Calculate equity curve from signals."""
    timestamps = []
    balances = []
    drawdowns = []

    balance = initial_balance
    max_balance = initial_balance

    for signal in signals:
        pnl = signal["pnl"]
        balance += pnl

        if balance > max_balance:
            max_balance = balance

        drawdown = (max_balance - balance) / max_balance * 100 if max_balance > 0 else 0

        timestamps.append(signal["closed_at"])
        balances.append(balance)
        drawdowns.append(drawdown)

    return {
        "timestamps": timestamps,
        "balances": balances,
        "drawdowns": drawdowns,
        "final_balance": balance,
        "max_drawdown": max(drawdowns) if drawdowns else 0,
        "total_pnl": balance - initial_balance,
        "total_pnl_percent": (balance - initial_balance) / initial_balance * 100,
    }


def calculate_symbol_stats(signals: List[Dict]) -> Dict:
    """Calculate P&L statistics by symbol."""
    symbol_stats = {}

    for signal in signals:
        symbol = signal["symbol"]
        if symbol not in symbol_stats:
            symbol_stats[symbol] = {"total_pnl": 0, "trades": 0, "wins": 0, "losses": 0, "pnls": []}

        stats = symbol_stats[symbol]
        stats["total_pnl"] += signal["pnl"]
        stats["trades"] += 1
        stats["pnls"].append(signal["pnl"])

        if signal["pnl"] > 0:
            stats["wins"] += 1
        else:
            stats["losses"] += 1

    return symbol_stats


def create_equity_chart(
    signals: List[Dict],
    initial_balance: float = 10000.0,
    output_path: Optional[str] = None,
    title: str = "Trading Performance",
):
    """Create comprehensive equity chart."""
    if not signals:
        print("No signals to plot")
        return

    # Calculate data
    equity = calculate_equity_curve(signals, initial_balance)
    symbol_stats = calculate_symbol_stats(signals)

    # Create figure with subplots
    fig = plt.figure(figsize=(16, 12))
    gs = GridSpec(3, 2, figure=fig, height_ratios=[2, 1, 1])

    # 1. Equity Curve (top, full width)
    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(equity["timestamps"], equity["balances"], "b-", linewidth=2, label="Balance")
    ax1.axhline(y=initial_balance, color="gray", linestyle="--", alpha=0.5, label="Initial")
    ax1.fill_between(
        equity["timestamps"],
        initial_balance,
        equity["balances"],
        where=[b >= initial_balance for b in equity["balances"]],
        color="green",
        alpha=0.3,
    )
    ax1.fill_between(
        equity["timestamps"],
        initial_balance,
        equity["balances"],
        where=[b < initial_balance for b in equity["balances"]],
        color="red",
        alpha=0.3,
    )
    ax1.set_title(f"{title} - Equity Curve", fontsize=14, fontweight="bold")
    ax1.set_ylabel("Balance ($)")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)

    # Add annotations
    final = equity["final_balance"]
    pnl = equity["total_pnl"]
    pnl_pct = equity["total_pnl_percent"]
    ax1.annotate(
        f"Final: ${final:.2f}\nP&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)",
        xy=(equity["timestamps"][-1], final),
        xytext=(10, 10),
        textcoords="offset points",
        fontsize=10,
        fontweight="bold",
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
    )

    # 2. Drawdown (middle left)
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.fill_between(equity["timestamps"], 0, equity["drawdowns"], color="red", alpha=0.5)
    ax2.plot(equity["timestamps"], equity["drawdowns"], "r-", linewidth=1)
    ax2.set_title("Drawdown %", fontsize=12)
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_ylim(0, max(equity["drawdowns"]) * 1.2 if equity["drawdowns"] else 10)
    ax2.invert_yaxis()
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45)

    # 3. P&L by Symbol (middle right)
    ax3 = fig.add_subplot(gs[1, 1])
    symbols = list(symbol_stats.keys())
    pnls = [symbol_stats[s]["total_pnl"] for s in symbols]
    colors = ["green" if p > 0 else "red" for p in pnls]
    bars = ax3.barh(symbols, pnls, color=colors, alpha=0.7)
    ax3.axvline(x=0, color="black", linewidth=0.5)
    ax3.set_title("P&L by Symbol ($)", fontsize=12)
    ax3.set_xlabel("P&L ($)")
    ax3.grid(True, alpha=0.3, axis="x")

    # Add values on bars
    for bar, pnl in zip(bars, pnls):
        width = bar.get_width()
        ax3.annotate(
            f"${pnl:+.2f}",
            xy=(width, bar.get_y() + bar.get_height() / 2),
            xytext=(5 if width >= 0 else -5, 0),
            textcoords="offset points",
            ha="left" if width >= 0 else "right",
            va="center",
            fontsize=8,
        )

    # 4. Trade Distribution (bottom left)
    ax4 = fig.add_subplot(gs[2, 0])
    all_pnls = [s["pnl"] for s in signals]
    wins = [p for p in all_pnls if p > 0]
    losses = [p for p in all_pnls if p <= 0]

    ax4.hist(
        [wins, losses],
        bins=20,
        label=["Wins", "Losses"],
        color=["green", "red"],
        alpha=0.7,
        stacked=True,
    )
    ax4.axvline(x=0, color="black", linewidth=1)
    ax4.set_title("Trade P&L Distribution", fontsize=12)
    ax4.set_xlabel("P&L ($)")
    ax4.set_ylabel("Count")
    ax4.legend()
    ax4.grid(True, alpha=0.3)

    # 5. Win Rate by Symbol (bottom right)
    ax5 = fig.add_subplot(gs[2, 1])
    win_rates = []
    for s in symbols:
        stats = symbol_stats[s]
        wr = stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0
        win_rates.append(wr)

    colors = ["green" if wr >= 50 else "orange" if wr >= 40 else "red" for wr in win_rates]
    bars = ax5.barh(symbols, win_rates, color=colors, alpha=0.7)
    ax5.axvline(x=50, color="gray", linestyle="--", alpha=0.5)
    ax5.set_title("Win Rate by Symbol (%)", fontsize=12)
    ax5.set_xlabel("Win Rate (%)")
    ax5.set_xlim(0, 100)
    ax5.grid(True, alpha=0.3, axis="x")

    # Add values and trade counts
    for bar, wr, symbol in zip(bars, win_rates, symbols):
        trades = symbol_stats[symbol]["trades"]
        ax5.annotate(
            f"{wr:.0f}% ({trades})",
            xy=(wr, bar.get_y() + bar.get_height() / 2),
            xytext=(5, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            fontsize=8,
        )

    # Summary stats
    total_trades = len(signals)
    total_wins = len(wins)
    win_rate = total_wins / total_trades * 100 if total_trades > 0 else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0

    summary = (
        f"Total Trades: {total_trades} | "
        f"Win Rate: {win_rate:.1f}% | "
        f"Avg Win: ${avg_win:.2f} | "
        f"Avg Loss: ${avg_loss:.2f} | "
        f"Max DD: {equity['max_drawdown']:.1f}%"
    )
    fig.suptitle(summary, y=0.02, fontsize=10, style="italic")

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.08)

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Chart saved to: {output_path}")
    else:
        plt.show()

    return fig


def main():
    parser = argparse.ArgumentParser(description="Generate equity curve chart")
    parser.add_argument("--hours", type=int, help="Hours of history to show")
    parser.add_argument("--symbol", type=str, help="Filter by symbol")
    parser.add_argument("--output", "-o", type=str, help="Output file path")
    parser.add_argument("--balance", type=float, default=10000.0, help="Initial balance")
    args = parser.parse_args()

    # Get config
    try:
        config = get_config()
        db_url = f"postgresql://{config.database.user}:{config.database.password}@{config.database.host}:{config.database.port}/{config.database.name}"
    except Exception:
        db_url = "postgresql://trader:trader@localhost:5432/project103"

    print("Connecting to database...")

    try:
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        session = Session()

        print("Fetching signals...")
        signals = get_closed_signals(session, hours=args.hours, symbol=args.symbol)

        print(f"Found {len(signals)} closed signals")

        if signals:
            title = "Trading Performance"
            if args.hours:
                title += f" (Last {args.hours}h)"
            if args.symbol:
                title += f" - {args.symbol}"

            create_equity_chart(
                signals, initial_balance=args.balance, output_path=args.output, title=title
            )
        else:
            print("No closed signals found in database")

        session.close()

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
