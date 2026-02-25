import itertools
import pandas as pd
from typing import Dict, Any, List, Type

from core.logger import setup_logger
from core.strategies.base import BaseStrategy
from core.backtest.engine import BacktestEngine

logger = setup_logger("optimizer")

class StrategyOptimizer:
    """
    Простой Grid Search оптимизатор.
    Прогоняет все комбинации переданных параметров для указанной стратегии.
    """
    
    def __init__(self, data: pd.DataFrame, strategy_class: Type[BaseStrategy], initial_balance: float = 1000.0):
        # Храним только базовые OHLCV колонки, чтобы избежать загрязнения индикаторами
        base_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        available = [c for c in base_cols if c in data.columns]
        self.original_data = data[available].copy(deep=True)
        self.strategy_class = strategy_class
        self.initial_balance = initial_balance
        self.results: List[Dict[str, Any]] = []

    def optimize(self, param_grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
        """
        Запускает бэктест для всех комбинаций параметров из param_grid.
        param_grid: {"rsi_threshold": [30, 40], "sl_atr_mult": [1.0, 1.5]}
        Возвращает отсортированный список результатов.
        """
        keys = param_grid.keys()
        values = param_grid.values()
        
        # Генерируем все комбинации (распаковка через itertools.product)
        combinations = list(itertools.product(*values))
        total_runs = len(combinations)
        
        logger.info(f"Начало оптимизации {self.strategy_class.__name__}. Всего комбинаций: {total_runs}")
        
        for idx, combination in enumerate(combinations):
            # Создаем словарь параметров для конкретного прогона
            kwargs = dict(zip(keys, combination))
            
            # Инициализируем стратегию
            strategy_instance = self.strategy_class(**kwargs)
            
            # DEBUG: проверяем оригинальные данные перед копированием
            logger.debug(f"[DEBUG {idx+1}/{total_runs}] original_data id={id(self.original_data)}, cols={list(self.original_data.columns)}, shape={self.original_data.shape}")
            
            # Свежая глубокая копия чистых OHLCV данных для каждого прогона
            data_copy = self.original_data.copy(deep=True)
            
            logger.debug(f"[DEBUG {idx+1}/{total_runs}] data_copy id={id(data_copy)}, cols={list(data_copy.columns)}, shape={data_copy.shape}")
            
            engine = BacktestEngine(data=data_copy, strategy=strategy_instance, initial_balance=self.initial_balance)
            engine.run()
            
            # DEBUG: проверяем оригинальные данные ПОСЛЕ прогона
            logger.debug(f"[DEBUG {idx+1}/{total_runs}] AFTER RUN original_data cols={list(self.original_data.columns)}, shape={self.original_data.shape}")
            
            # Собираем все метрики из движка
            result = engine.metrics
            result["params"] = kwargs
            
            self.results.append(result)
            logger.info(f"[{idx+1}/{total_runs}] {kwargs} -> ROI: {result['roi']:.2f}%, Trades: {result['total_trades']}, Winrate: {result['winrate']:.1f}%")

        # Сортируем по ROI по убыванию
        self.results.sort(key=lambda x: x["roi"], reverse=True)
        return self.results

    def print_top_results(self, top_n: int = 5):
        logger.info(f"\n--- TOP {top_n} РЕЗУЛЬТАТОВ ОПТИМИЗАЦИИ ---")
        for i, res in enumerate(self.results[:top_n]):
            logger.info(f"#{i+1} | ROI: {res['roi']:>6.2f}% | Winrate: {res['winrate']:>5.1f}% | Trades: {res['total_trades']:>3} | Params: {res['params']}")
        logger.info("----------------------------------------\n")
