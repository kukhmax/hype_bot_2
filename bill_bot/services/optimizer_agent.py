from __future__ import annotations

import json
import logging
from bill_bot.services.execution import RedisTradeState

logger = logging.getLogger(__name__)

class TradeOptimizerAgent:
    def __init__(self, trade_state: RedisTradeState, pairs: list[str], timeframes: list[str]):
        self.trade_state = trade_state
        self.pairs = [p.upper() for p in pairs]
        self.timeframes = timeframes

    async def get_all_completed_trades(self, user_id: int) -> list[dict]:
        """Собирает все завершенные сделки по всем парам и таймфреймам из Redis"""
        all_trades = []
        for pair in self.pairs:
            for tf in self.timeframes:
                try:
                    trades = await self.trade_state.get_trades(user_id, pair, tf, limit=5000)
                    all_trades.extend(trades)
                except Exception as e:
                    logger.error(f"OptimizerAgent: Error fetching trades for {pair}:{tf} - {e}")
        return all_trades

    async def analyze_history(self, user_id: int) -> dict:
        """
        Анализирует историю сделок и группирует показатели по парам, таймфреймам и направлению.
        """
        trades = await self.get_all_completed_trades(user_id)
        
        global_stats = {"wins": 0, "losses": 0, "total_pnl": 0.0, "total_fee": 0.0}
        by_pair: dict[str, dict] = {}
        by_setup: dict[str, dict] = {}  # key: "PAIR:TF:SIDE"
        by_trailing: dict[str, dict] = {}
        by_spread_bucket: dict[str, dict] = {}

        def _accumulate(store: dict[str, dict], key: str, is_win: bool, pnl: float) -> None:
            if not key:
                return
            if key not in store:
                store[key] = {"wins": 0, "losses": 0, "total_pnl": 0.0}
            if is_win:
                store[key]["wins"] += 1
            else:
                store[key]["losses"] += 1
            store[key]["total_pnl"] += pnl

        for t in trades:
            pnl = float(t.get("pnl", 0.0))
            fee = float(t.get("fee", 0.0))
            pair = str(t.get("pair", "")).upper()
            tf = str(t.get("tf", ""))
            side = str(t.get("side", "")).upper()
            trailing = str(t.get("trailing_strategy", "")).upper()
            setup_features = t.get("setup_features") if isinstance(t.get("setup_features"), dict) else {}
            spread_bucket = str(setup_features.get("cluster_spread_atr_bucket", "")).lower()
            
            if not pair or not tf or not side:
                continue

            # Global
            is_win = pnl > 0
            if is_win:
                global_stats["wins"] += 1
            else:
                global_stats["losses"] += 1
            global_stats["total_pnl"] += pnl
            global_stats["total_fee"] += fee

            # By Pair
            _accumulate(by_pair, pair, is_win, pnl)

            # By Setup (Pair + TF + Side)
            setup_key = f"{pair}:{tf}:{side}"
            _accumulate(by_setup, setup_key, is_win, pnl)
            _accumulate(by_trailing, trailing, is_win, pnl)
            _accumulate(by_spread_bucket, spread_bucket, is_win, pnl)

        # Calculate win rates
        def calc_win_rate(wins: int, losses: int) -> float:
            total = wins + losses
            return float(wins) / total if total > 0 else 0.5

        global_win_rate = calc_win_rate(global_stats["wins"], global_stats["losses"])
        
        pair_stats = {}
        for p, s in by_pair.items():
            pair_stats[p] = {
                "win_rate": calc_win_rate(s["wins"], s["losses"]),
                "total": s["wins"] + s["losses"],
                "pnl": s["total_pnl"]
            }

        setup_stats = {}
        for k, s in by_setup.items():
            setup_stats[k] = {
                "win_rate": calc_win_rate(s["wins"], s["losses"]),
                "total": s["wins"] + s["losses"],
                "pnl": s["total_pnl"]
            }

        trailing_stats = {}
        for k, s in by_trailing.items():
            trailing_stats[k] = {
                "win_rate": calc_win_rate(s["wins"], s["losses"]),
                "total": s["wins"] + s["losses"],
                "pnl": s["total_pnl"]
            }

        spread_bucket_stats = {}
        for k, s in by_spread_bucket.items():
            spread_bucket_stats[k] = {
                "win_rate": calc_win_rate(s["wins"], s["losses"]),
                "total": s["wins"] + s["losses"],
                "pnl": s["total_pnl"]
            }

        return {
            "global": {
                "win_rate": global_win_rate,
                "total": global_stats["wins"] + global_stats["losses"],
                "pnl": global_stats["total_pnl"],
                "fee": global_stats["total_fee"]
            },
            "by_pair": pair_stats,
            "by_setup": setup_stats,
            "by_trailing": trailing_stats,
            "by_spread_bucket": spread_bucket_stats,
        }

    async def predict_probability(
        self,
        user_id: int,
        pair: str,
        tf: str,
        side: str,
        default_risk_pct: float,
        features: dict | None = None,
    ) -> dict:
        """
        Предсказывает вероятность успеха сделки и выбирает стратегию риск-менеджмента и трейлинга.
        """
        pair_upper = pair.upper()
        side_upper = side.upper()
        
        # Анализируем историю
        stats = await self.analyze_history(user_id)
        
        setup_key = f"{pair_upper}:{tf}:{side_upper}"
        setup_info = stats["by_setup"].get(setup_key)
        pair_info = stats["by_pair"].get(pair_upper)
        global_info = stats["global"]
        spread_bucket = str((features or {}).get("cluster_spread_atr_bucket", "")).lower()
        bucket_info = stats["by_spread_bucket"].get(spread_bucket) if spread_bucket else None

        # Иерархический расчет вероятности (Setup -> Pair -> Global -> 0.5)
        # Если по данному сетапу есть хотя бы 3 сделки, берем его винрейт
        if setup_info and setup_info["total"] >= 3:
            probability = setup_info["win_rate"]
            source = "setup_history"
            total_samples = setup_info["total"]
        elif bucket_info and bucket_info["total"] >= 5:
            probability = bucket_info["win_rate"]
            source = f"spread_bucket:{spread_bucket}"
            total_samples = bucket_info["total"]
        # Если по монете есть хотя бы 5 сделок, берем винрейт монеты
        elif pair_info and pair_info["total"] >= 5:
            probability = pair_info["win_rate"]
            source = "pair_history"
            total_samples = pair_info["total"]
        # Если есть хотя бы 10 сделок глобально, берем глобальный винрейт
        elif global_info["total"] >= 10:
            probability = global_info["win_rate"]
            source = "global_history"
            total_samples = global_info["total"]
        else:
            probability = 0.5
            source = "default"
            total_samples = 0

        # Ограничим вероятность разумными рамками (0.1 - 0.9)
        probability = max(0.1, min(0.9, probability))

        if spread_bucket == "wide":
            probability = max(0.1, probability - 0.05)
        elif spread_bucket == "tight":
            probability = min(0.9, probability + 0.03)

        # Принятие решений о трейлинге и объеме риска на основе вероятности
        if spread_bucket == "wide":
            trailing_strategy = "SCALPING"
            risk_multiplier = 0.8 if probability >= 0.45 else 0.5
            note = "Широкий сетап по ATR. Предпочтителен защитный трейлинг и пониженный риск."
        elif probability >= 0.60:
            trailing_strategy = "TRENDING"
            risk_multiplier = 1.3  # Увеличиваем риск для сильных сетапов
            note = "Высокая историческая вероятность успеха. Используем трендовый (широкий) трейлинг."
        elif probability >= 0.45:
            trailing_strategy = "SCALPING"
            risk_multiplier = 1.0  # Стандартный риск
            note = "Умеренная вероятность. Используем агрессивный (защитный) трейлинг."
        else:
            trailing_strategy = "SCALPING"
            risk_multiplier = 0.5  # Снижаем риск вдвое для слабых сетапов
            note = "Низкая вероятность успеха. Снижаем риск вдвое, защитный трейлинг."

        suggested_risk = default_risk_pct * risk_multiplier

        return {
            "probability": probability,
            "trailing_strategy": trailing_strategy,
            "suggested_risk_pct": suggested_risk,
            "source": source,
            "total_samples": total_samples,
            "note": note
        }
