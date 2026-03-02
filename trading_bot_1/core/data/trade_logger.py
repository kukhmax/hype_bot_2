import os
import csv
from datetime import datetime
from typing import Optional

from core.logger import setup_logger

logger = setup_logger("trade_logger")

CSV_FILE = "trades_history.csv"

class TradeLogger:
    """Обеспечивает запись всех закрытых сделок в CSV-файл для истории и анализа."""

    @staticmethod
    def _init_csv():
        """Создает файл и заголовки, если его еще нет."""
        if not os.path.exists(CSV_FILE):
            try:
                with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as file:
                    writer = csv.writer(file)
                    writer.writerow([
                        "Timestamp", "Date", "Symbol", "Mode", "Side", 
                        "EntryPrice", "ClosePrice", "Qty", "PnL_USDT", 
                        "Strategy", "Regime", "Reason"
                    ])
                logger.debug(f"Создан новый файл для истории сделок: {CSV_FILE}")
            except Exception as e:
                logger.error(f"Ошибка создания {CSV_FILE}: {e}")

    @staticmethod
    def log_trade(
        symbol: str,
        mode: str, # "PAPER" или "LIVE"
        side: str, # "BUY" или "SELL"
        entry_price: float,
        close_price: float,
        qty: float,
        pnl: float,
        strategy_name: str,
        regime: str,
        reason: str # "TP", "SL", "TrailingSL", "Manual", etc.
    ):
        """Записывает информацию о закрытой сделке в CSV."""
        TradeLogger._init_csv()
        
        timestamp = int(datetime.now().timestamp() * 1000)
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow([
                    timestamp,
                    date_str,
                    symbol,
                    mode,
                    side,
                    f"{entry_price:.4f}",
                    f"{close_price:.4f}",
                    f"{qty:.4f}",
                    f"{pnl:.2f}",
                    strategy_name,
                    regime,
                    reason
                ])
            logger.debug(f"Сделка по {symbol} (PnL: {pnl:.2f}) записана в {CSV_FILE}")
        except Exception as e:
            logger.error(f"Ошибка добавления сделки в {CSV_FILE}: {e}")
