# Scène 01 · Scalping en direct (Jev contre Claude Opus 5.5)

Deux fonds, **même stratégie, mêmes données, même compte Alpaca paper**. À chaque bougie d'une seconde, chaque fonds
à plat demande à son modèle d'entrer long, short ou de rester à plat. Les sorties sont mécaniques et identiques
(tenue fixe, objectif, stop). Jev répond en ≈ 0,3 s et décide à chaque bougie ; Opus 5.5 répond en 2 à 5 s et rate
donc 2 bougies sur 3. Tout est réel : flux de prix, décisions OpenRouter, ordres et exécutions Alpaca.

## Lancer et filmer
- http://127.0.0.1:8800/trading, plein écran (F11). La page est **vierge** : rien ne tourne avant **« Lancer le test »**
  (ou Espace). **H** masque les réglages. « Nouveau test » recharge une page vierge.
- Réglages (barre du haut) : source de prix, symbole, bougies (1 s par défaut), stratégie, seuil d'entrée, durée
  (arrêt automatique), taille de chaque position (100 000 $ par défaut sur actions : à 3 pb de gain par trade, ça fait
  30 $ visibles à l'écran ; le compte paper a 100 000 $ et 4× de levier).
- **Préréglage de la vidéo : « BTC · simulé · 24 h/24 »** (par défaut) : prix Coinbase BTC-USD à la seconde,
  **courtier simulé** (exécution au meilleur prix affiché, long et short, frais réglables, 0 par défaut), stratégie
  **Rupture**. Marche à toute heure, environ un trade par minute pour Jev.
- **« TSLA · Alpaca · séance US »** : de vrais ordres sur ton compte Alpaca paper, stratégie Ressort, pendant la séance
  de Wall Street (15 h 30-22 h à Paris, 14 h 30-21 h du 25/10 au 1er/11). Long et short, commission 0, écart ≈ 1,6 pb.
  Peu de trades (≈ 10 par jour).
- **« BTC · Alpaca · 0,25 %/ordre »** : vrais ordres crypto sur Alpaca paper ; Alpaca interdit le short en crypto et
  prend 0,25 % par ordre : aucune stratégie ne couvre ces frais, c'est pour montrer que les ordres partent, pas pour gagner.
- À l'écran : bougies 1 s, **EMA 20** (violet), **VWAP** (orange pointillé), **jauge d'étirement** en bas (σ, rouge
  au-dessus de 0, vert en dessous, saturée au-delà du seuil), flèches LONG / SHORT à l'entrée, pastille du gain à la
  sortie, lignes entrée / objectif / stop, compte à rebours de sortie, bande des décisions (une case par bougie :
  long, short, à plat, réfléchit, ratée, position tenue), compteurs et coût réel.
- Chaque session est enregistrée dans `data/sessions/trading_<date>.jsonl` (bougies, décisions, ordres, exécutions,
  trades) pour retracer les vraies données au montage.

## La stratégie « Rupture » · BTC/USD (celle de la vidéo, 24 h/24)
Préréglage « BTC · simulé · 24 h/24 ». Étude sur **10 journées** de bougies d'1 s (Binance BTCUSDT, 02-11/10/2026,
`research/btc_study.py` et `btc_momentum.py`), écart Coinbase mesuré en direct (0,001 bps médian, 0,17 bps au p90).

- **Le bitcoin à la seconde est un marché de momentum** : après un excès de plus de 2 σ en 10 s, le prix *continue*
  de +0,5 à +0,7 bps sur 1-2 minutes (dizaines de milliers d'événements, très significatif). Fader (comme sur TSLA)
  perd sur 100 % des réglages.
- **Règle retenue** : étirement des 10 dernières secondes au-delà de **2,0 σ** → position **dans le sens** du
  mouvement, tenue **60 s**, objectif +8 pb, **stop −2 pb**.
- **Résultat (écart 0,2 bps, sans frais)** : profil Jev **+585 bps par jour, ~920 trades/jour, +0,60 pb par trade,
  50 % de gagnants, 10 jours positifs sur 10** (de +156 à +996). Profil Opus (décision toutes les 4 s, 3 s de retard) :
  +187 bps/jour. Avec un écart de 0,5 bps : Jev +308, **Opus devient perdant** (6 % de réglages positifs).
  **L'avantage vit dans les premières secondes après la cassure : c'est la vitesse de décision qui fait la différence.**
- **Ce que ça veut dire honnêtement** : c'est un backtest avec exécution au meilleur prix affiché, sans frais de
  courtage. Un compte Coinbase classique paie 0,4 à 0,6 % par ordre, soit 100 fois le gain par trade : cette stratégie
  n'est rentable qu'à frais quasi nuls (teneur de marché, compte institutionnel). Le réglage « frais » de la barre
  permet de le montrer à l'écran (à 5 pb par ordre, tout disparaît). Alpaca crypto (0,25 %/ordre, pas de short) est hors
  jeu : d'où le courtier simulé.
- À l'écran : les bandes rouge / verte sont le seuil traduit en prix (au-dessus de la rouge → LONG, en dessous de la
  verte → SHORT), la jauge du bas est l'étirement en σ.

