"""Stratégie « Ressort » : retour à la moyenne sur excès court (la stratégie de la vidéo).

Recherche du 12/10/2026 (research/, données SIP Alpaca, bougies d'1 s, 3 journées puis validation sur 10) :
  - après un mouvement de plus de 2 à 2,5 écarts-types en 30-60 s, TSLA revient en moyenne de 2 à 5 bps dans la
    minute qui suit (écart achat-vente à payer : 1,6 bps) ; QQQ revient moins mais ne coûte que 0,2 bps d'écart ;
  - SPY ne rapporte rien, NVDA fait l'inverse (momentum) : ne pas fader NVDA.
  - les stops serrés (4-8 bps) détruisent l'avantage : le bruit normal de TSLA sur une minute est de ~8 bps.
    On tient donc un temps FIXE (60-120 s), avec un stop large de sécurité.

Rôle du modèle : à chaque bougie, quand le fonds est à plat, il reçoit l'étirement mesuré (z-scores, VWAP, position
dans la fourchette, volume) et décide d'entrer long, short ou de rester à plat. Les sorties (durée, objectif, stop)
sont mécaniques et identiques pour les deux fonds : seule la vitesse et la qualité de la décision d'entrée changent.
"""
from . import indicators as ind
from .base import FundView, MarketView, Risk, Strategy

PRESETS = {
    # symbole : (seuil d'étirement en σ, tenue en s, objectif bps ou None, stop bps)
    "TSLA": {"thr": 2.5, "hold": 90, "tp": 8.0, "sl": 16.0, "window": 30},   # valide sur 10 journees (research/)
    "QQQ": {"thr": 1.5, "hold": 120, "tp": 8.0, "sl": 16.0, "window": 30},
    "SPY": {"thr": 2.0, "hold": 120, "tp": 8.0, "sl": 16.0, "window": 30},
}
DEFAULT = {"thr": 2.0, "hold": 90, "tp": 8.0, "sl": 24.0, "window": 30}


def stretch(bars, window: int):
    """z-score du rendement des `window` dernières secondes, normalisé par la volatilité 1 s sur 60 s."""
    vol = ind.volatility_bps(bars, 60)
    if vol <= 0 or len(bars) <= window:
        return 0.0, vol
    r = ind.ret_bps(bars, window)
    return r / (vol * window ** 0.5), vol


class Ressort(Strategy):
    id = "ressort"
    label = "Ressort · retour à la moyenne sur excès court (TSLA, QQQ)"
    short = "Ressort · fade des excès"
    description = ("Quand le prix s'écarte de plus de 2 σ en 30-60 s, le modèle prend la position inverse ; "
                   "sortie après un temps fixe, objectif +8 bps, stop large.")
    risk = Risk(position_usd=100_000, min_hold_s=0, max_orders_per_min=20)
    # réglages par symbole (voir PRESETS) appliqués par le moteur via params()
    decide_when_in_position = False      # pendant la tenue, les sorties sont mécaniques

    thr_override: float | None = None    # choisi dans la page : 2,5 σ (validé) ou 2,0 σ (plus de trades)

    def params(self, symbol: str) -> dict:
        p = dict(PRESETS.get(symbol.upper().replace("/", ""), DEFAULT))
        if self.thr_override:
            p["thr"] = float(self.thr_override)
        return p

    def state(self, m: MarketView, f: FundView) -> dict:
        p = self.params(m.symbol)
        b = m.bars
        z30, vol = stretch(b, 30)
        z60, _ = stretch(b, 60)
        z10, _ = stretch(b, 10)
        rng = ind.range_position(b, 300)
        spread = ((m.ask - m.bid) / m.price * 1e4) if (m.bid and m.ask and m.price) else None
        return {
            "symbol": m.symbol,
            "bar_seconds": m.bar_sec,
            "stretch_sigma": {"10s": round(z10, 2), "30s": round(z30, 2), "60s": round(z60, 2)},
            "entry_threshold_sigma": p["thr"],
            "move_bps": {"30s": round(ind.ret_bps(b, 30), 1), "60s": round(ind.ret_bps(b, 60), 1)},
            "volatility_1s_bps": round(vol, 2),
            "vwap_deviation_bps": None if ind.range_position(b, 60) is None else round(_vwap_dev(b), 1),
            "position_in_5min_range": None if rng is None else round(rng * 2 - 1, 2),
            "volume_zscore": round(ind.volume_z(b), 2),
            "spread_bps": None if spread is None else round(spread, 2),
            "planned_hold_seconds": p["hold"],
            "current_position": f.side,
        }

    def questions(self, m: MarketView, f: FundView) -> dict:
        p = self.params(m.symbol)
        crit = {
            "long": f"Buy now: the price just fell too fast (stretch below -{p['thr']} sigma over 30-60 s) and should bounce back within {p['hold']} s",
            "flat": "Stay out: the stretch is inside the normal range, or the move is backed by a real trend (strong volume, range breakout)",
        }
        if m.allow_short:
            crit["short"] = f"Sell short now: the price just rose too fast (stretch above +{p['thr']} sigma over 30-60 s) and should fall back within {p['hold']} s"
        return {
            "position": {
                "type": "choice",
                "instructions": (
                    "Mean-reversion scalper on 1-second bars. Read `stretch_sigma` (how far the last 30-60 s move is "
                    "from normal, in standard deviations) against `entry_threshold_sigma`. A move beyond the threshold "
                    "usually snaps back partly within `planned_hold_seconds`; a move inside the threshold is noise. "
                    "Be careful with breakouts: a stretch together with a very high `volume_zscore` and a price at the edge "
                    "of `position_in_5min_range` can be a real trend. Which position should be taken now?"),
                "criteria": crit,
            }
        }

    def target(self, answers: dict, m: MarketView, f: FundView) -> str | None:
        return (answers.get("position") or {}).get("choice")

    def exits(self, m: MarketView) -> dict:
        """Règles de sortie mécaniques, lues par le moteur : tenue fixe, objectif, stop (en bps)."""
        p = self.params(m.symbol)
        return {"max_hold_s": p["hold"], "take_profit_bps": p["tp"], "stop_loss_bps": p["sl"]}

    def indicators(self, m: MarketView) -> dict:
        """Indicateurs à tracer sur le graphique (une valeur par bougie fermée) :
        - stretch : étirement en σ (jauge du bas) ; thr : le seuil
        - band_up / band_dn : prix au-delà desquels la bougie suivante déclencherait une entrée (short / long),
          c'est le seuil traduit en prix, qui glisse avec le temps
        - ema20, vwap"""
        b = m.bars
        p = self.params(m.symbol)
        w = p["window"]
        z, vol = stretch(b, w)
        e20 = ind.ema(ind.closes(b[-120:]), 20)
        band_up = band_dn = None
        if vol > 0 and len(b) > w + 6:
            # reference = cloture d'il y a w secondes (lissee sur 6 s pour une bande lisible), seuil traduit en prix
            refs = [x["c"] for x in b[-w - 5:-w + 1]] if w > 1 else [b[-1]["c"]]
            ref = sum(refs) / len(refs)
            k = p["thr"] * vol * (w ** 0.5) / 1e4
            band_up, band_dn = ref * (1 + k), ref * (1 - k)
        return {"stretch": round(z, 2), "ema20": e20, "vwap": _vwap(b), "thr": p["thr"],
                "band_up": band_up, "band_dn": band_dn}


