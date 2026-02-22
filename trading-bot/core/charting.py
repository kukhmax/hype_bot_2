import io
import pandas as pd
import mplfinance as mpf
import numpy as np

def generate_setup_chart(
    token: str, 
    tf_display: str, 
    candles: list[dict], 
    ema_high: np.ndarray, 
    ema_low: np.ndarray,
    lookback: int = 150
) -> io.BytesIO:
    """
    Генерирует график свечей с наложенными линиями EMA20(High) и EMA20(Low), 
    и возвращает байтовый поток с PNG изображением.
    """
    # Берем только последние `lookback` свечей для визуализации
    if len(candles) > lookback:
        candles_slice = candles[-lookback:]
        ema_h_slice = ema_high[-lookback:]
        ema_l_slice = ema_low[-lookback:]
    else:
        candles_slice = candles
        ema_h_slice = ema_high
        ema_l_slice = ema_low

    # Подготавливаем DataFrame для mplfinance
    df_data = []
    for c in candles_slice:
        df_data.append({
            "Date": pd.to_datetime(c["t"], unit="ms"),
            "Open": c["o"],
            "High": c["h"],
            "Low": c["l"],
            "Close": c["c"],
            "Volume": c["v"]
        })
    df = pd.DataFrame(df_data)
    df.set_index("Date", inplace=True)

    # Подготавливаем дополнительные линии (EMA)
    apdd = [
        mpf.make_addplot(ema_h_slice, color="rgba(0,0,255,0.6)", width=1.5, title="EMA20(H)"),
        mpf.make_addplot(ema_l_slice, color="rgba(255,0,0,0.6)", width=1.5, title="EMA20(L)")
    ]

    # Настраиваем стиль
    mc = mpf.make_marketcolors(
        up="green", down="red",
        edge="inherit", wick="inherit",
        volume="in"
    )
    s = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle="--",
        y_on_right=True,
        facecolor="#1e1e1e",
        edgecolor="#2a2a2a",
        figcolor="#121212",
        gridcolor="#2a2a2a",
        rc={
            "axes.labelcolor": "white",
            "xtick.color": "white",
            "ytick.color": "white",
            "text.color": "white"
        }
    )

    buf = io.BytesIO()
    
    mpf.plot(
        df,
        type="candle",
        style=s,
        addplot=apdd,
        volume=True,
        figsize=(10, 6),
        title=f"\\n{token} {tf_display}",
        tight_layout=True,
        savefig=dict(fname=buf, dpi=120, format="png", bbox_inches="tight")
    )
    
    buf.seek(0)
    return buf
