# Jev Arena · Jev contre Claude Opus 5.5, en direct

Application locale pour la vidéo YouTube sur Jev. Quatre épreuves filmables en 1920x1080 :

| Page | Épreuve |
|---|---|
| http://127.0.0.1:8800/trading | Scalping en direct sur Alpaca paper : deux fonds, bougies d'1 s, positions long et short |
| http://127.0.0.1:8800/tetris | Tetris : mêmes pièces, chute de plus en plus rapide |
| http://127.0.0.1:8800/leads | La course aux 100 leads (Scout, Lightpanda, Crawl4AI) |
| http://127.0.0.1:8800/avis | Le tri d'avis clients en direct, avec la vraie note comme juge |

Jev est toujours **à gauche** et Opus **à droite**. Chaque décision part pour de vrai sur OpenRouter (Jev via l'API
Decisions, Opus 5.5 en effort de raisonnement « low », le plus rapide autorisé). Le coût réel de chaque appel est
noté dans `data/ledger.jsonl`. Chaque session est enregistrée dans `data/sessions/` (pour retracer les vraies
données en motion design ensuite).

## Lancer
Sous Windows : double-clic sur `lancer_arena.bat` : il démarre Docker, l'application (`arena` + `crawl4ai`) et ouvre la page
d'accueil. Pour arrêter : `docker compose down` dans ce dossier.

Pour filmer : Chrome en plein écran (**F11**), puis **Espace** pour lancer ou arrêter une épreuve. Sur la page
trading, **H** affiche ou masque les réglages.

## Trading : ce qu'il faut savoir
- **Réglage de la vidéo (par défaut)** : « BTC · simulé · 24 h/24 », stratégie **Rupture** (suivre une cassure de
  2 σ en 10 s, tenue 60 s, stop −2 pb). Validée sur 10 journées de bougies d'1 s : profil Jev +585 bps/jour, positif
  10 jours sur 10, contre +187 pour le profil Opus ; avec un écart de 0,5 bps Opus devient perdant. Environ un trade par
  minute pour Jev. Chiffres, méthode et limites honnêtes (sans frais de courtage !) dans
  `arena/app/scenes/trading/LISEZMOI.md`.
- « TSLA · Alpaca · séance US » : vrais ordres sur ton compte paper, stratégie Ressort (retour à la moyenne sur excès de
  2,5 σ), pendant la séance de Wall Street. Petit avantage, ≈ 10 entrées par jour : pour un time-lapse, 1 à 2 heures.
- Le flux crypto d'Alpaca est trop maigre pour des bougies d'1 s (32 cotations en 25 s mesurées) : le graphique BTC vient
  de Coinbase. En crypto, Alpaca interdit la vente à découvert et prend 0,25 % par ordre, d'où le courtier simulé
  (exécution au meilleur prix Coinbase, frais réglables).
- Sans clés Alpaca (version publique), seul le courtier simulé est disponible. Voir `README.md` et `.env.example`.
- Un seul compte paper aujourd'hui : les deux fonds y sont séparés par étiquette d'ordre (`jev-…` / `opus-…`), avec
  un suivi de position et de gain propre à chacun. Pour deux comptes vraiment séparés, crée un 2e compte paper sur
  Alpaca, mets ses clés dans `.env` (`ALPACA_OPUS_KEY`, `ALPACA_OPUS_SECRET`), puis `docker compose up -d`.
- Au démarrage et à l'arrêt d'une session, l'application annule les ordres et remet le symbole à plat sur le compte.
- Durée par défaut : 10 minutes, avec arrêt automatique. Plafond de dépense trading : 6 $ (`CAP_TRADING`).
- Stratégies : `provisoire` (prudente, reste souvent à plat), `demo-active` (pour voir les positions à l'écran,
  ignore les frais). La stratégie de **Fable 5.1** se branche en déposant un fichier dans
  `arena/app/scenes/trading/strategies/` : voir `STRATEGIE.md` dans ce dossier (le brief à lui donner).

## Fichiers
- `arena/app/core/` : appels Jev et Opus (`deciders.py`), coûts (`ledger.py`), diffusion en direct (`hub.py`).
- `arena/app/scenes/<épreuve>/` : la logique de chaque épreuve, avec son propre `LISEZMOI.md`.
- `arena/app/static/<épreuve>/` : les pages.
- `.env` : tes clés (OpenRouter, Alpaca, GitHub), jamais publié (voir `.env.example`).
