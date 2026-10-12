"""Strategie PROVISOIRE (en attendant celle de Fable 5.1).

Sert uniquement a faire tourner l'interface pour de vrai. Elle ne pretend pas gagner de l'argent :
le modele lit le momentum des dernieres secondes et choisit la position a tenir.
"""
from . import indicators as ind
from .base import FundView, MarketView, Risk, Strategy


class Provisoire(Strategy):
    id = "provisoire"
    label = "Provisoire · momentum (en attendant Fable 5.1)"
    short = "provisoire (Fable 5.1 à venir)"
    description = "Le modèle lit le mouvement des dernières secondes et choisit la position à tenir."
    risk = Risk(position_usd=10_000, min_hold_s=3, max_orders_per_min=24)

    def state(self, m: MarketView, f: FundView) -> dict:
        b = m.bars
        spread = ((m.ask - m.bid) / m.price * 1e4) if (m.bid and m.ask and m.price) else None
        e9, e21 = ind.ema(ind.closes(b[-60:]), 9), ind.ema(ind.closes(b[-60:]), 21)
        return {
            "symbol": m.symbol,
            "bar_seconds": m.bar_sec,
            "price": round(m.price, 4),
            "returns_bps": {f"{k}_bars": round(ind.ret_bps(b, k), 2) for k in (1, 5, 15, 60)},
            "last_10_bar_returns_bps": ind.last_returns_bps(b, 10),
            "rsi_14": None if ind.rsi(b) is None else round(ind.rsi(b), 1),
            "ema9_vs_ema21_bps": round((e9 / e21 - 1) * 1e4, 2) if e9 and e21 else None,
            "volatility_60_bars_bps": round(ind.volatility_bps(b), 2),
            "volume_zscore": round(ind.volume_z(b), 2),
            "aggressive_buy_ratio_15_bars": None if ind.buy_ratio(b) is None else round(ind.buy_ratio(b), 2),
            "position_in_60_bar_range": None if ind.range_position(b) is None else round(ind.range_position(b), 2),
            "spread_bps": None if spread is None else round(spread, 2),
            "fee_per_order_bps": m.fee_bps,
            "current_position": f.side,
            "unrealized_bps": round(f.unrealized_bps, 2),
            "seconds_in_position": round(f.seconds_in_position, 1),
        }

    def questions(self, m: MarketView, f: FundView) -> dict:
        crit = {
            "long": "Hold a long position: the price is likely to rise over the next few bars",
            "flat": "Hold no position: no clear edge, or the expected move does not cover the fees",
        }
        if m.allow_short:
            crit["short"] = "Hold a short position: the price is likely to fall over the next few bars"
        return {
            "position": {
                "type": "choice",
                "instructions": "You are a scalping trader. Given the live market `state` (1-second bars), "
                                "which position should be held for the next few bars? "
                                "Consider `current_position` and `fee_per_order_bps`.",
                "criteria": crit,
            }
        }

    def target(self, answers: dict, m: MarketView, f: FundView) -> str | None:
        a = answers.get("position") or {}
        return a.get("choice")


STRATEGY = Provisoire()
