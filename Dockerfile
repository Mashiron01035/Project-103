# ======================================================================================================================
# Multi-stage Dockerfile for Trading Bot
# ======================================================================================================================

# ======================================================================================================================
# Stage 1: Builder - compile dependencies
# ======================================================================================================================
FROM python:3.12-slim-bookworm AS builder

# System dependencies needed for compilation
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    cmake \
    wget \
    curl \
    git \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    libgfortran5 \
    libopenblas-dev \
    liblapack-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install TA-Lib (required for technical indicators)
# Update config.guess and config.sub for ARM64/aarch64 support
RUN wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz && \
    tar -xzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib && \
    wget -O config.guess 'https://git.savannah.gnu.org/cgit/config.git/plain/config.guess' && \
    wget -O config.sub 'https://git.savannah.gnu.org/cgit/config.git/plain/config.sub' && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd .. && \
    rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

# Install Poetry
ENV POETRY_VERSION=1.7.1 \
    POETRY_HOME=/opt/poetry \
    POETRY_VIRTUALENVS_CREATE=true \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry

WORKDIR /build

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install dependencies
RUN poetry install --no-root --only main

# ======================================================================================================================
# Stage 2: Runtime - final image
# ======================================================================================================================
FROM python:3.12-slim-bookworm AS runtime

# Metadata
LABEL maintainer="mashiron@tuta.io" \
      description="TradingBot" \
      version="1.0.0"

# Runtime dependencies only
RUN apt-get update && apt-get install -y \
    libpq5 \
    libgomp1 \
    libgfortran5 \
    libopenblas0 \
    liblapack3 \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create the user that runs the bot
RUN groupadd -g 1000 trader && \
    useradd -r -u 1000 -g trader -s /bin/bash -m trader

WORKDIR /app

# Copy TA-Lib from the builder
COPY --from=builder /usr/lib/libta_lib.so* /usr/lib/
COPY --from=builder /usr/include/ta-lib /usr/include/ta-lib

# Copy the poetry virtualenv from the builder to /app/.venv
COPY --from=builder /build/.venv /app/.venv

# Make Python use this venv
ENV PATH="/app/.venv/bin:$PATH"

# Create the base directories
RUN mkdir -p /app/logs /app/data /app/reports /app/config && \
    chown -R trader:trader /app

# Copy the application code
COPY --chown=trader:trader src/ ./src/
COPY --chown=trader:trader config/ ./config/
COPY --chown=trader:trader scripts/ ./scripts/
COPY --chown=trader:trader pyproject.toml ./pyproject.toml
COPY --chown=trader:trader poetry.lock* ./

# Set the time zone
ENV TZ=UTC

# Default environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:$PYTHONPATH \
    LOG_LEVEL=INFO \
    ENVIRONMENT=production

# Switch to the trader user
USER trader

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.path.insert(0, '/app'); from src.utils.health_check import check_health; exit(0 if check_health() else 1)"

# Ports for the dashboard and API
EXPOSE 8000 8080

# Run the bot
CMD ["python", "-m", "src.main"]

# ======================================================================================================================
# Stage 3: Development - image for development
# ======================================================================================================================
FROM runtime AS development

USER root

# Additional development tools
RUN apt-get update && apt-get install -y \
    vim \
    nano \
    htop \
    net-tools \
    iputils-ping \
    telnet \
    postgresql-client \
    redis-tools \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry for development
ENV POETRY_VERSION=1.7.1 \
    POETRY_HOME=/opt/poetry \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry

WORKDIR /app

# Install extra packages directly (the venv is already on PATH)
RUN pip install --no-cache-dir \
    psycopg2-binary \
    jinja2 \
    debugpy

# Permissions for trader
RUN chown -R trader:trader /app

USER trader

ENV ENVIRONMENT=development \
    LOG_LEVEL=DEBUG

# Run in development mode
CMD ["python", "-m", "src.main"]

# ======================================================================================================================
# Stage 4: Testing - image for running tests
# ======================================================================================================================
FROM development AS testing

USER root

# Additional test utilities
RUN pip install pytest-html pytest-benchmark

USER trader

# Run the tests by default
CMD ["pytest", "-v", "--cov=src", "--cov-report=term-missing", "--cov-report=html"]
