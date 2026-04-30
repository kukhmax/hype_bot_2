from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import Workbook


def _fmt_dt_ms(ts_ms: int, tz: ZoneInfo) -> str:
    try:
        ts_ms = int(ts_ms)
    except Exception:
        return ""
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(tz)
    except Exception:
        return ""
    return dt.strftime("%H:%M %d/%m/%y")


def build_trades_xlsx(trades: list[dict], tf: str, tf_ms: int, base_equity: float, tz_name: str = "Europe/Warsaw") -> bytes:
    tz = ZoneInfo(tz_name)
    wb = Workbook()
    ws = wb.active
    ws.title = "Trades"

    headers = [
        "pair",
        "tf",
        "side",
        "opened",
        "closed",
        "entry",
        "exit",
        "qty",
        "pnl",
        "pnl_pct",
        "notional",
        "reason",
        "cluster_time",
        "equity_after",
        "duration_min",
    ]
    ws.append(headers)

    def _get_float(d: dict, k: str) -> float | None:
        try:
            return float(d.get(k))
        except Exception:
            return None

    def _get_int(d: dict, k: str) -> int | None:
        try:
            return int(d.get(k))
        except Exception:
            return None

    sorted_trades = sorted(trades, key=lambda x: int(x.get("closed_t", 0) or 0))

    equity = float(base_equity)
    wins = 0
    losses = 0
    sum_win = 0.0
    sum_loss = 0.0
    total_pnl = 0.0

    for t in sorted_trades:
        pair = str(t.get("pair", "")).upper()
        side = str(t.get("side", "")).upper()
        reason = str(t.get("reason", ""))
        entry = _get_float(t, "entry")
        exit_px = _get_float(t, "exit")
        qty = _get_float(t, "qty")
        pnl = _get_float(t, "pnl")
        opened_t = _get_int(t, "opened_t")
        closed_t = _get_int(t, "closed_t")
        cluster_t = _get_int(t, "cluster_t")

        if entry is None or exit_px is None or qty is None or pnl is None:
            continue

        opened_close_ms = (opened_t + tf_ms) if opened_t else None
        closed_close_ms = (closed_t + tf_ms) if closed_t else None
        cluster_close_ms = (cluster_t + tf_ms) if cluster_t else None

        notional = abs(entry * qty)
        pnl_pct = (pnl / notional * 100.0) if notional > 0 else 0.0

        duration_min = None
        if opened_close_ms and closed_close_ms and closed_close_ms >= opened_close_ms:
            duration_min = (closed_close_ms - opened_close_ms) / 60000.0

        total_pnl += pnl
        equity += pnl

        if pnl >= 0:
            wins += 1
            sum_win += pnl
        else:
            losses += 1
            sum_loss += pnl

        ws.append(
            [
                pair,
                tf,
                side,
                _fmt_dt_ms(opened_close_ms, tz) if opened_close_ms else "",
                _fmt_dt_ms(closed_close_ms, tz) if closed_close_ms else "",
                entry,
                exit_px,
                qty,
                pnl,
                pnl_pct,
                notional,
                reason,
                _fmt_dt_ms(cluster_close_ms, tz) if cluster_close_ms else "",
                equity,
                duration_min,
            ]
        )

    ws2 = wb.create_sheet("Summary")
    ws2.append(["tf", tf])
    ws2.append(["base_equity", float(base_equity)])
    ws2.append(["total_trades", wins + losses])
    ws2.append(["wins", wins])
    ws2.append(["losses", losses])
    win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0
    ws2.append(["win_rate_pct", win_rate])
    ws2.append(["total_pnl", total_pnl])
    avg_win = (sum_win / wins) if wins > 0 else 0.0
    avg_loss = (sum_loss / losses) if losses > 0 else 0.0
    ws2.append(["avg_win", avg_win])
    ws2.append(["avg_loss", avg_loss])
    profit_factor = (sum_win / abs(sum_loss)) if sum_loss < 0 else 0.0
    ws2.append(["profit_factor", profit_factor])
    ws2.append(["equity_end", float(base_equity) + total_pnl])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()

