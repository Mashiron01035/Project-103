-- =============================================================================
-- Trading Bot Database Initialization Script
-- Project-103
-- =============================================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- SCHEMAS
-- =============================================================================
CREATE SCHEMA IF NOT EXISTS market_data;
CREATE SCHEMA IF NOT EXISTS trading;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS config;

-- =============================================================================
-- MARKET_DATA TABLES (TimescaleDB hypertables)
-- =============================================================================

-- Candles (OHLCV)
CREATE TABLE IF NOT EXISTS market_data.candles (
    timestamp TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    open DECIMAL(20,8) NOT NULL,
    high DECIMAL(20,8) NOT NULL,
    low DECIMAL(20,8) NOT NULL,
    close DECIMAL(20,8) NOT NULL,
    volume DECIMAL(30,8) NOT NULL,
    quote_volume DECIMAL(30,8),
    trades_count INTEGER,
    taker_buy_volume DECIMAL(30,8),
    taker_buy_quote_volume DECIMAL(30,8),
    exchange VARCHAR(50),
    source VARCHAR(50),
    is_closed BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT candles_pkey PRIMARY KEY (timestamp, symbol, timeframe)
);

-- Turn into a hypertable
SELECT create_hypertable('market_data.candles', 'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_candles_symbol_timeframe
    ON market_data.candles(symbol, timeframe, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_candles_created_at
    ON market_data.candles(created_at);

-- Compression policy (after 7 days)
ALTER TABLE market_data.candles SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol,timeframe',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('market_data.candles', INTERVAL '7 days', if_not_exists => TRUE);

-- Retention policy (after 2 years)
SELECT add_retention_policy('market_data.candles', INTERVAL '2 years', if_not_exists => TRUE);

-- Tickers
CREATE TABLE IF NOT EXISTS market_data.tickers (
    timestamp TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    bid_price DECIMAL(20,8),
    bid_size DECIMAL(30,8),
    ask_price DECIMAL(20,8),
    ask_size DECIMAL(30,8),
    last_price DECIMAL(20,8) NOT NULL,
    last_size DECIMAL(30,8),
    volume_24h DECIMAL(30,8),
    quote_volume_24h DECIMAL(30,8),
    price_change_24h DECIMAL(10,4),
    price_change_percent_24h DECIMAL(10,4),
    high_24h DECIMAL(20,8),
    low_24h DECIMAL(20,8),
    open_24h DECIMAL(20,8),
    open_interest DECIMAL(30,8),
    exchange VARCHAR(50),
    source VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT tickers_pkey PRIMARY KEY (timestamp, symbol)
);

SELECT create_hypertable('market_data.tickers', 'timestamp',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_tickers_symbol
    ON market_data.tickers(symbol, timestamp DESC);

-- Policies for tickers
ALTER TABLE market_data.tickers SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('market_data.tickers', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('market_data.tickers', INTERVAL '30 days', if_not_exists => TRUE);

-- Order book (snapshots)
CREATE TABLE IF NOT EXISTS market_data.orderbook_snapshots (
    timestamp TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    bids JSONB NOT NULL,
    asks JSONB NOT NULL,
    sequence_id BIGINT,
    is_snapshot BOOLEAN DEFAULT TRUE,
    exchange VARCHAR(50),
    source VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT orderbook_pkey PRIMARY KEY (timestamp, symbol)
);

SELECT create_hypertable('market_data.orderbook_snapshots', 'timestamp',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_orderbook_symbol
    ON market_data.orderbook_snapshots(symbol, timestamp DESC);

-- Orderbook retention policy (keep only 7 days)
SELECT add_retention_policy('market_data.orderbook_snapshots', INTERVAL '7 days', if_not_exists => TRUE);

-- Exchange trades
CREATE TABLE IF NOT EXISTS market_data.trades (
    id UUID DEFAULT uuid_generate_v4(),
    timestamp TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    trade_id VARCHAR(50),
    price DECIMAL(20,8) NOT NULL,
    quantity DECIMAL(30,8) NOT NULL,
    side VARCHAR(10) NOT NULL,
    is_buyer_maker BOOLEAN,
    exchange VARCHAR(50),
    source VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT trades_pkey PRIMARY KEY (timestamp, symbol, id)
);

SELECT create_hypertable('market_data.trades', 'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_trades_symbol
    ON market_data.trades(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_trades_trade_id
    ON market_data.trades(trade_id) WHERE trade_id IS NOT NULL;

-- Policies for trades
ALTER TABLE market_data.trades SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('market_data.trades', INTERVAL '3 days', if_not_exists => TRUE);
SELECT add_retention_policy('market_data.trades', INTERVAL '90 days', if_not_exists => TRUE);

-- Indicators (computed values)
CREATE TABLE IF NOT EXISTS market_data.indicators (
    timestamp TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    indicator_name VARCHAR(50) NOT NULL,
    values JSONB NOT NULL,
    parameters JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT indicators_pkey PRIMARY KEY (timestamp, symbol, timeframe, indicator_name)
);

SELECT create_hypertable('market_data.indicators', 'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_indicators_lookup
    ON market_data.indicators(symbol, timeframe, indicator_name, timestamp DESC);

SELECT add_retention_policy('market_data.indicators', INTERVAL '30 days', if_not_exists => TRUE);

-- =============================================================================
-- TRADING TABLES
-- =============================================================================

-- Strategies
CREATE TABLE IF NOT EXISTS trading.strategies (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    version VARCHAR(20) NOT NULL,
    type VARCHAR(50),
    description TEXT,
    config JSONB NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategies_active
    ON trading.strategies(is_active) WHERE is_active = TRUE;

-- =============================================================================
-- NOTE: The following tables are managed by SQLAlchemy (src/data/storage/models.py):
--   - trading.signals
--   - trading.orders
--   - trading.balance_history
-- DO NOT create them here - SQLAlchemy creates them automatically when the bot starts.
-- The definitions below are commented out for reference.
-- =============================================================================

/*
-- Signals (MANAGED BY SQLALCHEMY - DO NOT UNCOMMENT)
CREATE TABLE IF NOT EXISTS trading.signals (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    strategy_id UUID REFERENCES trading.strategies(id),
    timeframe VARCHAR(10) NOT NULL,
    side VARCHAR(10) NOT NULL,
    type VARCHAR(20) NOT NULL,
    confidence DECIMAL(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    strength INTEGER,
    entry_price DECIMAL(20,8),
    stop_loss DECIMAL(20,8),
    take_profits JSONB,
    risk_reward_ratio DECIMAL(10,2),
    position_size_percent DECIMAL(5,4),
    indicators_snapshot JSONB,
    metadata JSONB,
    is_executed BOOLEAN DEFAULT FALSE,
    executed_at TIMESTAMPTZ,
    execution_result JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_timestamp
    ON trading.signals(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signals_symbol
    ON trading.signals(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signals_strategy
    ON trading.signals(strategy_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signals_not_executed
    ON trading.signals(symbol, is_executed) WHERE is_executed = FALSE;

-- Orders (MANAGED BY SQLALCHEMY - DO NOT UNCOMMENT)
CREATE TABLE IF NOT EXISTS trading.orders (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    exchange_order_id VARCHAR(100) UNIQUE,
    signal_id UUID REFERENCES trading.signals(id),
    position_id UUID,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    type VARCHAR(30) NOT NULL,
    quantity DECIMAL(30,8) NOT NULL,
    price DECIMAL(20,8),
    stop_price DECIMAL(20,8),
    executed_quantity DECIMAL(30,8) DEFAULT 0,
    executed_avg_price DECIMAL(20,8),
    status VARCHAR(20) NOT NULL,
    time_in_force VARCHAR(10),
    reduce_only BOOLEAN DEFAULT FALSE,
    close_position BOOLEAN DEFAULT FALSE,
    activation_price DECIMAL(20,8),
    callback_rate DECIMAL(5,4),
    working_type VARCHAR(20),
    price_protect BOOLEAN DEFAULT FALSE,
    reject_reason TEXT,
    commission DECIMAL(20,8),
    commission_asset VARCHAR(20),
    exchange VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    executed_at TIMESTAMPTZ,
    canceled_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol
    ON trading.orders(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status
    ON trading.orders(status) WHERE status IN ('NEW', 'PARTIALLY_FILLED');
CREATE INDEX IF NOT EXISTS idx_orders_position
    ON trading.orders(position_id) WHERE position_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_orders_signal
    ON trading.orders(signal_id) WHERE signal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_orders_exchange_id
    ON trading.orders(exchange_order_id) WHERE exchange_order_id IS NOT NULL;
*/

-- Positions
CREATE TABLE IF NOT EXISTS trading.positions (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    strategy_id UUID REFERENCES trading.strategies(id),
    side VARCHAR(10) NOT NULL,
    quantity DECIMAL(30,8) NOT NULL,
    entry_price DECIMAL(20,8) NOT NULL,
    entry_value DECIMAL(30,8) NOT NULL,
    current_price DECIMAL(20,8),
    current_value DECIMAL(30,8),
    realized_pnl DECIMAL(20,8) DEFAULT 0,
    unrealized_pnl DECIMAL(20,8) DEFAULT 0,
    pnl_percent DECIMAL(10,4),
    commission_paid DECIMAL(20,8) DEFAULT 0,
    stop_loss DECIMAL(20,8),
    take_profits JSONB,
    trailing_stop_distance DECIMAL(10,4),
    highest_price DECIMAL(20,8),
    lowest_price DECIMAL(20,8),
    max_drawdown DECIMAL(10,4),
    status VARCHAR(20) NOT NULL,
    open_orders_count INTEGER DEFAULT 0,
    partial_closes JSONB,
    leverage DECIMAL(10,2) DEFAULT 1,
    margin_type VARCHAR(20),
    exchange VARCHAR(50),
    metadata JSONB,
    opened_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_positions_status
    ON trading.positions(status) WHERE status != 'CLOSED';
CREATE INDEX IF NOT EXISTS idx_positions_symbol
    ON trading.positions(symbol, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_positions_strategy
    ON trading.positions(strategy_id, opened_at DESC);

-- Order execution history
CREATE TABLE IF NOT EXISTS trading.order_fills (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    order_id UUID REFERENCES trading.orders(id),
    exchange_trade_id VARCHAR(100) UNIQUE,
    symbol VARCHAR(20) NOT NULL,
    price DECIMAL(20,8) NOT NULL,
    quantity DECIMAL(30,8) NOT NULL,
    commission DECIMAL(20,8),
    commission_asset VARCHAR(20),
    timestamp TIMESTAMPTZ NOT NULL,
    is_maker BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fills_order
    ON trading.order_fills(order_id);
CREATE INDEX IF NOT EXISTS idx_fills_timestamp
    ON trading.order_fills(timestamp DESC);

-- Balance and account
CREATE TABLE IF NOT EXISTS trading.account_balance (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    asset VARCHAR(20) NOT NULL,
    free DECIMAL(30,8) NOT NULL,
    locked DECIMAL(30,8) NOT NULL,
    total DECIMAL(30,8) NOT NULL,
    in_orders DECIMAL(30,8) DEFAULT 0,
    in_positions DECIMAL(30,8) DEFAULT 0,
    unrealized_pnl DECIMAL(20,8) DEFAULT 0,
    exchange VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(timestamp, asset, exchange)
);

CREATE INDEX IF NOT EXISTS idx_balance_timestamp
    ON trading.account_balance(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_balance_asset
    ON trading.account_balance(asset, timestamp DESC);

-- PnL history
CREATE TABLE IF NOT EXISTS trading.pnl_history (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    position_id UUID REFERENCES trading.positions(id),
    symbol VARCHAR(20) NOT NULL,
    strategy_id UUID REFERENCES trading.strategies(id),
    pnl DECIMAL(20,8) NOT NULL,
    pnl_percent DECIMAL(10,4) NOT NULL,
    commission DECIMAL(20,8) DEFAULT 0,
    is_realized BOOLEAN NOT NULL,
    trade_type VARCHAR(20),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pnl_timestamp
    ON trading.pnl_history(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_pnl_position
    ON trading.pnl_history(position_id);
CREATE INDEX IF NOT EXISTS idx_pnl_strategy
    ON trading.pnl_history(strategy_id, timestamp DESC);

-- =============================================================================
-- ANALYTICS TABLES
-- =============================================================================

-- Strategy performance
CREATE TABLE IF NOT EXISTS analytics.strategy_performance (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    strategy_id UUID REFERENCES trading.strategies(id),
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    total_trades INTEGER NOT NULL DEFAULT 0,
    winning_trades INTEGER NOT NULL DEFAULT 0,
    losing_trades INTEGER NOT NULL DEFAULT 0,
    win_rate DECIMAL(5,4),
    average_win DECIMAL(20,8),
    average_loss DECIMAL(20,8),
    profit_factor DECIMAL(10,4),
    total_pnl DECIMAL(20,8),
    total_commission DECIMAL(20,8),
    net_pnl DECIMAL(20,8),
    roi DECIMAL(10,4),
    sharpe_ratio DECIMAL(10,4),
    sortino_ratio DECIMAL(10,4),
    max_drawdown DECIMAL(10,4),
    max_drawdown_duration INTERVAL,
    average_trade_duration INTERVAL,
    best_trade JSONB,
    worst_trade JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(strategy_id, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_perf_strategy
    ON analytics.strategy_performance(strategy_id, period_start DESC);

-- Daily statistics
CREATE TABLE IF NOT EXISTS analytics.daily_statistics (
    date DATE NOT NULL,
    symbol VARCHAR(20),
    total_volume DECIMAL(30,8),
    total_trades INTEGER,
    total_pnl DECIMAL(20,8),
    realized_pnl DECIMAL(20,8),
    unrealized_pnl DECIMAL(20,8),
    total_commission DECIMAL(20,8),
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    positions_opened INTEGER DEFAULT 0,
    positions_closed INTEGER DEFAULT 0,
    max_drawdown DECIMAL(10,4),
    ending_balance DECIMAL(30,8),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_daily_stats_date
    ON analytics.daily_statistics(date DESC);

-- Risk metrics
CREATE TABLE IF NOT EXISTS analytics.risk_metrics (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    portfolio_value DECIMAL(30,8),
    cash_balance DECIMAL(30,8),
    positions_value DECIMAL(30,8),
    total_exposure DECIMAL(30,8),
    leverage DECIMAL(10,4),
    var_95 DECIMAL(20,8),
    var_99 DECIMAL(20,8),
    expected_shortfall DECIMAL(20,8),
    beta DECIMAL(10,4),
    correlation_matrix JSONB,
    concentration_risk JSONB,
    max_position_size DECIMAL(30,8),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_timestamp
    ON analytics.risk_metrics(timestamp DESC);

-- =============================================================================
-- AUDIT TABLES
-- =============================================================================

-- Action logs
CREATE TABLE IF NOT EXISTS audit.action_logs (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    action_type VARCHAR(50) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(100),
    user_id VARCHAR(100),
    ip_address INET,
    user_agent TEXT,
    request_data JSONB,
    response_data JSONB,
    status VARCHAR(20),
    error_message TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON audit.action_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_entity
    ON audit.action_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_action
    ON audit.action_logs(action_type, timestamp DESC);

-- Configuration changes
CREATE TABLE IF NOT EXISTS audit.config_changes (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    config_type VARCHAR(50) NOT NULL,
    config_key VARCHAR(100) NOT NULL,
    old_value JSONB,
    new_value JSONB,
    changed_by VARCHAR(100),
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_config_changes_timestamp
    ON audit.config_changes(timestamp DESC);

-- System errors
CREATE TABLE IF NOT EXISTS audit.system_errors (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    error_type VARCHAR(100) NOT NULL,
    error_message TEXT NOT NULL,
    error_details JSONB,
    stack_trace TEXT,
    component VARCHAR(50),
    severity VARCHAR(20),
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    resolution_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_errors_timestamp
    ON audit.system_errors(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_errors_unresolved
    ON audit.system_errors(resolved, severity) WHERE resolved = FALSE;

-- =============================================================================
-- CONFIG TABLES
-- =============================================================================

-- Trading pair configuration
CREATE TABLE IF NOT EXISTS config.trading_pairs (
    symbol VARCHAR(20) PRIMARY KEY,
    base_asset VARCHAR(20) NOT NULL,
    quote_asset VARCHAR(20) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    is_futures BOOLEAN DEFAULT FALSE,
    contract_type VARCHAR(20),
    min_quantity DECIMAL(30,8),
    max_quantity DECIMAL(30,8),
    quantity_step DECIMAL(30,8),
    min_price DECIMAL(20,8),
    max_price DECIMAL(20,8),
    price_tick_size DECIMAL(20,8),
    min_notional DECIMAL(20,8),
    maker_fee DECIMAL(10,6),
    taker_fee DECIMAL(10,6),
    exchange VARCHAR(50),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trading_pairs_active
    ON config.trading_pairs(is_active) WHERE is_active = TRUE;

-- Strategy parameters
CREATE TABLE IF NOT EXISTS config.strategy_parameters (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    strategy_id UUID REFERENCES trading.strategies(id),
    symbol VARCHAR(20),
    timeframe VARCHAR(10),
    parameters JSONB NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(strategy_id, symbol, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_strategy_params_active
    ON config.strategy_parameters(is_active) WHERE is_active = TRUE;

-- Global settings
CREATE TABLE IF NOT EXISTS config.settings (
    key VARCHAR(100) PRIMARY KEY,
    value JSONB NOT NULL,
    description TEXT,
    category VARCHAR(50),
    is_secret BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- VIEWS
-- =============================================================================

-- Currently open positions
CREATE OR REPLACE VIEW trading.v_open_positions AS
SELECT
    p.*,
    s.name as strategy_name,
    s.type as strategy_type,
    COALESCE(o.pending_orders, 0) as pending_orders
FROM trading.positions p
LEFT JOIN trading.strategies s ON p.strategy_id = s.id
LEFT JOIN (
    SELECT position_id, COUNT(*) as pending_orders
    FROM trading.orders
    WHERE status IN ('NEW', 'PARTIALLY_FILLED')
    GROUP BY position_id
) o ON p.id = o.position_id
WHERE p.status IN ('OPEN', 'CLOSING');

-- Active orders
CREATE OR REPLACE VIEW trading.v_active_orders AS
SELECT
    o.*,
    p.entry_price as position_entry_price,
    p.side as position_side,
    s.name as strategy_name
FROM trading.orders o
LEFT JOIN trading.positions p ON o.position_id = p.id
LEFT JOIN trading.signals sig ON o.signal_id = sig.id
LEFT JOIN trading.strategies s ON sig.strategy_id = s.id
WHERE o.status IN ('NEW', 'PARTIALLY_FILLED');

-- Latest signals
CREATE OR REPLACE VIEW trading.v_recent_signals AS
SELECT
    sig.*,
    s.name as strategy_name,
    s.type as strategy_type
FROM trading.signals sig
LEFT JOIN trading.strategies s ON sig.strategy_id = s.id
WHERE sig.timestamp > NOW() - INTERVAL '24 hours'
ORDER BY sig.timestamp DESC;

-- =============================================================================
-- FUNCTIONS AND TRIGGERS
-- =============================================================================

-- updated_at update function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers for updated_at
DROP TRIGGER IF EXISTS update_strategies_updated_at ON trading.strategies;
CREATE TRIGGER update_strategies_updated_at
    BEFORE UPDATE ON trading.strategies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_orders_updated_at ON trading.orders;
CREATE TRIGGER update_orders_updated_at
    BEFORE UPDATE ON trading.orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_positions_updated_at ON trading.positions;
CREATE TRIGGER update_positions_updated_at
    BEFORE UPDATE ON trading.positions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_trading_pairs_updated_at ON config.trading_pairs;
CREATE TRIGGER update_trading_pairs_updated_at
    BEFORE UPDATE ON config.trading_pairs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_strategy_parameters_updated_at ON config.strategy_parameters;
CREATE TRIGGER update_strategy_parameters_updated_at
    BEFORE UPDATE ON config.strategy_parameters
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_settings_updated_at ON config.settings;
CREATE TRIGGER update_settings_updated_at
    BEFORE UPDATE ON config.settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Position PnL function
CREATE OR REPLACE FUNCTION calculate_position_pnl(
    p_position_id UUID,
    p_current_price DECIMAL(20,8)
) RETURNS TABLE (
    unrealized_pnl DECIMAL(20,8),
    pnl_percent DECIMAL(10,4)
) AS $$
DECLARE
    v_position RECORD;
    v_unrealized_pnl DECIMAL(20,8);
    v_pnl_percent DECIMAL(10,4);
BEGIN
    SELECT * INTO v_position FROM trading.positions WHERE id = p_position_id;

    IF v_position IS NULL THEN
        RETURN;
    END IF;

    IF v_position.side = 'LONG' OR v_position.side = 'BUY' THEN
        v_unrealized_pnl := (p_current_price - v_position.entry_price) * v_position.quantity;
    ELSE
        v_unrealized_pnl := (v_position.entry_price - p_current_price) * v_position.quantity;
    END IF;

    IF v_position.entry_value > 0 THEN
        v_pnl_percent := (v_unrealized_pnl / v_position.entry_value) * 100;
    ELSE
        v_pnl_percent := 0;
    END IF;

    RETURN QUERY SELECT v_unrealized_pnl, v_pnl_percent;
END;
$$ LANGUAGE plpgsql;

-- Audit function
CREATE OR REPLACE FUNCTION audit_trigger()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO audit.action_logs (
        timestamp,
        action_type,
        entity_type,
        entity_id,
        request_data,
        status
    ) VALUES (
        NOW(),
        TG_OP,
        TG_TABLE_NAME,
        CASE
            WHEN TG_OP = 'DELETE' THEN OLD.id::text
            ELSE NEW.id::text
        END,
        CASE
            WHEN TG_OP = 'DELETE' THEN row_to_json(OLD)
            ELSE row_to_json(NEW)
        END,
        'SUCCESS'
    );

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Audit triggers for important tables
DROP TRIGGER IF EXISTS audit_orders ON trading.orders;
CREATE TRIGGER audit_orders
    AFTER INSERT OR UPDATE OR DELETE ON trading.orders
    FOR EACH ROW EXECUTE FUNCTION audit_trigger();

DROP TRIGGER IF EXISTS audit_positions ON trading.positions;
CREATE TRIGGER audit_positions
    AFTER INSERT OR UPDATE OR DELETE ON trading.positions
    FOR EACH ROW EXECUTE FUNCTION audit_trigger();

-- Strategy statistics function
CREATE OR REPLACE FUNCTION analytics.get_strategy_stats(
    p_strategy_id UUID,
    p_days INTEGER DEFAULT 30
) RETURNS TABLE (
    total_trades BIGINT,
    winning_trades BIGINT,
    losing_trades BIGINT,
    win_rate DECIMAL(5,4),
    total_pnl DECIMAL(20,8),
    avg_pnl DECIMAL(20,8),
    max_pnl DECIMAL(20,8),
    min_pnl DECIMAL(20,8),
    avg_duration INTERVAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT as total_trades,
        COUNT(*) FILTER (WHERE p.realized_pnl > 0)::BIGINT as winning_trades,
        COUNT(*) FILTER (WHERE p.realized_pnl < 0)::BIGINT as losing_trades,
        CASE
            WHEN COUNT(*) > 0
            THEN (COUNT(*) FILTER (WHERE p.realized_pnl > 0)::DECIMAL / COUNT(*)::DECIMAL)
            ELSE 0
        END as win_rate,
        COALESCE(SUM(p.realized_pnl), 0) as total_pnl,
        COALESCE(AVG(p.realized_pnl), 0) as avg_pnl,
        COALESCE(MAX(p.realized_pnl), 0) as max_pnl,
        COALESCE(MIN(p.realized_pnl), 0) as min_pnl,
        AVG(p.closed_at - p.opened_at) as avg_duration
    FROM trading.positions p
    WHERE p.strategy_id = p_strategy_id
      AND p.status = 'CLOSED'
      AND p.closed_at >= NOW() - (p_days || ' days')::INTERVAL;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- INITIAL DATA
-- =============================================================================

-- Base settings
INSERT INTO config.settings (key, value, description, category) VALUES
    ('max_open_positions', '10', 'Maximum number of open positions', 'risk'),
    ('max_position_size_percent', '0.1', 'Maximum position size as percent of balance', 'risk'),
    ('default_stop_loss_percent', '0.02', 'Default stop loss percentage', 'risk'),
    ('default_take_profit_percent', '0.04', 'Default take profit percentage', 'risk'),
    ('min_confidence_threshold', '0.6', 'Minimum signal confidence to execute', 'trading'),
    ('enable_trailing_stop', 'true', 'Enable trailing stop for positions', 'trading'),
    ('trading_enabled', 'true', 'Global trading enabled flag', 'system'),
    ('maintenance_mode', 'false', 'Maintenance mode flag', 'system')
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- GRANTS (optional, uncomment if needed)
-- =============================================================================
-- GRANT USAGE ON SCHEMA market_data TO trading_app;
-- GRANT USAGE ON SCHEMA trading TO trading_app;
-- GRANT USAGE ON SCHEMA analytics TO trading_app;
-- GRANT USAGE ON SCHEMA audit TO trading_app;
-- GRANT USAGE ON SCHEMA config TO trading_app;
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA market_data TO trading_app;
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA trading TO trading_app;
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA analytics TO trading_app;
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA audit TO trading_app;
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA config TO trading_app;

-- Done!
SELECT 'Database initialization completed successfully!' as status;
