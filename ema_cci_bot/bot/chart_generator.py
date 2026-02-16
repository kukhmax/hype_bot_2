import mplfinance as mpf
import pandas as pd
import io
import os

def generate_signal_chart(df: pd.DataFrame, signal_data: dict):
    """
    Генерирует PNG изображение графика с нанесенными уровнями и EMA.
    Возвращает байтовый объект изображения.
    """
    # Берем последние 100 свечей для наглядности
    plot_df = df.tail(100).copy()
    plot_df.set_index('timestamp', inplace=True)

    ema_col = f"EMA_{signal_data['used_ema']}"
    
    # Подготовка дополнительных графиков (EMA)
    # Используем цвета: EMA - синий
    adps = [
        mpf.make_addplot(plot_df[ema_col], color='blue', width=1.5)
    ]

    # Подготовка горизонтальных линий (Entry, SL, TP)
    hlines_data = [signal_data['entry'], signal_data['sl'], signal_data['tp1'], signal_data['tp2']]
    hlines_colors = ['gray', 'red', 'green', 'green']
    hlines_styles = ['dashed', 'solid', 'dashed', 'solid']

    # Буфер для сохранения картинки в памяти
    buf = io.BytesIO()

    # Рисуем график
    mpf.plot(
        plot_df,
        type='candle',
        style='charles', # Стиль (зеленые/красные свечи)
        addplot=adps,
        hlines=dict(hlines=hlines_data, colors=hlines_colors, linestyle=hlines_styles, linewidths=1),
        title=f"\n{signal_data['symbol']} - {signal_data['side']} Signal (EMA {signal_data['used_ema']})",
        ylabel='Price',
        savefig=dict(fname=buf, dpi=100, bbox_inches='tight'),
        volume=False,
        figsize=(10, 6)
    )
    
    buf.seek(0)
    return buf