## La stratégie « Ressort » · TSLA (retour à la moyenne sur excès court, actions US)
Étude du 12/10/2026 dans `research/` (données SIP Alpaca à la seconde, exécutions réalistes : premier échange de la
seconde suivante ± demi-écart NBBO mesuré ; deux profils de latence : Jev = décision chaque seconde, exécution dans la
seconde ; Opus = décision toutes les 4 s, exécution 3 s plus tard).

- **Ce qui est vrai** : à 5-60 s, le prix revient en sens inverse d'un mouvement récent (corrélation de rang −0,05 à
  −0,13 sur SPY, TSLA, NVDA). Le flux d'ordres par règle du tick ne prédit rien.
- **Ce qui marche après coûts** : seulement les **excès extrêmes sur TSLA**. Règle retenue : étirement des 30 dernières
  secondes au-delà de **2,5 σ** (normalisé par la volatilité 1 s sur 60 s) → position inverse, **tenue 90 s**,
  objectif **+8 pb**, stop **−16 pb**.
  Résultat sur **10 journées** (28/09 → 09/10) : profil Jev **+74 pb en tout, 101 trades, +0,73 pb par trade, 61 % de
  gagnants, 7 jours positifs sur 10** (meilleur jour +59, pire −60) ; profil Opus +24 pb sur 61 trades. Sur 100 000 $,
  cela fait environ **+7 $ par jour**. C'est un avantage petit et fragile (t ≈ 0,8), pas une machine à cash.
- **Ce qui ne marche pas** : seuil 2,0 σ (−235 pb sur 7 jours : plus de trades, mais le bruit paie l'écart) ; QQQ et
  SPY (avantage nul, l'écart est petit mais les mouvements aussi) ; NVDA (titre à momentum : fader perd) ; les stops
  serrés de 4-8 pb (plus petits que le bruit normal de TSLA sur une minute, ≈ 8 pb) ; les frais crypto d'Alpaca.
- **Conséquence pour la vidéo** : à 2,5 σ il y a environ **10 entrées par jour** (une toutes les 40 min) ; une session
  de 10 minutes peut ne contenir aucun trade. Pour un time-lapse, laisse tourner **1 à 2 heures**. Le sélecteur
  « seuil 2,0 σ · plus de trades » donne 3 fois plus d'entrées mais l'avantage n'est plus prouvé : à dire à l'écran.
- Le rôle du modèle : il reçoit les étirements mesurés (10, 30, 60 s), le seuil, l'écart au VWAP, la position dans la
  fourchette de 5 min, le volume, l'écart achat-vente, et choisit. Jev renvoie une probabilité calculée ; Opus une
  confiance déclarée. Les deux reçoivent exactement le même état et les mêmes questions (`strategies/reversion.py`).

Pour écrire une autre stratégie (Fable 5.1 ou toi) : `strategies/STRATEGIE.md`, puis un fichier dans `strategies/`.

## Pièges connus
- Alpaca n'accepte **qu'une connexion** au flux IEX par compte : un seul serveur à la fois sur les actions (ne pas ouvrir
  une deuxième instance en source « Alpaca IEX »). Plusieurs onglets sur le même serveur, en revanche, ça marche.
- Le flux IEX ne voit qu'une partie des échanges : certaines secondes n'ont pas de trade, la bougie est alors plate.
- Opus peut mettre jusqu'à 10 s sur une décision : pendant ce temps, les bougies sont comptées « ratées ».

## Honnêteté
- Un compte paper n'a ni slippage ni impact de marché : les exécutions réelles seraient un peu moins bonnes.
- Les ordres partent au marché : on paie l'écart à l'aller et au retour.
- Les deux fonds partagent un compte (séparés par étiquette d'ordre) : un passage de long à short est coupé en deux
  ordres. Avec un deuxième compte paper (`ALPACA_OPUS_KEY/SECRET`), chacun a le sien.
- Le flux IEX gratuit ne voit qu'une partie des échanges ; les exécutions Alpaca se font au NBBO. La ligne violette
  « Alpaca » n'apparaît qu'en crypto (source Coinbase), où le prix d'exécution diffère du graphique.
- Ce n'est pas un conseil d'investissement.
