# Jev Arena · Jev contre Claude Opus 5.5, en direct

Quatre épreuves où **Jev** (le modèle de décision de TypeSafe) affronte **Claude Opus 5.5** en direct, avec de vraies
requêtes, de vraies pages et de vrais ordres (compte fictif). Jev est à gauche, Opus à droite. Chaque décision part
pour de vrai sur OpenRouter et son coût réel s'affiche.

| Page | Épreuve |
|---|---|
| `/trading` | Scalping sur bougies d'1 seconde : deux fonds, même stratégie, même compte, une décision par bougie |
| `/tetris` | Tetris : mêmes pièces, chute de plus en plus rapide ; une réponse en retard et la pièce tombe n'importe où |
| `/leads` | La course aux leads : Scout + Lightpanda + Crawl4AI trouvent de vrais sites, chaque modèle qualifie et choisit où chercher l'email |
| `/avis` | Le tri d'avis clients (Trustpilot, App Store, Google Play) : la vraie note du client sert de juge |

## Installer chez toi (5 minutes)

Il te faut [Docker Desktop](https://www.docker.com/products/docker-desktop/) et une clé OpenRouter.

```bash
git clone <ce dépôt> jev-arena
cd jev-arena
cp .env.example .env        # puis ouvre .env et colle ta clé OPENROUTER_API_KEY
docker compose up -d --build
```

Ouvre ensuite http://127.0.0.1:8800. Rien ne tourne tant que tu n'appuies pas sur le bouton de chaque épreuve.

### Les clés
- **`OPENROUTER_API_KEY`** (obligatoire) : https://openrouter.ai/keys. Les deux modèles sont appelés avec ta clé,
  au prix réel d'OpenRouter : Jev ≈ 0,00002 $ la décision, Opus 5.5 ≈ 0,003 $. Une session de trading de 10 minutes
  coûte environ 0,30 $, une course de 100 leads environ 1 $, une partie de Tetris environ 0,05 $.
- **`ALPACA_KEY` / `ALPACA_SECRET`** (facultatif) : un compte **paper** Alpaca (argent fictif, gratuit) pour envoyer de
  vrais ordres et trader des actions américaines (TSLA, QQQ) pendant la séance de Wall Street. Sans ces clés, le
  trading tourne sur un **courtier simulé** avec le flux public Coinbase (BTC/USD), long et short autorisés.
- **`GITHUB_TOKEN`** (facultatif) : accélère la recherche de profils de la course aux leads.

### Quand ça s'arrête tout seul
- **Crédits OpenRouter épuisés** : la session s'arrête, un bandeau rouge te l'indique ; recharge sur
  https://openrouter.ai/credits.
- **Plafond de dépense atteint** : chaque épreuve a un plafond en dollars (`CAP_*` dans `.env`, cumulés dans
  `data/ledger.jsonl`) ; relève-le ou vide le registre.
- **Marché fermé** : la source « Alpaca IEX (actions) » ne fonctionne que du lundi au vendredi, 9 h 30 à 16 h, heure
  de New York. En dehors, choisis « Coinbase » (BTC/USD, 24 h/24).
- **Ordre refusé par Alpaca** : affiché tel quel (pouvoir d'achat insuffisant, titre non vendable à découvert…).

## Ce qu'il faut savoir sur le trading
Deux stratégies, issues d'une étude sur dix journées de bougies d'une seconde (dossier `research/`) :
- **Rupture** (BTC/USD, par défaut) : le bitcoin à la seconde est un marché de momentum ; après une cassure de plus de
  2 σ en 10 s, le prix continue pendant 1 à 2 minutes. Le profil « décision à chaque seconde » (Jev) capte +0,6 point de
  base par trade, 10 jours positifs sur 10 ; le profil « 3 secondes de retard » (Opus) en capte trois fois moins, et
  devient perdant dès que l'écart achat-vente grandit. C'est **sans frais de courtage** : avec les commissions d'un compte
  particulier (0,4 à 0,6 % par ordre), cette stratégie perd. Le réglage « frais » permet de le voir.
- **Ressort** (TSLA, séance US) : retour à la moyenne après un excès de 2,5 σ en 30 s ; petit avantage, ≈ 10 trades
  par jour.

Les sorties sont mécaniques (tenue fixe, objectif, stop). Les résultats mesurés et les limites sont dans
`arena/app/scenes/trading/LISEZMOI.md`. **Ce n'est pas un conseil d'investissement** : le compte est fictif ou simulé.

## Arrêter et nettoyer
`docker compose down` arrête tout. `docker compose down --rmi local -v` supprime aussi les images. Les sessions
enregistrées sont dans `data/sessions/`.
