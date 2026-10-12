"""Interface d'une strategie de trading pour l'arene.

UNE strategie est partagee par les deux fonds : Jev et Opus recoivent EXACTEMENT le meme etat et les memes
questions. Seul le decideur change. C'est la condition pour que la video compare deux modeles et pas deux
strategies.

Pour ajouter une strategie (par exemple celle de Fable 5.1) : creer un fichier dans ce dossier qui definit
`STRATEGY = MaStrategie()` (sous-classe de Strategy). Elle apparait toute seule dans le menu de la page.
Voir STRATEGIE.md pour le detail des donnees disponibles.
"""
from dataclasses import dataclass, field


@dataclass
class Risk:
    position_usd: float = 10_000.0     # taille d'une position (notionnel)
    min_hold_s: float = 0.0            # duree minimale avant de changer de position
    max_orders_per_min: int = 30       # garde-fou (Alpaca limite a ~200 requetes/min par compte)
    stop_loss_bps: float | None = None # sortie forcee si la perte latente depasse (points de base)
    take_profit_bps: float | None = None
    max_hold_s: float | None = None    # duree maximale d'une position (sortie mecanique)


@dataclass
class MarketView:
    """Ce que voit la strategie a la fermeture d'une bougie."""
    symbol: str
    market: str                # "crypto" ou "stocks"
    bar_sec: int
    bars: list                 # bougies fermees, la plus recente en dernier : {t,o,h,l,c,v,vb,n,bid,ask}
    price: float               # dernier prix
    bid: float | None
    ask: float | None
    fee_bps: float             # frais estimes par ordre (crypto Alpaca 25 bps taker ; actions 0)
    allow_short: bool          # False en crypto (Alpaca n'autorise pas la vente a decouvert de crypto)


@dataclass
class FundView:
    """Ce que voit la strategie du fonds qui decide."""
    model: str                 # "jev" ou "opus" (ne PAS l'utiliser pour changer la strategie)
    side: str                  # "flat", "long" ou "short"
    qty: float
    entry_price: float | None
    unrealized_bps: float      # gain latent en points de base (0 si a plat)
    seconds_in_position: float
    realized_pnl: float
    trades: int
    last_choices: list = field(default_factory=list)   # dernieres decisions de ce fonds


class Strategy:
    id = "base"
    label = "Strategie"
    short = ""          # libelle court affiche pendant la session
    description = ""
    risk = Risk()

    def state(self, m: MarketView, f: FundView) -> dict:
        """Etat envoye au modele (dict JSON). Jev le lit ; Opus le recoit en JSON dans son prompt."""
        raise NotImplementedError

    def questions(self, m: MarketView, f: FundView) -> dict:
        """Questions typees (format Jev). Voir app/core/deciders.py."""
        raise NotImplementedError

    def target(self, answers: dict, m: MarketView, f: FundView) -> str | None:
        """Position voulue apres la decision : 'long', 'short', 'flat' ou None (ne rien changer)."""
        raise NotImplementedError
