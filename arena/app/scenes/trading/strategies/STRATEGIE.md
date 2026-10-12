# Écrire une stratégie pour la scène trading de Jev Arena (brief pour Fable 5.1)

## Le contexte
Jev Arena est une application filmée pour une vidéo YouTube. Deux fonds scalpent **en direct** sur un compte
**Alpaca paper** (argent fictif) :
- à gauche, **Jev** (TypeSafe), un modèle de *décision* : il ne génère pas de texte. Il lit un état JSON et répond
  à des questions typées avec des probabilités. Il répond en **0,3 à 0,5 s** pour environ 0,00002 $.
- à droite, **Claude Opus 5.5**, qui reçoit exactement le même état et les mêmes questions, et répond en JSON en
  **3 à 5 s** pour environ 0,003 $.

À chaque fermeture de bougie (1 s par défaut ; 5, 15, 30 s ou 1 min possibles), chaque fonds demande une décision
à son modèle **s'il est libre**. S'il attend encore la réponse précédente, la bougie est « ratée ». Jev décide donc
presque à chaque seconde, Opus environ une fois toutes les 4 à 5 secondes. La vidéo veut montrer cette différence
de cadence. Elle ne cherche pas à faire croire qu'une IA gagne à coup sûr.

**Une seule stratégie est partagée par les deux fonds.** Seul le décideur change. Ta stratégie ne doit jamais
dépendre de `f.model`.

## Ce que tu dois livrer
Un fichier Python `strategies/fable.py` (à côté de `provisoire.py`, qui sert d'exemple) qui définit :

```python
from . import indicators as ind          # ret_bps, last_returns_bps, ema, rsi, volatility_bps, volume_z, buy_ratio, range_position
from .base import FundView, MarketView, Risk, Strategy

class Fable(Strategy):
    id = "fable"
    label = "Fable 5.1 · <nom court>"
    short = "<libellé court affiché pendant la session>"
    description = "<une phrase>"
    risk = Risk(position_usd=10_000, min_hold_s=..., max_orders_per_min=..., stop_loss_bps=None, take_profit_bps=None)

    def state(self, m: MarketView, f: FundView) -> dict: ...      # état JSON envoyé au modèle
    def questions(self, m: MarketView, f: FundView) -> dict: ...  # questions typées (format Jev, ci-dessous)
    def target(self, answers: dict, m: MarketView, f: FundView) -> str | None: ...  # 'long' | 'short' | 'flat' | None

STRATEGY = Fable()
```

Le fichier apparaît tout seul dans le menu « stratégie » de la page (bouton de rechargement ou redémarrage).

## Les données disponibles
`MarketView` (au moment de la décision, uniquement des bougies **déjà fermées**) :
- `symbol` (`BTC/USD` par défaut, ou une action comme `SPY`), `market` (`"crypto"` ou `"stocks"`), `bar_sec`
- `bars` : les 300 dernières bougies `{t, o, h, l, c, v, vb, n, bid, ask}` (`vb` = volume acheteur agressif,
  `n` = nombre de trades ; une seconde sans trade donne une bougie plate, avec `v = 0`)
- `price`, `bid`, `ask` (meilleures offres du flux de prix)
- `fee_bps` : frais estimés par ordre (crypto Alpaca, premier palier « taker » : **25 points de base par ordre**,
  donc 50 pour un aller-retour ; actions : 0, mais l'écart achat-vente est payé)
- `allow_short` : **False en crypto** (Alpaca n'autorise pas la vente à découvert de crypto). En actions, True si le
  titre est empruntable. Si `target()` renvoie `'short'` alors qu'il est interdit, le moteur le traite comme `'flat'`.

`FundView` (le fonds qui décide) : `side` (`flat`, `long`, `short`), `qty`, `entry_price`, `unrealized_bps`,
`seconds_in_position`, `realized_pnl`, `trades`, `last_choices` (5 dernières décisions).

## Le format des questions (celui de Jev)
```python
{
  "position": {"type": "choice", "instructions": "... peut citer des clés de l'état entre `backticks` ...",
               "criteria": {"long": "description", "flat": "description", "short": "description"}},
  "edge": {"type": "noul", "instructions": "..."},                    # probabilité 0-1 que ce soit vrai (pas de criteria)
  "conviction": {"type": "score", "instructions": "...", "criteria": ["aucune", "faible", "forte"]},
}
```
Réponses reçues dans `target(answers, ...)` :
- choice : `answers["position"] = {"choice": "long", "probabilities": {"long": 0.62, ...}}`. Côté Opus, il n'y a
  pas de `probabilities` mais `"confidence"` (déclarée par le modèle).
- noul : `{"noul": 0.71}` ; score : `{"score": 1.4}` (Jev renvoie une valeur continue, Opus un entier).
- Si une réponse manque ou est invalide, la décision est déjà comptée en erreur et `target()` n'est pas appelé.

Bonnes pratiques avec Jev : des questions **fermées et courtes**, des options décrites concrètement, des nombres déjà
calculés dans l'état (Jev lit mieux un indicateur prêt qu'une longue série brute). Une seule décision par bougie,
donc garde peu de questions (1 à 3).

## Ce que fait le moteur (tu n'as pas à le coder)
- Il envoie les ordres au marché sur Alpaca. Un passage de long à short est fait en deux ordres (fermer, puis ouvrir).
- `Risk.min_hold_s` bloque un changement de position trop tôt. `Risk.max_orders_per_min` plafonne les ordres
  (Alpaca accepte environ 200 requêtes par minute et par compte). `Risk.position_usd` fixe la taille de chaque position.
- Les gains viennent des vraies exécutions Alpaca, et les frais estimés sont déduits.
- `stop_loss_bps` et `take_profit_bps` existent dans `Risk` mais ne sont **pas encore appliqués** par le moteur.
  Si tu en veux, gère-les dans `target()` grâce à `f.unrealized_bps`, ou demande-les.

## Ce qu'on sait déjà (tests réels de septembre, bougies d'1 minute sur une journée)
- Avec des frais réalistes, **aucun modèle n'a battu « acheter et attendre », ni même « ne rien faire »**. Jev
  perdait de −14 % (frais 0,02 %) à −52 % (frais 0,1 %) en tradant 368 fois. Prévenu des frais dans l'état, il
  restait à plat 94 % du temps, ce qui réduisait la perte sans la supprimer.
- Le mouvement moyen d'une minute (environ 1,2 point de base) est bien plus petit qu'un aller-retour de frais.
- Une stratégie « solide » ici, c'est donc d'abord une stratégie **qui ne trade que si le mouvement attendu couvre
  les coûts**, qui coupe ses pertes et qui reste lisible à l'écran (des positions qu'on voit s'ouvrir et se fermer).
  Il faut le dire honnêtement dans la vidéo plutôt que promettre un gain.
