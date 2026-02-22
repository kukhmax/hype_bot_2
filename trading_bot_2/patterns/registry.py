from patterns.ema_patterns import DriblingEMA, CatchingMA, AccumulationMA50, Angle45
from patterns.level_patterns import ReturnToLevel, CompressionBreakout, IslandGap, NewsBuyback
from patterns.volatility_patterns import SmallCandles, FishHook, PingPong, BodyShrinkage
from patterns.base import PatternDetector

ALL_PATTERNS: list[PatternDetector] = [
    DriblingEMA(),
    CatchingMA(),
    AccumulationMA50(),
    Angle45(),
    ReturnToLevel(),
    CompressionBreakout(),
    IslandGap(),
    NewsBuyback(),
    SmallCandles(),
    FishHook(),
    PingPong(),
    BodyShrinkage(),
]
