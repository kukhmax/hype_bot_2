"""
Trade History Analyzer — анализ реальных торговых результатов из trades_history.csv.

Модуль парсит CSV с историей сделок и вычисляет:
1. Win rate по стратегии + режиму + символу
2. Серию убытков (losing streak) для cooldown-логики
3. Дополнительные ML-фичи из торговых паттернов
4. Блокировку «токсичных» комбинаций (стратегия+режим с WR < 20%)
"""

import os
import csv
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from core.logger import setup_logger

logger = setup_logger("trade_analyzer")

# Путь к CSV файлу (относительно корня проекта)
DEFAULT_CSV_PATH = "trades_history.csv"


class TradeHistoryAnalyzer:
    """
    Анализирует историю сделок из CSV для принятия решений по фильтрации.

    Кэширует статистику в памяти и обновляется при каждой новой сделке.
    """

    # Пороги для блокировки
    MIN_TRADES_FOR_ANALYSIS = 5         # Минимум сделок для анализа
    TOXIC_WINRATE_THRESHOLD = 0.20      # WR < 20% → блокировка
    MAX_LOSING_STREAK = 3               # Серия > 3 убытков → cooldown
    COOLDOWN_WINDOW = 20                # Анализируем последние N сделок

    def __init__(self, csv_path: str = DEFAULT_CSV_PATH):
        self.csv_path = csv_path
        self.trades: List[Dict] = []

        # Кэш статистики: ключ → список PnL
        self._strategy_regime_stats: Dict[str, List[float]] = defaultdict(list)
        self._strategy_symbol_stats: Dict[str, List[float]] = defaultdict(list)
        self._symbol_stats: Dict[str, List[float]] = defaultdict(list)
        self._hour_stats: Dict[int, List[float]] = defaultdict(list)

        self._load_trades()

    def _load_trades(self):
        """Загрузить все сделки из CSV."""
        if not os.path.exists(self.csv_path):
            logger.warning(f"Файл истории сделок не найден: {self.csv_path}")
            return

        try:
            with open(self.csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        trade = {
                            "timestamp": int(row.get("Timestamp", 0)),
                            "date": row.get("Date", ""),
                            "symbol": row.get("Symbol", ""),
                            "mode": row.get("Mode", ""),
                            "side": row.get("Side", ""),
                            "entry_price": float(row.get("EntryPrice", 0)),
                            "close_price": float(row.get("ClosePrice", 0)),
                            "qty": float(row.get("Qty", 0)),
                            "pnl": float(row.get("PnL_USDT", 0)),
                            "strategy": row.get("Strategy", ""),
                            "regime": row.get("Regime", ""),
                            "reason": row.get("Reason", ""),
                        }
                        self.trades.append(trade)
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Пропуск строки в CSV: {e}")
                        continue

            logger.info(f"Загружено {len(self.trades)} сделок из {self.csv_path}")
            self._rebuild_stats()

        except Exception as e:
            logger.error(f"Ошибка загрузки trades_history.csv: {e}")

    def _rebuild_stats(self):
        """Пересчитать всю статистику из загруженных сделок."""
        self._strategy_regime_stats.clear()
        self._strategy_symbol_stats.clear()
        self._symbol_stats.clear()
        self._hour_stats.clear()

        for trade in self.trades:
            self._index_trade(trade)

    def _index_trade(self, trade: Dict):
        """Добавить одну сделку в индексы статистики."""
        pnl = trade["pnl"]
        strategy = trade["strategy"]
        regime = trade["regime"]
        symbol = trade["symbol"]

        # Извлекаем час из даты
        hour = -1
        try:
            dt = datetime.strptime(trade["date"], "%Y-%m-%d %H:%M:%S")
            hour = dt.hour
        except (ValueError, TypeError):
            pass

        sr_key = f"{strategy}|{regime}"
        ss_key = f"{strategy}|{symbol}"

        self._strategy_regime_stats[sr_key].append(pnl)
        self._strategy_symbol_stats[ss_key].append(pnl)
        self._symbol_stats[symbol].append(pnl)
        if hour >= 0:
            self._hour_stats[hour].append(pnl)

    def update(self, trade: Dict):
        """
        Добавить новую закрытую сделку и обновить статистику.

        Args:
            trade: словарь с полями symbol, strategy, regime, pnl, date, side, reason
        """
        self.trades.append(trade)
        self._index_trade(trade)
        logger.debug(
            f"TradeAnalyzer обновлён: +1 сделка ({trade.get('strategy', '?')}/"
            f"{trade.get('regime', '?')}/{trade.get('symbol', '?')} "
            f"PnL={trade.get('pnl', 0):.2f}). Всего: {len(self.trades)}"
        )

    # ─────────────────────────────────────────────
    # Методы запроса статистики
    # ─────────────────────────────────────────────

    def get_strategy_regime_winrate(
        self, strategy: str, regime: str, window: int = None
    ) -> Tuple[float, int]:
        """
        Win rate для комбинации стратегия+режим.

        Returns:
            (winrate, total_trades) — winrate ∈ [0, 1], кол-во сделок
        """
        key = f"{strategy}|{regime}"
        pnls = self._strategy_regime_stats.get(key, [])
        if window:
            pnls = pnls[-window:]
        if not pnls:
            return 0.5, 0  # Нет данных → нейтральный WR
        wins = sum(1 for p in pnls if p > 0)
        return wins / len(pnls), len(pnls)

    def get_symbol_winrate(self, symbol: str, window: int = None) -> Tuple[float, int]:
        """Win rate по символу."""
        pnls = self._symbol_stats.get(symbol, [])
        if window:
            pnls = pnls[-window:]
        if not pnls:
            return 0.5, 0
        wins = sum(1 for p in pnls if p > 0)
        return wins / len(pnls), len(pnls)

    def get_losing_streak(self, strategy: str, symbol: str) -> int:
        """
        Текущая серия последовательных убытков для стратегии+символа.

        Returns:
            int — количество подряд убыточных сделок (0 если последняя была прибыльной)
        """
        key = f"{strategy}|{symbol}"
        pnls = self._strategy_symbol_stats.get(key, [])
        if not pnls:
            return 0

        streak = 0
        for pnl in reversed(pnls):
            if pnl <= 0:
                streak += 1
            else:
                break
        return streak

    def get_hour_winrate(self, hour: int) -> Tuple[float, int]:
        """Win rate для конкретного часа суток (0-23)."""
        pnls = self._hour_stats.get(hour, [])
        if not pnls:
            return 0.5, 0
        wins = sum(1 for p in pnls if p > 0)
        return wins / len(pnls), len(pnls)

    def get_avg_pnl(self, strategy: str, regime: str) -> float:
        """Средний PnL для стратегии+режима."""
        key = f"{strategy}|{regime}"
        pnls = self._strategy_regime_stats.get(key, [])
        if not pnls:
            return 0.0
        return sum(pnls) / len(pnls)

    # ─────────────────────────────────────────────
    # Блокировка — проверка «токсичных» комбинаций
    # ─────────────────────────────────────────────

    def should_block_signal(
        self, strategy: str, regime: str, symbol: str
    ) -> Tuple[bool, str]:
        """
        Проверить, нужно ли заблокировать сигнал на основе истории сделок.

        Returns:
            (should_block, reason)
        """
        # 1. Проверка WR стратегии+режима
        sr_wr, sr_count = self.get_strategy_regime_winrate(
            strategy, regime, window=self.COOLDOWN_WINDOW
        )
        if sr_count >= self.MIN_TRADES_FOR_ANALYSIS and sr_wr < self.TOXIC_WINRATE_THRESHOLD:
            reason = (
                f"WR {strategy}+{regime} = {sr_wr:.0%} "
                f"(< {self.TOXIC_WINRATE_THRESHOLD:.0%}) за {sr_count} сделок"
            )
            logger.warning(f"[TRADE ANALYZER] BLOCK: {reason}")
            return True, reason

        # 2. Проверка losing streak для стратегии+символа
        streak = self.get_losing_streak(strategy, symbol)
        if streak >= self.MAX_LOSING_STREAK:
            reason = (
                f"Серия {streak} убытков подряд для {strategy}+{symbol} "
                f"(макс. допустимо: {self.MAX_LOSING_STREAK})"
            )
            logger.warning(f"[TRADE ANALYZER] BLOCK (cooldown): {reason}")
            return True, reason

        return False, ""

    # ─────────────────────────────────────────────
    # Общая статистика
    # ─────────────────────────────────────────────

    def get_summary(self) -> Dict:
        """Общая статистика для отображения в Telegram."""
        if not self.trades:
            return {"total": 0, "message": "Нет сделок в истории"}

        total = len(self.trades)
        wins = sum(1 for t in self.trades if t["pnl"] > 0)
        losses = total - wins
        total_pnl = sum(t["pnl"] for t in self.trades)
        avg_win = 0.0
        avg_loss = 0.0

        win_pnls = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        loss_pnls = [t["pnl"] for t in self.trades if t["pnl"] <= 0]

        if win_pnls:
            avg_win = sum(win_pnls) / len(win_pnls)
        if loss_pnls:
            avg_loss = sum(loss_pnls) / len(loss_pnls)

        # Найти худшую комбинацию стратегия+режим
        worst_combo = None
        worst_wr = 1.0
        for key, pnls in self._strategy_regime_stats.items():
            if len(pnls) >= self.MIN_TRADES_FOR_ANALYSIS:
                wr = sum(1 for p in pnls if p > 0) / len(pnls)
                if wr < worst_wr:
                    worst_wr = wr
                    worst_combo = key

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "winrate": round(wins / total * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "worst_combo": worst_combo,
            "worst_combo_wr": round(worst_wr * 100, 1) if worst_combo else None,
        }


class TradePatternFeatures:
    """
    Рассчитывает дополнительные ML-фичи из торговой истории.

    Эти фичи отражают паттерны поведения конкретной стратегии+режима+символа
    и используются как дополнительный вход для ансамбля.
    """

    @staticmethod
    def compute(
        analyzer: TradeHistoryAnalyzer,
        strategy: str,
        regime: str,
        symbol: str,
        sl_distance: float = 0.0,
        current_price: float = 0.0,
    ) -> Dict[str, float]:
        """
        Рассчитать trade-pattern фичи.

        Returns:
            Dict с фичами:
                - strategy_regime_winrate: WR стратегии+режима [0, 1]
                - symbol_winrate: WR символа [0, 1]
                - losing_streak_norm: текущая серия убытков (нормализована) [0, 1]
                - avg_pnl_norm: нормализованный средний PnL [-1, 1]
                - hour_winrate: WR для текущего часа [0, 1]
        """
        # Стратегия + Режим WR
        sr_wr, sr_count = analyzer.get_strategy_regime_winrate(
            strategy, regime, window=TradeHistoryAnalyzer.COOLDOWN_WINDOW
        )
        # Если мало данных — нейтральное значение 0.5
        if sr_count < TradeHistoryAnalyzer.MIN_TRADES_FOR_ANALYSIS:
            sr_wr = 0.5

        # Символ WR
        sym_wr, sym_count = analyzer.get_symbol_winrate(
            symbol, window=TradeHistoryAnalyzer.COOLDOWN_WINDOW
        )
        if sym_count < TradeHistoryAnalyzer.MIN_TRADES_FOR_ANALYSIS:
            sym_wr = 0.5

        # Losing streak (нормализуем: 0 → 0.0, 5+ → 1.0)
        streak = analyzer.get_losing_streak(strategy, symbol)
        streak_norm = min(streak / 5.0, 1.0)

        # Средний PnL (нормализуем: clamp [-1, 1] через деление на 200)
        avg_pnl = analyzer.get_avg_pnl(strategy, regime)
        avg_pnl_norm = max(-1.0, min(1.0, avg_pnl / 200.0))

        # Час суток WR
        current_hour = datetime.now().hour
        hour_wr, hour_count = analyzer.get_hour_winrate(current_hour)
        if hour_count < 3:
            hour_wr = 0.5

        return {
            "strategy_regime_winrate": float(sr_wr),
            "symbol_winrate": float(sym_wr),
            "losing_streak_norm": float(streak_norm),
            "avg_pnl_norm": float(avg_pnl_norm),
            "hour_winrate": float(hour_wr),
        }
