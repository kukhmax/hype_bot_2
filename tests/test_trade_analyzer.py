"""
Тесты для TradeHistoryAnalyzer и TradePatternFeatures.

Проверяет:
1. Парсинг CSV с реальными данными
2. Корректность расчёта WR по стратегии+режиму
3. Cooldown-логика (блокировка токсичных комбинаций)
4. TradePatternFeatures — все фичи в допустимом диапазоне
5. Обучение на trade history
"""

import os
import sys
import csv
import tempfile

# Добавляем корневую папку в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ml.trade_analyzer import TradeHistoryAnalyzer, TradePatternFeatures
from core.logger import setup_logger

logger = setup_logger("test_trade_analyzer")


def _create_test_csv(trades: list) -> str:
    """Создать временный CSV-файл с тестовыми сделками."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            "Timestamp", "Date", "Symbol", "Mode", "Side",
            "EntryPrice", "ClosePrice", "Qty", "PnL_USDT",
            "Strategy", "Regime", "Reason"
        ])
        for trade in trades:
            writer.writerow([
                trade.get("ts", 1000000),
                trade.get("date", "2026-03-10 10:00:00"),
                trade.get("symbol", "BTC_USDT"),
                "PAPER",
                trade.get("side", "BUY"),
                trade.get("entry", 100.0),
                trade.get("close", 101.0),
                trade.get("qty", 10.0),
                trade.get("pnl", 10.0),
                trade.get("strategy", "TestStrategy"),
                trade.get("regime", "strong_trend"),
                trade.get("reason", "TP"),
            ])
    return path


def test_parse_csv():
    """Тест: парсинг CSV файла."""
    logger.info("=== Тест 1: Парсинг CSV ===")

    trades = [
        {"pnl": 50.0, "strategy": "S1", "regime": "r1", "symbol": "BTC"},
        {"pnl": -30.0, "strategy": "S1", "regime": "r1", "symbol": "BTC"},
        {"pnl": 100.0, "strategy": "S2", "regime": "r2", "symbol": "ETH"},
    ]
    path = _create_test_csv(trades)
    try:
        analyzer = TradeHistoryAnalyzer(csv_path=path)
        assert len(analyzer.trades) == 3, f"Ожидалось 3 сделки, получено {len(analyzer.trades)}"
        summary = analyzer.get_summary()
        assert summary["total"] == 3
        assert summary["wins"] == 2
        assert summary["losses"] == 1
        logger.info(f"   Summary: {summary}")
        logger.info("✅ Тест парсинга CSV пройден!")
    finally:
        os.unlink(path)


def test_strategy_regime_winrate():
    """Тест: расчёт WR по стратегии+режиму."""
    logger.info("=== Тест 2: Strategy+Regime Win Rate ===")

    trades = [
        {"pnl": 50, "strategy": "LiqSweep", "regime": "high_vol", "symbol": "A"},
        {"pnl": -30, "strategy": "LiqSweep", "regime": "high_vol", "symbol": "A"},
        {"pnl": -40, "strategy": "LiqSweep", "regime": "high_vol", "symbol": "A"},
        {"pnl": -20, "strategy": "LiqSweep", "regime": "high_vol", "symbol": "A"},
        {"pnl": -10, "strategy": "LiqSweep", "regime": "high_vol", "symbol": "A"},
        {"pnl": 100, "strategy": "TrendPB", "regime": "strong", "symbol": "B"},
        {"pnl": 80, "strategy": "TrendPB", "regime": "strong", "symbol": "B"},
    ]
    path = _create_test_csv(trades)
    try:
        analyzer = TradeHistoryAnalyzer(csv_path=path)

        # LiqSweep + high_vol: 1 win / 5 trades = 20%
        wr, count = analyzer.get_strategy_regime_winrate("LiqSweep", "high_vol")
        assert count == 5, f"Ожидалось 5 сделок, получено {count}"
        assert abs(wr - 0.2) < 0.01, f"Ожидался WR=0.2, получено {wr}"
        logger.info(f"   LiqSweep/high_vol: WR={wr:.0%} ({count} trades)")

        # TrendPB + strong: 2 win / 2 trades = 100%
        wr2, count2 = analyzer.get_strategy_regime_winrate("TrendPB", "strong")
        assert count2 == 2
        assert abs(wr2 - 1.0) < 0.01
        logger.info(f"   TrendPB/strong: WR={wr2:.0%} ({count2} trades)")

        logger.info("✅ Тест Strategy+Regime WR пройден!")
    finally:
        os.unlink(path)


def test_losing_streak():
    """Тест: серия убытков."""
    logger.info("=== Тест 3: Losing Streak ===")

    trades = [
        {"pnl": 50, "strategy": "S1", "symbol": "A", "regime": "r"},
        {"pnl": -30, "strategy": "S1", "symbol": "A", "regime": "r"},
        {"pnl": -40, "strategy": "S1", "symbol": "A", "regime": "r"},
        {"pnl": -20, "strategy": "S1", "symbol": "A", "regime": "r"},
        {"pnl": 100, "strategy": "S2", "symbol": "B", "regime": "r"},
    ]
    path = _create_test_csv(trades)
    try:
        analyzer = TradeHistoryAnalyzer(csv_path=path)

        # S1 + A: последние 3 сделки убыточные
        streak = analyzer.get_losing_streak("S1", "A")
        assert streak == 3, f"Ожидался streak=3, получено {streak}"
        logger.info(f"   S1/A streak: {streak}")

        # S2 + B: последняя сделка прибыльная → streak = 0
        streak2 = analyzer.get_losing_streak("S2", "B")
        assert streak2 == 0, f"Ожидался streak=0, получено {streak2}"
        logger.info(f"   S2/B streak: {streak2}")

        logger.info("✅ Тест Losing Streak пройден!")
    finally:
        os.unlink(path)


def test_should_block_signal():
    """Тест: блокировка токсичных комбинаций."""
    logger.info("=== Тест 4: Block Signal ===")

    # 5 убытков подряд → должен заблокировать по WR < 20%
    trades = [
        {"pnl": -30, "strategy": "LiqSweep", "regime": "high_vol", "symbol": "A"},
        {"pnl": -40, "strategy": "LiqSweep", "regime": "high_vol", "symbol": "A"},
        {"pnl": -20, "strategy": "LiqSweep", "regime": "high_vol", "symbol": "A"},
        {"pnl": -10, "strategy": "LiqSweep", "regime": "high_vol", "symbol": "A"},
        {"pnl": -50, "strategy": "LiqSweep", "regime": "high_vol", "symbol": "A"},
    ]
    path = _create_test_csv(trades)
    try:
        analyzer = TradeHistoryAnalyzer(csv_path=path)

        blocked, reason = analyzer.should_block_signal("LiqSweep", "high_vol", "A")
        assert blocked is True, f"Должен быть заблокирован, но blocked={blocked}"
        assert "WR" in reason or "Серия" in reason
        logger.info(f"   Blocked: {blocked}, reason: {reason}")

        # Другая стратегия — не заблокирована
        blocked2, reason2 = analyzer.should_block_signal("TrendPB", "strong", "B")
        assert blocked2 is False, f"Не должен быть заблокирован"
        logger.info(f"   TrendPB/strong/B: blocked={blocked2}")

        logger.info("✅ Тест Block Signal пройден!")
    finally:
        os.unlink(path)


def test_update_live():
    """Тест: обновление в реальном времени."""
    logger.info("=== Тест 5: Live Update ===")

    path = _create_test_csv([])
    try:
        analyzer = TradeHistoryAnalyzer(csv_path=path)
        assert len(analyzer.trades) == 0

        # Добавляем сделку
        analyzer.update({
            "symbol": "BTC", "strategy": "S1", "regime": "r1",
            "pnl": -50.0, "date": "2026-03-10 10:00:00", "side": "BUY", "reason": "SL"
        })
        assert len(analyzer.trades) == 1

        # Проверяем что стат обновилась
        wr, count = analyzer.get_strategy_regime_winrate("S1", "r1")
        assert count == 1
        assert wr == 0.0
        logger.info(f"   After update: WR={wr:.0%}, count={count}")

        logger.info("✅ Тест Live Update пройден!")
    finally:
        os.unlink(path)


def test_trade_pattern_features():
    """Тест: TradePatternFeatures.compute() возвращает корректные значения."""
    logger.info("=== Тест 6: Trade Pattern Features ===")

    trades = [
        {"pnl": 50, "strategy": "S1", "regime": "r1", "symbol": "A", "date": "2026-03-10 10:00:00"},
        {"pnl": -30, "strategy": "S1", "regime": "r1", "symbol": "A", "date": "2026-03-10 11:00:00"},
        {"pnl": -40, "strategy": "S1", "regime": "r1", "symbol": "A", "date": "2026-03-10 12:00:00"},
        {"pnl": 100, "strategy": "S1", "regime": "r1", "symbol": "A", "date": "2026-03-10 13:00:00"},
        {"pnl": -20, "strategy": "S1", "regime": "r1", "symbol": "A", "date": "2026-03-10 14:00:00"},
        {"pnl": -10, "strategy": "S1", "regime": "r1", "symbol": "A", "date": "2026-03-10 15:00:00"},
    ]
    path = _create_test_csv(trades)
    try:
        analyzer = TradeHistoryAnalyzer(csv_path=path)

        features = TradePatternFeatures.compute(analyzer, "S1", "r1", "A")

        assert 0.0 <= features["strategy_regime_winrate"] <= 1.0, \
            f"WR out of range: {features['strategy_regime_winrate']}"
        assert 0.0 <= features["symbol_winrate"] <= 1.0
        assert 0.0 <= features["losing_streak_norm"] <= 1.0
        assert -1.0 <= features["avg_pnl_norm"] <= 1.0
        assert 0.0 <= features["hour_winrate"] <= 1.0

        logger.info(f"   Features: {features}")
        logger.info("✅ Тест Trade Pattern Features пройден!")
    finally:
        os.unlink(path)


def test_real_csv():
    """Тест: парсинг реального trades_history.csv (если существует)."""
    logger.info("=== Тест 7: Real CSV ===")

    real_csv = "trades_history.csv"
    if not os.path.exists(real_csv):
        logger.info("   ⚠️ Реальный CSV не найден, пропуск")
        return

    analyzer = TradeHistoryAnalyzer(csv_path=real_csv)
    summary = analyzer.get_summary()
    logger.info(f"   Сделок: {summary['total']}")
    logger.info(f"   WR: {summary['winrate']}%")
    logger.info(f"   PnL: {summary['total_pnl']}")
    logger.info(f"   Avg Win: {summary['avg_win']}, Avg Loss: {summary['avg_loss']}")
    if summary.get("worst_combo"):
        logger.info(f"   Худшая комбо: {summary['worst_combo']} WR={summary['worst_combo_wr']}%")

    # Проверяем блокировку для худшей комбинации
    if summary.get("worst_combo"):
        strategy, regime = summary["worst_combo"].split("|")
        blocked, reason = analyzer.should_block_signal(strategy, regime, "ANY_SYMBOL")
        logger.info(f"   Block test: blocked={blocked}, reason={reason}")

    logger.info("✅ Тест Real CSV пройден!")


def test_all():
    """Запуск всех тестов."""
    logger.info("\n" + "=" * 60)
    logger.info("ЗАПУСК ТЕСТОВ TRADE HISTORY ANALYZER")
    logger.info("=" * 60 + "\n")

    test_parse_csv()
    test_strategy_regime_winrate()
    test_losing_streak()
    test_should_block_signal()
    test_update_live()
    test_trade_pattern_features()
    test_real_csv()

    logger.info("\n" + "=" * 60)
    logger.info("✅ ВСЕ ТЕСТЫ TRADE ANALYZER ПРОЙДЕНЫ!")
    logger.info("=" * 60 + "\n")


if __name__ == "__main__":
    test_all()
