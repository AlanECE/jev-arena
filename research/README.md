# Étude de stratégie · scalping à la seconde (12/10/2026)

Tout vient de données **réelles** : trades SIP (toutes bourses) d'Alpaca, agrégés en bougies d'1 seconde, et cotations
NBBO échantillonnées pour mesurer l'écart achat-vente. Hors séance, les 15 dernières minutes ne sont pas accessibles
avec un compte gratuit ; tout le reste l'est.

| Script | Rôle |
|---|---|
| `fetch_sip.py SYM1,SYM2 J1,J2` | télécharge les trades SIP de 9 h 30 à 16 h (New York) → `data/<SYM>_<jour>_1s.csv` + cotations |
| `study.py` | signaux (rendements, flux par règle du tick, volatilité, EMA, VWAP, fourchette) et corrélation de rang avec le rendement des 5-60 s suivantes |
| `condmean.py` | avantage brut par événement : de combien le prix revient après un excès de 1,5 à 3 σ, à 5 s … 4 min, face à l'écart |
| `grid.py`, `grid2.py` | premiers backtests (horizon court, puis long) : exécutions réalistes, deux profils de latence |
| `grid3.py` | la règle propre (tenue fixe, stop large) : grille seuil × tenue × objectif × stop, par symbole et par profil |
| `tsla_detail.py` | détail jour par jour des réglages candidats |

Profils : **jev** = décision à chaque seconde, exécution dans la seconde suivante ; **opus** = décision toutes les 4 s,
exécution 3 s plus tard. Exécution = premier échange de la seconde suivante ± demi-écart NBBO médian du jour.

## Résultats
1. **Retour à la moyenne réel à 5-60 s** sur SPY, TSLA, NVDA (IC −0,05 à −0,13 pour r1…r30, rng60, z10/z30). Le flux
   d'ordres (règle du tick) ne prédit rien (IC ≈ 0).
2. **Après coûts**, seuls les excès extrêmes sur TSLA payent : après |z30| > 2,5, retour moyen +5 pb à 30-60 s (écart
   1,6 pb). Règle retenue : **TSLA, z30 > 2,5, tenue 90 s, objectif 8 pb, stop 16 pb** → 10 jours (28/09-09/10) :
   **+74 pb, 101 trades, +0,73 pb/trade, 61 % gagnants, 7 jours positifs sur 10, t ≈ 0,8** (profil jev) ; +24 pb (opus).
3. **Ce qui ne marche pas** : seuil 2,0 (−235 pb / 7 j) ; QQQ (6 j : ±100 pb par jour, avantage nul) ; SPY (≈ 0) ;
   NVDA (momentum : fader perd −60 à −85 pb/j) ; stops serrés de 4-8 pb (bruit d'1 min sur TSLA ≈ 8 pb) ; horizons
   de 10-40 s (l'écart mange tout) ; crypto chez Alpaca (0,25 % par ordre, pas de short).
4. **Pourquoi la vitesse compte quand même** : à l'entrée, le profil jev capte +0,73 pb/trade contre +0,39 pour opus
   (entrée 3 s plus tard, après le début du retour), et 101 trades contre 61 (les excès ratés pendant qu'Opus réfléchit).

Conclusion honnête (actions) : un petit avantage, fragile d'un jour à l'autre, pas une machine à cash ; le compte est fictif.

## BTC/USD (bougies d'1 s Binance BTCUSDT, 02-11/10/2026, `btc_study.py` et `btc_momentum.py`)
5. **Le bitcoin à la seconde est un marché de momentum** : après |z10| > 2-3, le prix continue de +0,5 à +0,7 pb sur
   1-2 minutes (n = 20 000 à 100 000, erreur type 0,01-0,03). Fader perd sur 100 % des réglages (−90 pb/jour au mieux).
6. **Règle « Rupture »** : z10 > 2,0 → position dans le sens du mouvement, tenue 60 s, objectif 8 pb, stop 2 pb. Avec
   l'écart Coinbase mesuré en direct (0,001 bps médian, 0,17 au p90 ; 0,2 pris) : profil jev **+585 pb/jour, ~920
   trades/jour, +0,60 pb/trade, 50 % gagnants, 10 jours positifs sur 10** ; profil opus +187 pb/jour. À 0,5 bps d'écart :
   jev +308 (88 % de réglages positifs), opus +31 au mieux (6 % positifs). **La vitesse de décision est l'avantage.**
7. Limites : exécution au meilleur prix affiché, sans file d'attente ni frais ; données Binance pour le backtest,
   flux Coinbase en direct ; un compte particulier paie 40 à 60 pb par ordre, 100 fois le gain par trade.
