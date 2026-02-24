import pandas as pd
from typing import List, Dict, Any

from core.logger import setup_logger
from core.strategies.base import BaseStrategy

logger = setup_logger("backtest_engine")

class BacktestEngine:
    """
    Простой скелет движка для тестирования исторических данных (векторизованно/побарно).
    Пока это MVP версия для прогона DataFrame через стратегию свеча за свечой.
    """
    def __init__(self, data: pd.DataFrame, strategy: BaseStrategy, initial_balance: float = 1000.0):
        self.data = data
        self.strategy = strategy
        self.balance = initial_balance
        self.initial_balance = initial_balance
        
        self.positions: List[Dict[str, Any]] = []
        self.trades: List[Dict[str, Any]] = []
        
        # Подготовим данные (рассчитаем индикаторы сразу на всем DF)
        if hasattr(self.strategy, 'prepare_data'):
            self.data = self.strategy.prepare_data(self.data)

    def run(self):
        """Запускает цикл прохода по всем свечам."""
        logger.info(f"Запуск бэктеста для стратегии {self.strategy.name} на {len(self.data)} свечах.")
        
        for idx in range(len(self.data)):
            self._process_candle(idx)
            
        self._print_metrics()

    def _process_candle(self, idx: int):
        """Обработка одной свечи (индекса в DataFrame)."""
        current_candle = self.data.iloc[idx]
        
        # 1. Сначала проверяем открытые позиции (SL / TP)
        self._check_positions(current_candle)
        
        # 2. Получаем сигнал от стратегии (передаем весь датафрейм и текущий индекс)
        # В реальной торговле мы бы передавали только до idx
        signal_info = self.strategy.on_ohlcv(self.data, idx)
        
        # 3. Обрабатываем сигнал
        signal = signal_info.get("signal", "NONE")
        if signal in ["BUY", "SELL"]:
            self._execute_signal(signal, signal_info, current_candle)

    def _check_positions(self, candle: pd.Series):
        """Простейшая проверка стопов и тейков."""
        for i in range(len(self.positions) - 1, -1, -1):
            pos = self.positions[i]
            
            # Проверка Long
            if pos["side"] == "BUY":
                # Сначала проверяем стоп (пессимистичный сценарий)
                if candle["low"] <= pos["stop_loss"]:
                    self._close_position(pos, pos["stop_loss"], candle["timestamp"], "SL")
                    self.positions.pop(i)
                elif "take_profit" in pos and candle["high"] >= pos["take_profit"]:
                    self._close_position(pos, pos["take_profit"], candle["timestamp"], "TP")
                    self.positions.pop(i)
                
            # Проверка Short
            elif pos["side"] == "SELL":
                # Сначала стоп (для шорта это рост цены)
                if candle["high"] >= pos["stop_loss"]:
                    self._close_position(pos, pos["stop_loss"], candle["timestamp"], "SL")
                    self.positions.pop(i)
                elif "take_profit" in pos and candle["low"] <= pos["take_profit"]:
                    self._close_position(pos, pos["take_profit"], candle["timestamp"], "TP")
                    self.positions.pop(i)

    def _execute_signal(self, signal: str, info: dict, candle: pd.Series):
        """Открываем позицию по закрытию свечи."""
        entry_price = candle["close"]
        
        # Фиксированный размер для тестов 10% от депо
        size_usdt = self.balance * 0.1 
        qty = size_usdt / entry_price
        
        new_pos = {
            "side": signal,
            "entry_price": entry_price,
            "qty": qty,
            "stop_loss": info.get("stop_loss", 0.0),
            "take_profit": info.get("take_profit"), # Может быть None
            "time": candle["timestamp"]
        }
        self.positions.append(new_pos)
        logger.debug(f"[{candle['timestamp']}] Открыта {signal} по {entry_price}")

    def _close_position(self, pos: dict, exit_price: float, timestamp: int, reason: str):
        """Закрывает позицию и считает PnL."""
        if pos["side"] == "BUY":
            pnl = (exit_price - pos["entry_price"]) * pos["qty"]
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["qty"]
            
        # Упрощенная комиссия 0.05%
        fee = (exit_price * pos["qty"]) * 0.0005 + (pos["entry_price"] * pos["qty"]) * 0.0005
        net_pnl = pnl - fee
        
        self.balance += net_pnl
        self.trades.append({
            "entry_time": pos["time"],
            "exit_time": timestamp,
            "side": pos["side"],
            "pnl": net_pnl,
            "reason": reason
        })
        logger.debug(f"[{timestamp}] Закрыта {pos['side']} по {exit_price} ({reason}). PnL: {net_pnl:.2f}")

    def _print_metrics(self):
        """Вывод простой статистики."""
        total_trades = len(self.trades)
        wins = len([t for t in self.trades if t['pnl'] > 0])
        winrate = (wins / total_trades * 100) if total_trades > 0 else 0
        roi = ((self.balance - self.initial_balance) / self.initial_balance) * 100
        
        logger.info("\n--- MVP Backtest Metrics ---")
        logger.info(f"Total Trades: {total_trades}")
        logger.info(f"Winrate: {winrate:.1f}%")
        logger.info(f"Final Balance: {self.balance:.2f} USDT")
        logger.info(f"ROI: {roi:.2f}%")
        logger.info("----------------------------\n")
