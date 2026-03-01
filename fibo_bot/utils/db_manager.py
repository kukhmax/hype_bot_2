"""
Fibo Bot — PostgreSQL Database Manager.

Логирование сигналов, свечей, состояния бота.
Асинхронный менеджер через asyncpg.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime

import asyncpg

from config import config
from utils.logger import get_logger

logger = get_logger("db_manager")

# ─── SQL: Инициализация таблиц ──────────────────────────────────────────────

INIT_SQL = """
-- Таблица сигналов
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    direction VARCHAR(10) NOT NULL,           -- LONG / SHORT
    regime VARCHAR(20),                       -- TREND_UP / TREND_DOWN / RANGE
    setup VARCHAR(50),                        -- Wave 2 → Wave 3
    entry_low DOUBLE PRECISION,
    entry_high DOUBLE PRECISION,
    stop_loss DOUBLE PRECISION,
    tp1 DOUBLE PRECISION,
    tp2 DOUBLE PRECISION,
    tp3 DOUBLE PRECISION,
    risk_reward DOUBLE PRECISION,
    probability DOUBLE PRECISION,             -- ML probability
    confidence VARCHAR(10),                   -- HIGH / MEDIUM / LOW
    vwap_alignment BOOLEAN DEFAULT FALSE,
    order_flow_info TEXT,
    htf_bias VARCHAR(20),
    explanation TEXT,
    chart_path VARCHAR(255),
    sent_to_telegram BOOLEAN DEFAULT FALSE
);

-- Таблица для логирования результатов сигналов
CREATE TABLE IF NOT EXISTS signal_results (
    id SERIAL PRIMARY KEY,
    signal_id INTEGER REFERENCES signals(id),
    result VARCHAR(20),                       -- WIN / LOSS / EXPIRED
    pnl_percent DOUBLE PRECISION,
    exit_price DOUBLE PRECISION,
    exit_time TIMESTAMPTZ,
    max_favorable DOUBLE PRECISION,           -- MAE
    max_adverse DOUBLE PRECISION,             -- MFE
    notes TEXT
);

-- Таблица ежедневной статистики
CREATE TABLE IF NOT EXISTS daily_stats (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    signals_count INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    total_pnl DOUBLE PRECISION DEFAULT 0,
    max_drawdown DOUBLE PRECISION DEFAULT 0
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signal_results_signal ON signal_results(signal_id);
CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date);
"""


class DatabaseManager:
    """Асинхронный менеджер PostgreSQL."""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Создание пула подключений."""
        try:
            self._pool = await asyncpg.create_pool(
                config.postgres.get_url(),
                min_size=2,
                max_size=10,
            )
            logger.info("✅ PostgreSQL подключен")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к PostgreSQL: {e}")
            raise

    async def disconnect(self):
        """Закрытие пула."""
        if self._pool:
            await self._pool.close()
            logger.info("PostgreSQL отключен")

    async def init_tables(self):
        """Создание таблиц при первом запуске."""
        async with self._pool.acquire() as conn:
            await conn.execute(INIT_SQL)
            logger.info("Таблицы PostgreSQL инициализированы")

    # ─── Сигналы ─────────────────────────────────────────────────────────

    async def save_signal(self, signal_data: Dict[str, Any]) -> int:
        """Сохранение сигнала в БД. Возвращает ID записи."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO signals (
                    symbol, timeframe, direction, regime, setup,
                    entry_low, entry_high, stop_loss, tp1, tp2, tp3,
                    risk_reward, probability, confidence,
                    vwap_alignment, order_flow_info, htf_bias,
                    explanation, chart_path, sent_to_telegram
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9, $10, $11,
                    $12, $13, $14,
                    $15, $16, $17,
                    $18, $19, $20
                ) RETURNING id
                """,
                signal_data.get("symbol"),
                signal_data.get("timeframe"),
                signal_data.get("direction"),
                signal_data.get("regime"),
                signal_data.get("setup"),
                signal_data.get("entry_low"),
                signal_data.get("entry_high"),
                signal_data.get("stop_loss"),
                signal_data.get("tp1"),
                signal_data.get("tp2"),
                signal_data.get("tp3"),
                signal_data.get("risk_reward"),
                signal_data.get("probability"),
                signal_data.get("confidence"),
                signal_data.get("vwap_alignment", False),
                signal_data.get("order_flow_info"),
                signal_data.get("htf_bias"),
                signal_data.get("explanation"),
                signal_data.get("chart_path"),
                signal_data.get("sent_to_telegram", False),
            )
            signal_id = row["id"]
            logger.info(f"Сигнал сохранён в БД (id={signal_id})")
            return signal_id

    async def update_signal_result(
        self,
        signal_id: int,
        result: str,
        pnl_percent: float = 0,
        exit_price: float = 0,
        max_favorable: float = 0,
        max_adverse: float = 0,
        notes: str = "",
    ):
        """Запись результата сигнала."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO signal_results (
                    signal_id, result, pnl_percent, exit_price,
                    exit_time, max_favorable, max_adverse, notes
                ) VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7)
                """,
                signal_id, result, pnl_percent, exit_price,
                max_favorable, max_adverse, notes,
            )

    # ─── Статистика ──────────────────────────────────────────────────────

    async def get_performance(self, days: int = 30) -> Dict[str, Any]:
        """Статистика за последние N дней."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as total_signals,
                    COUNT(CASE WHEN sr.result = 'WIN' THEN 1 END) as wins,
                    COUNT(CASE WHEN sr.result = 'LOSS' THEN 1 END) as losses,
                    COALESCE(AVG(sr.pnl_percent), 0) as avg_pnl,
                    COALESCE(SUM(sr.pnl_percent), 0) as total_pnl,
                    COALESCE(AVG(s.probability), 0) as avg_probability,
                    COALESCE(AVG(s.risk_reward), 0) as avg_rr
                FROM signals s
                LEFT JOIN signal_results sr ON sr.signal_id = s.id
                WHERE s.created_at >= NOW() - INTERVAL '%s days'
                """ % days,
            )
            total = row["total_signals"]
            wins = row["wins"] or 0
            losses = row["losses"] or 0
            return {
                "total_signals": total,
                "wins": wins,
                "losses": losses,
                "winrate": round(wins / total * 100, 1) if total > 0 else 0,
                "avg_pnl": round(float(row["avg_pnl"]), 2),
                "total_pnl": round(float(row["total_pnl"]), 2),
                "avg_probability": round(float(row["avg_probability"]), 3),
                "avg_rr": round(float(row["avg_rr"]), 2),
            }

    async def get_recent_signals(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Последние N сигналов."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.*, sr.result, sr.pnl_percent
                FROM signals s
                LEFT JOIN signal_results sr ON sr.signal_id = s.id
                ORDER BY s.created_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [dict(r) for r in rows]


# Глобальный экземпляр
db_manager = DatabaseManager()
