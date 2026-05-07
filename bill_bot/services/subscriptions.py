from __future__ import annotations

import json

import redis.asyncio as redis


class SubscriptionStore:
    def __init__(self, r: redis.Redis, tf: str):
        self.r = r
        self.tf = tf

    def user_pairs_key(self, user_id: int) -> str:
        return f"sub:user:{user_id}:pairs:{self.tf}"

    def user_cfg_key(self, user_id: int) -> str:
        return f"sub:user:{user_id}:cfg:{self.tf}"

    def user_active_key(self, user_id: int) -> str:
        return f"sub:user:{user_id}:active:{self.tf}"

    def pair_users_key(self, pair: str) -> str:
        return f"sub:pair:{pair}:{self.tf}:users"

    def active_pairs_key(self) -> str:
        return f"active_pairs:{self.tf}"

    def user_timeframe_key(self, user_id: int) -> str:
        return f"sub:user:{user_id}:timeframe"

    async def get_user_timeframe(self, user_id: int, default: str) -> str:
        v = await self.r.get(self.user_timeframe_key(user_id))
        if not v:
            return default
        return str(v)

    async def set_user_timeframe(self, user_id: int, tf: str) -> None:
        await self.r.set(self.user_timeframe_key(user_id), tf)

    async def is_active(self, user_id: int) -> bool:
        v = await self.r.get(self.user_active_key(user_id))
        return v == "1"

    async def set_active(self, user_id: int, active: bool) -> None:
        await self.r.set(self.user_active_key(user_id), "1" if active else "0")
        pairs = await self.get_user_pairs(user_id)
        for p in pairs:
            await self._refresh_active_pairs(p)

    async def get_user_pairs(self, user_id: int) -> list[str]:
        pairs = await self.r.smembers(self.user_pairs_key(user_id))
        out = [p for p in pairs if p]
        out.sort()
        return out

    async def toggle_pair(self, user_id: int, pair: str) -> bool:
        pair = pair.upper()
        user_key = self.user_pairs_key(user_id)
        pair_key = self.pair_users_key(pair)

        is_member = await self.r.sismember(user_key, pair)
        if is_member:
            await self.r.srem(user_key, pair)
            await self.r.srem(pair_key, str(user_id))
        else:
            await self.r.sadd(user_key, pair)
            await self.r.sadd(pair_key, str(user_id))

        await self._refresh_active_pairs(pair)
        return not is_member

    async def _refresh_active_pairs(self, pair: str) -> None:
        pair_key = self.pair_users_key(pair)
        raw_users = await self.r.smembers(pair_key)
        any_active = False
        for u in raw_users:
            try:
                uid = int(u)
            except Exception:
                continue
            if await self.is_active(uid):
                any_active = True
                break
        if any_active:
            await self.r.sadd(self.active_pairs_key(), pair)
        else:
            await self.r.srem(self.active_pairs_key(), pair)

    async def get_active_pairs(self) -> list[str]:
        pairs = await self.r.smembers(self.active_pairs_key())
        out = [p for p in pairs if p]
        out.sort()
        return out

    async def get_pair_users(self, pair: str) -> list[int]:
        raw = await self.r.smembers(self.pair_users_key(pair.upper()))
        out: list[int] = []
        for x in raw:
            try:
                out.append(int(x))
            except Exception:
                continue
        out.sort()
        return out

    async def set_user_risk(self, user_id: int, risk_pct: float) -> None:
        await self.r.hset(self.user_cfg_key(user_id), mapping={"risk_pct": float(risk_pct)})

    async def set_user_rr(self, user_id: int, rr: float) -> None:
        await self.r.hset(self.user_cfg_key(user_id), mapping={"rr": float(rr)})

    async def set_user_mode(self, user_id: int, mode: str) -> None:
        await self.r.hset(self.user_cfg_key(user_id), mapping={"trade_mode": str(mode).upper()})

    async def set_user_margin(self, user_id: int, margin_pct: float) -> None:
        await self.r.hset(self.user_cfg_key(user_id), mapping={"margin_pct": float(margin_pct)})

    async def get_user_cfg(self, user_id: int) -> dict:
        raw = await self.r.hgetall(self.user_cfg_key(user_id))
        cfg: dict = {}
        for k, v in raw.items():
            if isinstance(k, bytes):
                k = k.decode('utf-8')
            if isinstance(v, bytes):
                v = v.decode('utf-8')
            try:
                cfg[k] = float(v)
            except Exception:
                cfg[k] = v
        return cfg

    async def dump_user_state(self, user_id: int) -> dict:
        return {
            "active": await self.is_active(user_id),
            "pairs": await self.get_user_pairs(user_id),
            "cfg": await self.get_user_cfg(user_id),
        }
