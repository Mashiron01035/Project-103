#!/bin/bash
#
# Trading Bot CLI Wrapper
# Usage: ./bot.sh <command> [args...]
#
# Examples:
#   ./bot.sh status
#   ./bot.sh trading on
#   ./bot.sh leverage 10
#   ./bot.sh balance
#

CONTAINER_NAME="${BOT_CONTAINER:-trading-bot}"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Error: Container '${CONTAINER_NAME}' is not running"
    echo "Start with: docker compose --profile prod up -d"
    exit 1
fi

# Execute CLI in container
docker exec "${CONTAINER_NAME}" python -m scripts.cli "$@"