def _vwap(bars):
    v = sum(x["v"] for x in bars)
    if v <= 0:
        return None
    return sum(((x["h"] + x["l"] + x["c"]) / 3) * x["v"] for x in bars) / v


def _vwap_dev(bars):
    vw = _vwap(bars)
    if not vw:
        return 0.0
    return (bars[-1]["c"] / vw - 1) * 1e4


STRATEGY = Ressort()


# ---------------------------------------------------------------------------------------------------------------
# « Rupture » : la stratégie BTC. Même mesure d'excès, décision INVERSE : on suit la cassure.
# Étude du 12/10/2026 (research/btc_*.py, 10 journées Binance BTCUSDT à la seconde, écart Coinbase mesuré
# 0,001 bps médian / 0,17 bps p90) : après un excès de 2,5 σ en 10 s, le bitcoin CONTINUE de +0,5 à +0,7 bps sur
# 1-2 minutes (dizaines de milliers d'événements) ; fader perd sur 100 % des réglages.
# ---------------------------------------------------------------------------------------------------------------
# Validé sur 10 journées (02-11/10/2026, research/btc_momentum.py, écart 0,2 bps) : profil Jev +585 bps/jour,
# ~920 trades/jour, +0,60 bps par trade, 10 jours positifs sur 10 ; profil Opus (3 s de retard) +187 bps/jour.
PRESETS_RUPTURE = {
    "BTCUSD": {"thr": 2.0, "hold": 60, "tp": 5.0, "sl": 2.0, "window": 10},
    "ETHUSD": {"thr": 2.0, "hold": 60, "tp": 5.0, "sl": 2.0, "window": 10},
}
DEFAULT_RUPTURE = {"thr": 2.0, "hold": 60, "tp": 5.0, "sl": 2.0, "window": 10}


class Rupture(Ressort):
    id = "rupture"
    label = "Rupture · suivre la cassure (BTC)"
    short = "Rupture · suivi des cassures"
    description = ("Quand le prix casse de plus de 2 σ en 10 s, le modèle suit le mouvement ; "
                   "sortie après 60 s, objectif +5 pb, stop −2 pb.")
    risk = Risk(position_usd=100_000, min_hold_s=0, max_orders_per_min=20)

    def params(self, symbol: str) -> dict:
        p = dict(PRESETS_RUPTURE.get(symbol.upper().replace("/", ""), DEFAULT_RUPTURE))
        if self.thr_override:
            p["thr"] = float(self.thr_override)
        return p

    def state(self, m: MarketView, f: FundView) -> dict:
        s = super().state(m, f)
        p = self.params(m.symbol)
        s["signal_window_seconds"] = p["window"]
        s["regime"] = "momentum: on 1-second BTC data, moves beyond the threshold tend to CONTINUE for 1-2 minutes"
        return s

    def questions(self, m: MarketView, f: FundView) -> dict:
        p = self.params(m.symbol)
        w = p["window"]
        crit = {
            "long": f"Buy now: the price just broke UP by more than {p['thr']} sigma in {w} s (see `stretch_sigma`), follow the breakout for {p['hold']} s",
            "flat": "Stay out: the move is inside the normal range (no breakout), or the breakout is already exhausted",
        }
        if m.allow_short:
            crit["short"] = f"Sell short now: the price just broke DOWN by more than {p['thr']} sigma in {w} s, follow the breakdown for {p['hold']} s"
        return {
            "position": {
                "type": "choice",
                "instructions": (
                    "Breakout-following scalper on 1-second BTC bars. Read `stretch_sigma` (how far the last "
                    f"{w}-60 s move is from normal, in standard deviations) against `entry_threshold_sigma`. "
                    "On this market a move beyond the threshold usually CONTINUES in the same direction for "
                    "`planned_hold_seconds`; a move inside the threshold is noise. A breakout confirmed by a high "
                    "`volume_zscore` is stronger. Which position should be taken now?"),
                "criteria": crit,
            }
        }


STRATEGY_RUPTURE = Rupture()
