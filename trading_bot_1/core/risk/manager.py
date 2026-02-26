from typing import Dict, Any

from core.logger import setup_logger

logger = setup_logger("risk_manager")

class RiskManager:
    """
    Модуль управления рисками.
    Отвечает за:
    1. Расчет размера позиции (в % от депозита или фиксировано).
    2. Ограничение дневных убытков (Daily Drawdown Limit).
    3. Валидацию сигналов (хватает ли средств на балансе).
    """

    def __init__(self, risk_per_trade_percent: float = 2.0, max_daily_loss_percent: float = 5.0):
        # Риск на сделку (в % от текущего баланса)
        self.risk_per_trade_percent = risk_per_trade_percent
        # Максимальная допустимая дневная просадка (в % от стартового баланса дня)
        self.max_daily_loss_percent = max_daily_loss_percent
        
        # В MVP мы упрощенно храним тут стартовый баланс сессии бота.
        # В идеале стартовый баланс дня нужно сохранять в БД и сбрасывать в 00:00 UTC.
        self.session_start_balance = 0.0
        self.is_session_started = False
        
        # Текущий накопительный PnL бота
        self.current_session_pnl = 0.0

    def start_session(self, current_balance: float):
        """Инициализация сессии новым балансом."""
        if not self.is_session_started:
            self.session_start_balance = current_balance
            self.is_session_started = True
            logger.info(f"Риск-менеджмент: Стартовый баланс зафиксирован ({current_balance} USDT). Макс. убыток: {self.max_daily_loss_percent}%")

    def report_trade_result(self, pnl: float):
        """Обновление результатов после закрытия сделки (для учета дневного лимита)."""
        self.current_session_pnl += pnl
        logger.info(f"Риск-менеджмент: Зафиксирован PnL сделки: {pnl:.2f}. PnL сессии: {self.current_session_pnl:.2f} USDT")

    def check_daily_limit(self) -> bool:
        """
        Проверка, не превышен ли дневной лимит потерь.
        Вернет False, если торговать больше нельзя.
        """
        if not self.is_session_started:
            return True # Еще не начали
            
        # PnL у нас может быть отрицательным. Если убыток больше допустимого, тормозим.
        max_loss_usdt = self.session_start_balance * (self.max_daily_loss_percent / 100)
        
        if self.current_session_pnl <= -max_loss_usdt:
            logger.error(f"СРАБОТАЛ ДНЕВНОЙ ЛИМИТ УБЫТКОВ! "
                         f"Текущий PnL: {self.current_session_pnl:.2f} USDT. "
                         f"Лимит: -{max_loss_usdt:.2f} USDT. "
                         f"Торговля остановлена.")
            return False
            
        logger.debug(f"Дневной лимит OK. Текущий PnL: {self.current_session_pnl:.2f} (Лимит: -{max_loss_usdt:.2f})")
        return True

    def calculate_position_size(self, current_balance: float, entry_price: float, stop_loss: float) -> Dict[str, float]:
        """
        Расчет размера позиции.
        Вариант А: Фиксированный процент от депозита (просто сайз).
        Вариант Б: Риск на сделку (учитывая SL).
        Мы выберем Вариант А (сайз в 2% от баланса) для простоты скальпинга, 
        но можно переключить на полноценный расчет по SL.
        """
        
        if not self.check_daily_limit():
            return {"quote_qty": 0.0, "base_qty": 0.0, "reason": "daily_limit_reached"}

        # Рассчитываем размер порции в USDT
        # Например, 2% от 1000$ = 20 USDT на сделку (без плеча)
        quote_qty = current_balance * (self.risk_per_trade_percent / 100.0)
        
        # Минимальный размер ордера на MEXC обычно 5 USDT (зависит от пары)
        if quote_qty < 5.0:
            logger.warning(f"Риск-менеджмент: Рассчитанный объем ({quote_qty:.2f} USDT) меньше минимального (5 USDT).")
            # Для тестов/MVP будем входить на 5 баксов, если депо мелкий.
            quote_qty = 5.0
            
        # Если баланса не хватает даже на минималку
        if quote_qty > current_balance:
            logger.error("Риск-менеджмент: Недостаточно средств на балансе!")
            return {"quote_qty": 0.0, "base_qty": 0.0, "reason": "insufficient_funds"}

        # Базовый объем актива (например, сколько это SOL)
        base_qty = quote_qty / entry_price
        
        logger.debug(f"RiskManager Одобрил: Вход {entry_price}, Объем {quote_qty:.2f} USDT ({base_qty:.4f} crypto)")
        
        return {
            "quote_qty": round(quote_qty, 2), # Округляем до 2 знаков для USDT
            "base_qty": round(base_qty, 4),   # Округляем до 4 знаков для крипты
            "reason": "ok"
        }
