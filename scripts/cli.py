#!/usr/bin/env python3
"""

    -*- coding: utf-8 -*-
    Trading Bot CLI Controller

    Provides command-line interface for managing the trading bot.
    Can be used via docker exec or directly if dashboard is accessible.

    Usage:
        docker exec trading-bot python -m scripts.cli status
        docker exec trading-bot python -m scripts.cli trading on
        docker exec trading-bot python -m scripts.cli leverage 10

    SPDX-License-Identifier: AGPL-3.0

"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Optional

# Default API URL (inside Docker network)
DEFAULT_API_URL = "http://localhost:8080"


def make_request(
    endpoint: str, method: str = "GET", data: Optional[dict] = None, api_url: str = DEFAULT_API_URL
) -> dict:
    """Make HTTP request to API"""
    url = f"{api_url}{endpoint}"

    try:
        if data:
            # Add query params for simple values
            params = "&".join(f"{k}={v}" for k, v in data.items())
            if method == "GET":
                url = f"{url}?{params}"
            else:
                url = f"{url}?{params}"

        request = urllib.request.Request(url, method=method)
        request.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode())

    except urllib.error.HTTPError as e:
        try:
            error_body = json.loads(e.read().decode())
            return {"error": error_body.get("error", str(e)), "status_code": e.code}
        except Exception:
            return {"error": str(e), "status_code": e.code}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def cmd_status(args):
    """Show bot status"""
    result = make_request("/api/control/status", api_url=args.api_url)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    state = result.get("trading_state", "unknown")
    state_icon = {
        "enabled": "[ON]",
        "disabled": "[OFF]",
        "paused": "[PAUSED]",
        "emergency": "[EMERGENCY]",
    }.get(state, "[?]")

    print(f"\n{'='*50}")
    print("  TRADING BOT STATUS")
    print(f"{'='*50}")
    print(f"  State:      {state_icon} {state.upper()}")
    print(f"  Mode:       {result.get('mode', 'unknown')}")
    print(f"  Exchange:   {result.get('exchange', 'unknown')}")
    print("")
    print(f"  Strategies: {len(result.get('active_strategies', []))}")
    for s in result.get("active_strategies", []):
        print(f"              - {s}")
    print("")
    print(f"  Pairs:      {len(result.get('active_pairs', []))}")
    for p in result.get("active_pairs", []):
        print(f"              - {p}")
    print("")
    print(f"  Signals:    {result.get('total_signals_generated', 0)}")
    print(f"  Trades:     {result.get('total_trades_executed', 0)}")
    print(f"{'='*50}\n")
    return 0


def cmd_trading(args):
    """Enable/disable trading"""
    if args.action == "on":
        result = make_request("/api/control/trading/enable", method="POST", api_url=args.api_url)
        if result.get("success"):
            print("[OK] Trading ENABLED")
            return 0
    elif args.action == "off":
        result = make_request("/api/control/trading/disable", method="POST", api_url=args.api_url)
        if result.get("success"):
            print("[OK] Trading DISABLED")
            return 0
    else:
        print(f"Unknown action: {args.action}. Use 'on' or 'off'")
        return 1

    print(f"Error: {result.get('error', 'Unknown error')}")
    return 1


def cmd_stop(args):
    """Emergency stop"""
    result = make_request(
        "/api/control/trading/emergency-stop", method="POST", api_url=args.api_url
    )

    if result.get("success"):
        print("[!!!] EMERGENCY STOP EXECUTED")
        actions = result.get("actions_taken", [])
        if actions:
            print(f"Actions: {', '.join(actions)}")
        return 0

    print(f"Error: {result.get('error', 'Unknown error')}")
    return 1


def cmd_balance(args):
    """Show balance and P&L"""
    result = make_request("/api/control/balance", api_url=args.api_url)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    initial = result.get("initial_balance", 0)
    current = result.get("current_balance", 0)
    pnl = result.get("total_pnl_usdt", 0)
    roi = ((current - initial) / initial * 100) if initial > 0 else 0

    pnl_icon = "+" if pnl >= 0 else ""

    print(f"\n{'='*50}")
    print("  BALANCE & P&L")
    print(f"{'='*50}")
    print(f"  Initial:    ${initial:,.2f}")
    print(f"  Current:    ${current:,.2f}")
    print(f"  Peak:       ${result.get('max_balance', initial):,.2f}")
    print("")
    print(f"  P&L:        {pnl_icon}${pnl:,.2f} ({pnl_icon}{roi:.2f}%)")
    print(f"  Drawdown:   {result.get('max_drawdown_percent', 0):.2f}%")
    print("")
    print(f"  Open:       {result.get('active_signals', 0)}")
    print(f"  Closed:     {result.get('signals_closed', 0)}")
    print(f"  Win Rate:   {result.get('win_rate', 0) * 100:.1f}%")
    print(f"{'='*50}\n")
    return 0


def cmd_leverage(args):
    """View or set leverage"""
    if args.value is None:
        # Get current leverage
        result = make_request("/api/control/leverage", api_url=args.api_url)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print(f"Current leverage: {result.get('leverage', 1)}x")
        return 0
    else:
        # Set leverage
        result = make_request(
            "/api/control/leverage",
            method="POST",
            data={"leverage": args.value},
            api_url=args.api_url,
        )
        if result.get("success"):
            print(
                f"[OK] Leverage changed: {result.get('old_leverage')}x -> {result.get('new_leverage')}x"
            )
            return 0
        print(f"Error: {result.get('error', 'Unknown error')}")
        return 1


def cmd_hedge(args):
    """View or set hedge mode"""
    if args.action is None:
        # Get current mode
        result = make_request("/api/control/hedge", api_url=args.api_url)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        mode = result.get("hedge_mode", False)
        print(f"Hedge mode: {'ON' if mode else 'OFF'}")
        return 0
    else:
        # Set mode
        enabled = args.action.lower() in ("on", "true", "1", "enable")
        result = make_request(
            "/api/control/hedge",
            method="POST",
            data={"enabled": str(enabled).lower()},
            api_url=args.api_url,
        )
        if result.get("success"):
            new_mode = result.get("new_mode", False)
            print(f"[OK] Hedge mode: {'ON' if new_mode else 'OFF'}")
            return 0
        print(f"Error: {result.get('error', 'Unknown error')}")
        return 1


def cmd_pairs(args):
    """List trading pairs"""
    result = make_request("/api/control/status", api_url=args.api_url)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    pairs = result.get("active_pairs", [])
    print(f"\nActive Trading Pairs ({len(pairs)}):")
    for p in pairs:
        print(f"  - {p}")
    print()
    return 0


def cmd_add_pair(args):
    """Add trading pair"""
    result = make_request(
        "/api/control/pairs/add",
        method="POST",
        data={"symbol": args.symbol.upper()},
        api_url=args.api_url,
    )

    if result.get("success"):
        print(f"[OK] Added pair: {args.symbol.upper()}")
        return 0

    print(f"Error: {result.get('error', 'Unknown error')}")
    return 1


def cmd_remove_pair(args):
    """Remove trading pair"""
    result = make_request(
        "/api/control/pairs/remove",
        method="POST",
        data={"symbol": args.symbol.upper()},
        api_url=args.api_url,
    )

    if result.get("success"):
        print(f"[OK] Removed pair: {args.symbol.upper()}")
        return 0

    print(f"Error: {result.get('error', 'Unknown error')}")
    return 1


def cmd_strategies(args):
    """List strategies"""
    result = make_request("/api/control/strategies", api_url=args.api_url)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    strategies = result.get("strategies", [])
    print(f"\nStrategies ({result.get('active', 0)}/{result.get('total', 0)} active):")
    for s in strategies:
        icon = "[ON]" if s.get("enabled") else "[OFF]"
        print(f"  {icon} {s.get('name')}")
        print(
            f"       Signals: {s.get('total_signals', 0)} | Win: {s.get('win_rate', 0):.1f}% | P&L: ${s.get('total_pnl', 0):.2f}"
        )
    print()
    return 0


def cmd_stats(args):
    """Show trading statistics"""
    result = make_request("/api/control/stats", api_url=args.api_url)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    if result.get("message"):
        print(result["message"])
        return 0

    print(f"\n{'='*50}")
    print("  TRADING STATISTICS")
    print(f"{'='*50}")
    print(f"  Total Signals:  {result.get('total_signals', 0)}")
    print(f"  Closed:         {result.get('closed_signals', 0)}")
    print("")
    print(f"  Winners:        {result.get('winners', 0)}")
    print(f"  Losers:         {result.get('losers', 0)}")
    print(f"  Win Rate:       {result.get('win_rate', 0) * 100:.1f}%")
    print("")
    print(f"  Total P&L:      {result.get('total_pnl', 0):+.2f}%")
    print(f"  Avg Winner:     +{result.get('avg_winner', 0):.2f}%")
    print(f"  Avg Loser:      {result.get('avg_loser', 0):.2f}%")
    print(f"  Profit Factor:  {result.get('profit_factor', 0):.2f}")

    avg_hold = result.get("avg_holding_time_seconds", 0)
    if avg_hold > 3600:
        hold_str = f"{avg_hold/3600:.1f}h"
    elif avg_hold > 60:
        hold_str = f"{avg_hold/60:.1f}m"
    else:
        hold_str = f"{avg_hold:.0f}s"
    print(f"  Avg Hold Time:  {hold_str}")
    print(f"{'='*50}\n")
    return 0


def cmd_reload(args):
    """Reload configuration"""
    result = make_request("/api/control/config/reload", method="POST", api_url=args.api_url)

    if result.get("success"):
        print("[OK] Configuration reloaded")
        changes = result.get("changes", [])
        if changes:
            for c in changes:
                print(f"  - {c}")
        return 0

    print(f"Error: {result.get('error', 'Unknown error')}")
    return 1


def cmd_health(args):
    """Check system health"""
    result = make_request("/api/health", api_url=args.api_url)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    overall = result.get("overall_status", "unknown")
    icon = {"healthy": "[OK]", "degraded": "[WARN]", "unhealthy": "[FAIL]"}.get(overall, "[?]")

    print(f"\nSystem Health: {icon} {overall.upper()}")

    components = result.get("components", {})
    for name, status in components.items():
        comp_icon = "[OK]" if status.get("healthy") else "[FAIL]"
        print(f"  {comp_icon} {name}")
    print()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Trading Bot CLI Controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  cli.py status              Show bot status
  cli.py trading on          Enable trading
  cli.py trading off         Disable trading
  cli.py stop                Emergency stop
  cli.py balance             Show balance and P&L
  cli.py leverage            Show current leverage
  cli.py leverage 10         Set leverage to 10x
  cli.py hedge               Show hedge mode
  cli.py hedge on            Enable hedge mode
  cli.py pairs               List trading pairs
  cli.py add BTCUSDT         Add trading pair
  cli.py remove BTCUSDT      Remove trading pair
  cli.py strategies          List strategies
  cli.py stats               Show trading statistics
  cli.py reload              Reload configuration
  cli.py health              Check system health
""",
    )

    parser.add_argument(
        "--api-url", default=DEFAULT_API_URL, help=f"API URL (default: {DEFAULT_API_URL})"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # status
    subparsers.add_parser("status", help="Show bot status")

    # trading
    trading_parser = subparsers.add_parser("trading", help="Enable/disable trading")
    trading_parser.add_argument("action", choices=["on", "off"], help="on or off")

    # stop
    subparsers.add_parser("stop", help="Emergency stop")

    # balance
    subparsers.add_parser("balance", help="Show balance and P&L")

    # leverage
    leverage_parser = subparsers.add_parser("leverage", help="View or set leverage")
    leverage_parser.add_argument("value", type=int, nargs="?", help="Leverage value (1-100)")

    # hedge
    hedge_parser = subparsers.add_parser("hedge", help="View or set hedge mode")
    hedge_parser.add_argument("action", nargs="?", choices=["on", "off"], help="on or off")

    # pairs
    subparsers.add_parser("pairs", help="List trading pairs")

    # add
    add_parser = subparsers.add_parser("add", help="Add trading pair")
    add_parser.add_argument("symbol", help="Symbol to add (e.g., BTCUSDT)")

    # remove
    remove_parser = subparsers.add_parser("remove", help="Remove trading pair")
    remove_parser.add_argument("symbol", help="Symbol to remove")

    # strategies
    subparsers.add_parser("strategies", help="List strategies")

    # stats
    subparsers.add_parser("stats", help="Show trading statistics")

    # reload
    subparsers.add_parser("reload", help="Reload configuration")

    # health
    subparsers.add_parser("health", help="Check system health")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Route to command handler
    commands = {
        "status": cmd_status,
        "trading": cmd_trading,
        "stop": cmd_stop,
        "balance": cmd_balance,
        "leverage": cmd_leverage,
        "hedge": cmd_hedge,
        "pairs": cmd_pairs,
        "add": cmd_add_pair,
        "remove": cmd_remove_pair,
        "strategies": cmd_strategies,
        "stats": cmd_stats,
        "reload": cmd_reload,
        "health": cmd_health,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)
    else:
        print(f"Unknown command: {args.command}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
