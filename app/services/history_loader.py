import logging
from typing import List, Dict, Any

from app.services.hyperliquid_api import HyperliquidAPI
from app.services.candles import RedisCandleStore

logger = logging.getLogger(__name__)


def _parse_candles(resp: Any) -> List[Dict[str, float]]:
    out: List[Dict[str, float]] = []
    if not resp:
        return out

    seq = None
    if isinstance(resp, dict):
        for key in ("candles", "data", "result", "rows"):
            if key in resp and isinstance(resp[key], list):
                seq = resp[key]
                break
        if seq is None and isinstance(resp.get("value"), list):
            seq = resp["value"]
    elif isinstance(resp, list):
        seq = resp

    if not isinstance(seq, list):
        return out

    for item in seq:
        if isinstance(item, dict):
            t = item.get("t") or item.get("time") or item.get("timestamp")
            o = item.get("o") or item.get("open")
            h = item.get("h") or item.get("high")
            l = item.get("l") or item.get("low")
            c = item.get("c") or item.get("close")
            if None not in (t, o, h, l, c):
                out.append({"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c)})
        elif isinstance(item, (list, tuple)) and len(item) >= 5:
            t, o, h, l, c = item[:5]
            try:
                out.append({"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c)})
            except Exception:
                continue
    return out


class HistoryLoader:
    @staticmethod
    async def preload_for_pair_tf(pair: str, tf: int, n: int = 200) -> int:
        store_pair = pair.upper()
        if store_pair.endswith("USDC"):
            store_pair = store_pair[:-4]
        existing = await RedisCandleStore.get_last(store_pair, tf, 1)
        if existing:
            return 0
        interval = f"{tf}m"
        logger.info("Загрузка истории %s %s (%s свечей)", store_pair, interval, n)
        resp = await HyperliquidAPI.get_candles(store_pair, interval=interval, n=n)
        candles = _parse_candles(resp)
        if not candles:
            logger.warning("Пустой ответ истории %s %s", store_pair, interval)
            return 0
        take = candles[-n:]
        added = 0
        for c in take:
            await RedisCandleStore.append(store_pair, tf, c, keep=n)
            added += 1
        logger.info("Загружено %s свечей %s %s", added, store_pair, interval)
        return added

    @staticmethod
    async def preload_all_from_subscriptions(n: int = 200) -> int:
        from app.services.subscription_service import SubscriptionService
        users = await SubscriptionService.get_all_users()
        pairs = set()
        for uid in users:
            subs = await SubscriptionService.get_user_subscriptions(uid)
            for s in subs:
                try:
                    tf = int(str(s["timeframe"]).replace("m", ""))
                    sp = str(s["pair"]).upper()
                    if sp.endswith("USDC"):
                        sp = sp[:-4]
                    pairs.add((sp, tf))
                except Exception:
                    continue
        total = 0
        for pair, tf in pairs:
            total += await HistoryLoader.preload_for_pair_tf(pair, tf, n=n)
        return total
