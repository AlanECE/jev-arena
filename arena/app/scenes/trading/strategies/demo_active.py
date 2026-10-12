"""Strategie de DEMONSTRATION de l'interface (pas une strategie de trading serieuse).

Meme lecture du momentum que la provisoire, mais sans parler des frais au modele et avec une consigne qui
pousse a prendre position : elle sert a voir les positions s'ouvrir et se fermer a l'ecran en attendant la
strategie de Fable 5.1. Elle perdra de l'argent a cause des frais, c'est attendu.
"""
from . import indicators as ind
from .base import FundView, MarketView, Risk, Strategy


class DemoActive(Strategy):
    id = "demo-active"
    label = "Démo interface · momentum actif (ignore les frais)"
    short = "démo active (ignore les frais)"
    description = "Pour voir les positions s'ouvrir et se fermer. Ne tient pas compte des frais."
    risk = Risk(position_usd=10_000, min_hold_s=4, max_orders_per_min=20)

    def state(self, m: MarketView, f: FundView) -> dict:
        b = m.bars
        e9, e21 = ind.ema(ind.closes(b[-60:]), 9), ind.ema(ind.closes(b[-60:]), 21)
        return {
            "symbol": m.symbol,
            "bar_seconds": m.bar_sec,
            "returns_bps": {f"{k}_bars": round(ind.ret_bps(b, k), 2) for k in (1, 5, 15, 30)},
            "last_10_bar_returns_bps": ind.last_returns_bps(b, 10),
            "ema9_vs_ema21_bps": round((e9 / e21 - 1) * 1e4, 2) if e9 and e21 else None,
            "aggressive_buy_ratio_15_bars": None if ind.buy_ratio(b) is None else round(ind.buy_ratio(b), 2),
            "current_position": f.side,
            "unrealized_bps": round(f.unrealized_bps, 2),
        }

    def questions(self, m: MarketView, f: FundView) -> dict:
        crit = {
            "long": "Be long: recent momentum is up (positive short-term returns, buyers dominate)",
            "flat": "Stay out: momentum is mixed or exactly zero",
        }
        if m.allow_short:
            crit["short"] = "Be short: recent momentum is down (negative short-term returns, sellers dominate)"
        return {
            "position": {
                "type": "choice",
                "instructions": "Momentum-following scalper on 1-second bars. Follow the direction of the last "
                                "5 to 15 bars in `returns_bps`. Pick the position to hold now.",
                "criteria": crit,
            }
        }

    def target(self, answers: dict, m: MarketView, f: FundView) -> str | None:
        return (answers.get("position") or {}).get("choice")


STRATEGY = DemoActive()
