# Makefile for Trading Bot Project
# Usage: make [target]

# =============================================================================
# Variables
# =============================================================================
PROJECT_NAME := Project-103
PYTHON_VERSION := 3.10
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
POETRY := poetry
DOCKER_COMPOSE := docker compose
SRC_DIR := src
TEST_DIR := tests
SCRIPT_DIR := scripts

# Colors for output
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
BLUE := \033[0;34m
NC := \033[0m # No Color

# Default target
.DEFAULT_GOAL := help

# =============================================================================
# Help
# =============================================================================
.PHONY: help
help: ## Show this help message
	@echo "$(BLUE)Trading Bot - Available Commands$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(YELLOW)Examples:$(NC)"
	@echo "  make install        - Install all dependencies"
	@echo "  make run           - Run the trading bot"
	@echo "  make test          - Run all tests"
	@echo "  make docker-up     - Start all services with Docker"

# =============================================================================
# Poetry & Dependencies
# =============================================================================
.PHONY: install-poetry
install-poetry: ## Install Poetry
	@echo "$(BLUE)Installing Poetry...$(NC)"
	@curl -sSL https://install.python-poetry.org | python3 -
	@echo "$(GREEN)Poetry installed successfully$(NC)"

.PHONY: install
install: ## Install dependencies with Poetry
	@echo "$(BLUE)Installing dependencies...$(NC)"
	@$(POETRY) install
	@echo "$(GREEN)Dependencies installed successfully$(NC)"

.PHONY: install-dev
install-dev: ## Install with dev dependencies
	@echo "$(BLUE)Installing dev dependencies...$(NC)"
	@$(POETRY) install --with dev
	@echo "$(GREEN)Dev dependencies installed successfully$(NC)"

.PHONY: update
update: ## Update dependencies
	@echo "$(BLUE)Updating dependencies...$(NC)"
	@$(POETRY) update
	@echo "$(GREEN)Dependencies updated successfully$(NC)"

.PHONY: lock
lock: ## Update poetry.lock
	@echo "$(BLUE)Updating poetry.lock...$(NC)"
	@$(POETRY) lock --no-update
	@echo "$(GREEN)poetry.lock updated$(NC)"

# =============================================================================
# Development
# =============================================================================
.PHONY: run
run: ## Run the trading bot
	@echo "$(BLUE)Starting Trading Bot...$(NC)"
	@$(POETRY) run python -m src.main

.PHONY: run-dev
run-dev: ## Run in development mode with hot reload
	@echo "$(BLUE)Starting Trading Bot in dev mode...$(NC)"
	@$(POETRY) run python -m src.main --dev --reload

.PHONY: shell
shell: ## Open Python shell with project context
	@echo "$(BLUE)Opening Python shell...$(NC)"
	@$(POETRY) run ipython

.PHONY: jupyter
jupyter: ## Start Jupyter Lab
	@echo "$(BLUE)Starting Jupyter Lab...$(NC)"
	@$(POETRY) run jupyter lab --notebook-dir=notebooks

# =============================================================================
# Database
# =============================================================================
.PHONY: db-create
db-create: ## Create database
	@echo "$(BLUE)Creating database...$(NC)"
	@$(POETRY) run python scripts/setup_database.py
	@echo "$(GREEN)Database created successfully$(NC)"

.PHONY: db-migrate
db-migrate: ## Run database migrations
	@echo "$(BLUE)Running migrations...$(NC)"
	@$(POETRY) run alembic upgrade head
	@echo "$(GREEN)Migrations completed$(NC)"

.PHONY: db-rollback
db-rollback: ## Rollback last migration
	@echo "$(YELLOW)Rolling back migration...$(NC)"
	@$(POETRY) run alembic downgrade -1

.PHONY: db-reset
db-reset: ## Reset database (drop and recreate)
	@echo "$(RED)Resetting database...$(NC)"
	@$(POETRY) run python scripts/reset_database.py
	@echo "$(GREEN)Database reset completed$(NC)"

.PHONY: db-backup
db-backup: ## Backup database
	@echo "$(BLUE)Creating database backup...$(NC)"
	@mkdir -p backups
	@docker exec trading-postgres pg_dump -U trader trading_bot > backups/backup_$(shell date +%Y%m%d_%H%M%S).sql
	@echo "$(GREEN)Backup created successfully$(NC)"

# =============================================================================
# Testing
# =============================================================================
.PHONY: test
test: ## Run all tests
	@echo "$(BLUE)Running tests...$(NC)"
	@$(POETRY) run pytest tests/ -v --tb=short

.PHONY: test-unit
test-unit: ## Run unit tests only
	@echo "$(BLUE)Running unit tests...$(NC)"
	@$(POETRY) run pytest tests/unit/ -v --tb=short

.PHONY: test-integration
test-integration: ## Run integration tests
	@echo "$(BLUE)Running integration tests...$(NC)"
	@$(POETRY) run pytest tests/integration/ -v --tb=short

.PHONY: test-e2e
test-e2e: ## Run end-to-end tests
	@echo "$(BLUE)Running e2e tests...$(NC)"
	@$(POETRY) run pytest tests/e2e/ -v --tb=short

.PHONY: test-cov
test-cov: ## Run tests with coverage
	@echo "$(BLUE)Running tests with coverage...$(NC)"
	@$(POETRY) run pytest tests/ --cov=src --cov-report=term-missing --cov-report=html
	@echo "$(GREEN)Coverage report generated in htmlcov/$(NC)"

.PHONY: test-watch
test-watch: ## Run tests in watch mode
	@echo "$(BLUE)Running tests in watch mode...$(NC)"
	@$(POETRY) run ptw tests/ -- -v

# =============================================================================
# Code Quality
# =============================================================================
.PHONY: format
format: ## Format code with black and isort
	@echo "$(BLUE)Formatting code...$(NC)"
	@$(POETRY) run black $(SRC_DIR) $(TEST_DIR) $(SCRIPT_DIR)
	@$(POETRY) run isort $(SRC_DIR) $(TEST_DIR) $(SCRIPT_DIR)
	@echo "$(GREEN)Code formatted successfully$(NC)"

.PHONY: lint
lint: ## Run linters (ruff + mypy)
	@echo "$(BLUE)Running linters...$(NC)"
	@$(POETRY) run ruff check $(SRC_DIR) $(TEST_DIR) $(SCRIPT_DIR)
	@$(POETRY) run mypy $(SRC_DIR)
	@echo "$(GREEN)Linting completed$(NC)"

.PHONY: security
security: ## Run security checks with bandit
	@echo "$(BLUE)Running security checks...$(NC)"
	@$(POETRY) run bandit -r $(SRC_DIR)
	@echo "$(GREEN)Security check completed$(NC)"

.PHONY: check
check: format lint test ## Run all checks (format, lint, test)
	@echo "$(GREEN)All checks passed!$(NC)"

.PHONY: pre-commit
pre-commit: ## Run pre-commit hooks
	@echo "$(BLUE)Running pre-commit hooks...$(NC)"
	@$(POETRY) run pre-commit run --all-files

.PHONY: install-pre-commit
install-pre-commit: ## Install pre-commit hooks
	@echo "$(BLUE)Installing pre-commit hooks...$(NC)"
	@$(POETRY) run pre-commit install
	@echo "$(GREEN)Pre-commit hooks installed$(NC)"

# =============================================================================
# Docker - Development
# =============================================================================
.PHONY: up
up: ## Start dev environment (alias for docker-dev)
	@$(MAKE) docker-dev

.PHONY: down
down: ## Stop all containers
	@$(MAKE) docker-down

.PHONY: docker-dev
docker-dev: ## Start development environment
	@echo "$(BLUE)Starting dev environment...$(NC)"
	@$(DOCKER_COMPOSE) --profile dev up -d
	@echo "$(GREEN)Dev environment started!$(NC)"
	@echo "$(YELLOW)Services:$(NC)"
	@echo "  - Dashboard:   http://localhost:8080"
	@echo "  - API:         http://localhost:8000"
	@echo "  - Grafana:     http://localhost:3000 (admin/admin)"
	@echo "  - Prometheus:  http://localhost:9090"
	@echo "  - Debug port:  5678"

.PHONY: docker-dev-logs
docker-dev-logs: ## Start dev with logs attached
	@$(DOCKER_COMPOSE) --profile dev up

.PHONY: docker-prod
docker-prod: ## Start production environment
	@echo "$(BLUE)Starting production environment...$(NC)"
	@$(DOCKER_COMPOSE) --profile prod up -d
	@echo "$(GREEN)Production environment started$(NC)"

.PHONY: docker-test
docker-test: ## Run tests in Docker
	@echo "$(BLUE)Running tests...$(NC)"
	@$(DOCKER_COMPOSE) --profile test run --rm test-runner

.PHONY: docker-tools
docker-tools: ## Start dev + admin tools (pgadmin, redis-insight)
	@echo "$(BLUE)Starting dev with tools...$(NC)"
	@$(DOCKER_COMPOSE) --profile dev --profile tools up -d
	@echo "$(GREEN)Tools started!$(NC)"
	@echo "  - PgAdmin:      http://localhost:5050"
	@echo "  - RedisInsight: http://localhost:8001"

# =============================================================================
# Docker - Management
# =============================================================================
.PHONY: docker-build
docker-build: ## Build all Docker images
	@echo "$(BLUE)Building Docker images...$(NC)"
	@$(DOCKER_COMPOSE) build
	@echo "$(GREEN)Docker images built successfully$(NC)"

.PHONY: docker-build-dev
docker-build-dev: ## Build dev image only
	@$(DOCKER_COMPOSE) build trading-bot-dev

.PHONY: docker-build-no-cache
docker-build-no-cache: ## Build without cache
	@$(DOCKER_COMPOSE) build --no-cache

.PHONY: docker-down
docker-down: ## Stop all Docker services
	@echo "$(YELLOW)Stopping Docker services...$(NC)"
	@$(DOCKER_COMPOSE) --profile dev --profile prod --profile test --profile tools down
	@echo "$(GREEN)Services stopped$(NC)"

.PHONY: docker-restart
docker-restart: docker-down docker-dev ## Restart dev environment
	@echo "$(GREEN)Dev environment restarted$(NC)"

.PHONY: docker-logs
docker-logs: ## Show dev container logs (follow)
	@$(DOCKER_COMPOSE) --profile dev logs -f trading-bot-dev

.PHONY: docker-logs-all
docker-logs-all: ## Show all services logs
	@$(DOCKER_COMPOSE) --profile dev logs -f

.PHONY: docker-clean
docker-clean: ## Clean Docker resources (containers + volumes)
	@echo "$(RED)Cleaning Docker resources...$(NC)"
	@$(DOCKER_COMPOSE) --profile dev --profile prod --profile test --profile tools down -v
	@docker system prune -f
	@echo "$(GREEN)Docker resources cleaned$(NC)"

.PHONY: docker-shell
docker-shell: ## Open bash in dev container
	@$(DOCKER_COMPOSE) --profile dev exec trading-bot-dev bash

.PHONY: docker-shell-root
docker-shell-root: ## Open root bash in dev container
	@$(DOCKER_COMPOSE) --profile dev exec -u root trading-bot-dev bash

.PHONY: docker-status
docker-status: ## Show Docker services status
	@echo "$(BLUE)Docker services status:$(NC)"
	@$(DOCKER_COMPOSE) ps -a

.PHONY: docker-health
docker-health: ## Show services health
	@$(DOCKER_COMPOSE) ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

# =============================================================================
# Docker - Database & Redis
# =============================================================================
.PHONY: db-shell
db-shell: ## Open PostgreSQL shell
	@$(DOCKER_COMPOSE) exec postgres psql -U trader -d project103

.PHONY: redis-shell
redis-shell: ## Open Redis CLI
	@$(DOCKER_COMPOSE) exec redis redis-cli

# =============================================================================
# Trading Bot Commands
# =============================================================================
.PHONY: backtest
backtest: ## Run backtest
	@echo "$(BLUE)Running backtest...$(NC)"
	@$(POETRY) run python scripts/backtest.py

.PHONY: optimize
optimize: ## Optimize strategy parameters
	@echo "$(BLUE)Running strategy optimization...$(NC)"
	@$(POETRY) run python scripts/optimize_strategy.py

.PHONY: download-data
download-data: ## Download historical data
	@echo "$(BLUE)Downloading historical data...$(NC)"
	@$(POETRY) run python scripts/download_historical.py

.PHONY: analyze
analyze: ## Analyze performance
	@echo "$(BLUE)Analyzing performance...$(NC)"
	@$(POETRY) run python scripts/analyze_performance.py

.PHONY: report
report: ## Generate trading report
	@echo "$(BLUE)Generating report...$(NC)"
	@$(POETRY) run python scripts/generate_report.py

# =============================================================================
# Monitoring
# =============================================================================
.PHONY: monitor
monitor: ## Open monitoring dashboard
	@echo "$(BLUE)Opening monitoring dashboards...$(NC)"
	@open http://localhost:3000 || xdg-open http://localhost:3000 || echo "Grafana: http://localhost:3000"
	@open http://localhost:8080 || xdg-open http://localhost:8080 || echo "Dashboard: http://localhost:8080"

.PHONY: health
health: ## Check system health
	@echo "$(BLUE)Checking system health...$(NC)"
	@$(POETRY) run python scripts/health_check.py

.PHONY: metrics
metrics: ## Show current metrics
	@echo "$(BLUE)Current metrics:$(NC)"
	@curl -s http://localhost:8000/metrics | head -20

# =============================================================================
# Documentation
# =============================================================================
.PHONY: docs
docs: ## Build documentation
	@echo "$(BLUE)Building documentation...$(NC)"
	@$(POETRY) run mkdocs build
	@echo "$(GREEN)Documentation built in site/$(NC)"

.PHONY: docs-serve
docs-serve: ## Serve documentation locally
	@echo "$(BLUE)Serving documentation...$(NC)"
	@$(POETRY) run mkdocs serve

# =============================================================================
# Cleanup
# =============================================================================
.PHONY: clean
clean: ## Clean temporary files and caches
	@echo "$(YELLOW)Cleaning temporary files...$(NC)"
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@find . -type f -name "*.pyo" -delete
	@find . -type f -name "*.coverage" -delete
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf htmlcov/
	@rm -rf dist/
	@rm -rf build/
	@echo "$(GREEN)Cleanup completed$(NC)"

.PHONY: clean-all
clean-all: clean docker-clean ## Clean everything including Docker
	@echo "$(GREEN)Full cleanup completed$(NC)"

# =============================================================================
# Release
# =============================================================================
.PHONY: version
version: ## Show current version
	@echo "$(BLUE)Current version:$(NC)"
	@$(POETRY) version

.PHONY: version-patch
version-patch: ## Bump patch version
	@echo "$(BLUE)Bumping patch version...$(NC)"
	@$(POETRY) version patch
	@echo "$(GREEN)Version bumped$(NC)"

.PHONY: version-minor
version-minor: ## Bump minor version
	@echo "$(BLUE)Bumping minor version...$(NC)"
	@$(POETRY) version minor
	@echo "$(GREEN)Version bumped$(NC)"

.PHONY: version-major
version-major: ## Bump major version
	@echo "$(BLUE)Bumping major version...$(NC)"
	@$(POETRY) version major
	@echo "$(GREEN)Version bumped$(NC)"

# =============================================================================
# Quick Commands
# =============================================================================
.PHONY: setup
setup: install-poetry install install-pre-commit ## Complete project setup
	@echo "$(GREEN)Project setup completed!$(NC)"
	@echo ""
	@echo "$(YELLOW)Next steps:$(NC)"
	@echo "1. Copy .env.example to .env and configure"
	@echo "2. Run 'make up' to start dev environment"

.PHONY: dev
dev: docker-dev ## Start development environment (Docker)

.PHONY: prod
prod: docker-prod ## Start production environment (Docker)

.PHONY: reset
reset: docker-clean clean ## Reset everything to clean state
	@echo "$(GREEN)System reset completed$(NC)"

.PHONY: logs
logs: docker-logs ## Show logs (alias)

.PHONY: ps
ps: docker-status ## Show status (alias)

.PHONY: restart
restart: docker-restart ## Restart dev environment (alias)

# Prevent Make from trying to remake this makefile
Makefile: ;
@: ;

# Set shell for commands
SHELL := /bin/bash