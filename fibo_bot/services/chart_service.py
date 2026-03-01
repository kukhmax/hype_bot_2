"""
Fibo Bot — Chart Service.

Визуализация торговых сигналов с помощью mplfinance и matplotlib.
- Свечной график (последние N свечей)
- Наложение индикаторов: VWAP, EMA
- Уровни Fibonacci от Wave 1
- Зона входа (Entry box)
- Линии Stop Loss и Take Profit (TP1, TP2, TP3)
- Водяной знак с информацией о сигнале и ML вероятностью
"""

import os
from typing import Optional, List
from datetime import datetime

import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import mplfinance as mpf

from config import config
from engines.market_engine import CandleBuffer
from engines.strategy_engine import TradeSignal, Direction
from engines.feature_engine import compute_vwap, compute_ema
from utils.logger import get_logger

logger = get_logger("chart_service")


# Используем бэкенд Agg для отрисовки без GUI (работает в Docker без X11)
matplotlib.use("Agg")

# Директория для сохранения графиков
CHARTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)


class ChartService:
    """Сервис генерации графиков для Telegram."""

    def __init__(self, theme: str = "nightclouds", num_candles: int = 150):
        self.theme = theme
        self.num_candles = num_candles
        logger.info(f"[ChartService] Инициализирован (theme={theme}, candles={num_candles})")

    def _buffer_to_dataframe(self, buffer: CandleBuffer) -> pd.DataFrame:
        """Конвертация CandleBuffer в pandas DataFrame для mplfinance."""
        candles = buffer.to_list()[-self.num_candles:]

        df = pd.DataFrame([
            {
                "Date": pd.to_datetime(c.timestamp, unit="ms"),
                "Open": c.open,
                "High": c.high,
                "Low": c.low,
                "Close": c.close,
                "Volume": c.volume,
            }
            for c in candles
        ])
        df.set_index("Date", inplace=True)
        return df

    def generate_chart(self, signal: TradeSignal, buffer: CandleBuffer) -> Optional[str]:
        """
        Генерация графика для сигнала.
        Возвращает абсолютный путь к сохраненному PNG файлу.
        """
        logger.info(f"[Chart] Генерация графика для {signal.symbol} {signal.timeframe}...")
        try:
            df = self._buffer_to_dataframe(buffer)

            if len(df) < 50:
                logger.warning(f"[Chart] Недостаточно свечей для графика: {len(df)}")
                return None

            # 1. Вычисляем индикаторы для графика
            closes = df["Close"].values
            highs = df["High"].values
            lows = df["Low"].values
            volumes = df["Volume"].values

            vwap = compute_vwap(highs, lows, closes, volumes)
            ema20 = compute_ema(closes, 20)
            ema50 = compute_ema(closes, 50)

            # Добавляем индикаторы через mpf.make_addplot
            apds = [
                mpf.make_addplot(vwap, color="#FF9800", width=1.5, label="VWAP"),
                mpf.make_addplot(ema20, color="#2196F3", width=1.0, alpha=0.7, label="EMA20"),
                mpf.make_addplot(ema50, color="#9C27B0", width=1.0, alpha=0.7, label="EMA50"),
            ]

            # 2. Формируем линии уровней (SL, TP, Entry)
            t_lines = []
            colors = []
            styles = []
            widths = []

            # Stop Loss (Красная пунктирная)
            t_lines.append(signal.stop_loss)
            colors.append("#F44336")
            styles.append("--")
            widths.append(1.5)

            # TP1, TP2, TP3 (Зеленые сплошные)
            for tp in [signal.tp1, signal.tp2, signal.tp3]:
                if tp > 0:
                    t_lines.append(tp)
                    colors.append("#4CAF50")
                    styles.append("-")
                    widths.append(1.2)

            # 3. Entry Box (Прямоугольник зоны входа)
            # В mplfinance fill_between используется для заливки зон
            entry_min = min(signal.entry_low, signal.entry_high)
            entry_max = max(signal.entry_low, signal.entry_high)

            # Создаем массив для заливки зоны входа
            # Заливаем только последние 20% графика (правая часть)
            fill_start_idx = int(len(df) * 0.8)
            y1 = np.full(len(df), np.nan)
            y2 = np.full(len(df), np.nan)
            y1[fill_start_idx:] = entry_min
            y2[fill_start_idx:] = entry_max

            apds.append(
                mpf.make_addplot(y1, type="fill_between", y2=y2, color="#2196F3", alpha=0.2)
            )

            # 4. Стиль графика
            mc = mpf.make_marketcolors(
                up="#4CAF50", down="#F44336",
                edge="inherit", wick="inherit", volume="in", OHL="inherit"
            )
            s = mpf.make_mpf_style(
                marketcolors=mc,
                base_mpf_style=self.theme,
                y_on_right=True,
                gridcolor="#333333",
                gridstyle="--",
            )

            # 5. Текст для графика (Watermark / Легенда)
            title = f"{signal.symbol} | {signal.timeframe} | {signal.direction}"
            # Добавляем информацию о ML вероятности в заголовок, если она рассчитана (будет в Шаге 8)
            prob_text = f" | ML Prob: {signal.probability:.0f}%" if signal.probability > 0 else ""
            title += prob_text

            # 6. Имя файла
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{signal.symbol}_{signal.timeframe}_{signal.direction}_{timestamp_str}.png"
            filepath = os.path.join(CHARTS_DIR, filename)

            # Очищаем старые плоты из памяти matplotlib
            plt.close("all")

            # 7. Рендер и сохранение
            fig, axes = mpf.plot(
                df,
                type="candle",
                title=title,
                style=s,
                volume=True,
                addplot=apds,
                hlines=dict(hlines=t_lines, colors=colors, linestyle=styles, linewidths=widths),
                figsize=(12, 7),
                dpi=120,
                returnfig=True,
                tight_layout=True,
            )

            # Добавляем текстовые метки к линиям (на правом краю)
            ax = axes[0] # Ось с ценами
            x_pos = len(df) - 1

            # Текст стопа
            ax.text(x_pos, signal.stop_loss, " SL", color="#F44336",
                    verticalalignment="bottom", weight="bold")

            # Текст TP
            if signal.tp1 > 0:
                ax.text(x_pos, signal.tp1, " TP1", color="#4CAF50",
                        verticalalignment="bottom", weight="bold")
            if signal.tp2 > 0:
                ax.text(x_pos, signal.tp2, " TP2", color="#4CAF50",
                        verticalalignment="bottom", weight="bold")
            if signal.tp3 > 0:
                ax.text(x_pos, signal.tp3, " TP3", color="#4CAF50",
                        verticalalignment="bottom", weight="bold")

            # Текст Entry
            ax.text(x_pos, entry_max, " Entry", color="#2196F3",
                    verticalalignment="bottom", weight="bold")

            # Информационный блок в левом верхнем углу
            info_text = (
                f"Regime: {signal.regime}\n"
                f"Trend EMA: {signal.htf_bias}\n"
                f"R:R = {signal.risk_reward}\n"
                f"P(ML) = {signal.probability:.0f}%\n"
                f"Confidence: {signal.confidence}"
            )
            ax.text(0.02, 0.95, info_text, transform=ax.transAxes,
                    fontsize=10, color="white",
                    verticalalignment='top',
                    bbox=dict(boxstyle="round", facecolor="black", alpha=0.5))

            # Сохраняем фигуру явно
            fig.savefig(filepath, bbox_inches="tight", dpi=120)
            plt.close(fig) # Закрываем для освобождения памяти

            logger.info(f"[Chart] ✅ График сохранён: {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"[Chart] Ошибка генерации графика: {e}", exc_info=True)
            return None

# Глобальный экземпляр
chart_service = ChartService()
