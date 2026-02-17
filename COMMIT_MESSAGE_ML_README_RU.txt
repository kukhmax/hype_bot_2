chore(docs+rest): обновить README под ML Supertrend и уточнить загрузку истории

Что сделано
- Документация:
  - Обновлён app/README.md под текущую архитектуру:
    - ML Adaptive Supertrend + ADX ≥ 20 как основной сигнал;
    - описание пайплайна: Telegram → подписки → Redis → WS → ML → сигнал → ордера;
    - подробное объяснение работы ML-движка: ATR, k-means, Supertrend, ADX, кэш индикатора;
    - актуальные разделы по установке, запуску, логам и управлению контейнерами.
  - Уточнено, что подписка теперь собирает параметры: пара, таймфрейм, риск % (без ручных ADX/ATR).
- Загрузка истории:
  - HyperliquidAPI.get_candles теперь отправляет candleSnapshot с полями coin/interval/startTime/endTime, привязанными к n свечам и таймфрейму;
  - HistoryLoader явно передаёт n в REST-обёртку и корректно логирует процесс предзагрузки.
- Хэндлеры:
  - start: текст /start описывает ML Adaptive Supertrend и Risk %, без ADX/ATR в настройках;
  - subscriptions: FSM упрощён до пары/TF/риск, тексты подписок обновлены под новый формат.

Файлы
- app/README.md — новое описание установки, использования и внутренней логики бота.
- app/services/hyperliquid_api.py — корректное тело запроса candleSnapshot под текущее API.
- app/services/history_loader.py — использование параметра n и логи загрузки истории.
- app/handlers/start.py — текущее описание индикатора и настроек подписки.
- app/handlers/subscriptions.py — FSM без ADX/ATR, обновлённые сообщения пользователю.
