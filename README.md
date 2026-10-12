# Jev Arena à trois · Jev, Laya et Claude Opus 5.5, en direct

Cette branche (`laya`) ajoute un troisième modèle à l'arène : **Laya**, le modèle de décision open source de Convai.
Jev (TypeSafe), Laya et Claude Opus 5.5 reçoivent exactement les mêmes questions, en direct, et leur coût réel
s'affiche. La version à deux (Jev contre Opus) est sur la branche `main`.

## Lancer la version à trois (5 minutes)

Il te faut :
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows, Mac ou Linux), lancé ;
- [Git](https://git-scm.com/downloads) ;
- une clé OpenRouter : https://openrouter.ai/keys (Jev et Opus sont payés avec, Laya est gratuit).

**Sous Windows (PowerShell)** :

```powershell
git clone -b laya https://github.com/AlanECE/jev-arena.git jev-arena-laya
cd jev-arena-laya
copy .env.example .env
notepad .env
docker compose up -d --build
```

**Sur Mac ou Linux (Terminal)** :

```bash
git clone -b laya https://github.com/AlanECE/jev-arena.git jev-arena-laya
cd jev-arena-laya
cp .env.example .env
nano .env
docker compose up -d --build
```

Dans `.env`, colle ta clé juste après `OPENROUTER_API_KEY=` et enregistre (dans `nano` : Ctrl+O, Entrée, Ctrl+X).
Le premier lancement prend quelques minutes (téléchargement des images Docker).

Ouvre ensuite **http://127.0.0.1:8810**. Rien ne tourne tant que tu n'appuies pas sur le bouton d'une épreuve.

Tu as déjà la version à deux ? Elle utilise le port 8800 : les deux peuvent tourner en même temps.

## Laya : deux façons de l'avoir

### 1. La démo gratuite sur Hugging Face (par défaut)
Tu n'as rien à faire : c'est le réglage de `.env.example` (`LAYA_BACKEND=hosted`). L'arène envoie les questions à
la [démo officielle de Laya](https://huggingface.co/spaces/convaiinnovations/laya-demo). Pas de compte, pas de carte
graphique, rien à télécharger.

À savoir : la démo est publique. Tes questions partent chez Hugging Face, et la file d'attente est partagée avec
les autres visiteurs : une décision prend environ une demi-seconde quand la démo est calme, plusieurs secondes quand
elle est chargée.

### 2. Laya sur ton PC
Laya tourne alors dans un conteneur `laya` à côté de l'arène. C'est gratuit et rien ne sort de ton PC.

Avec une **carte NVIDIA** (série RTX 20 ou plus récente, pilote à jour) : dans `.env`, mets

```
LAYA_BACKEND=local
COMPOSE_PROFILES=laya-local
```

**Sans carte graphique** (ou sur Mac) : mets

```
LAYA_BACKEND=local
COMPOSE_PROFILES=laya-cpu
```

Puis relance `docker compose up -d --build`. Le premier démarrage est long : l'image GPU pèse environ 9 Go (l'image
CPU est bien plus légère), et Laya télécharge ensuite ses modèles (environ 2,3 Go), une seule fois. Sur le processeur,
chaque décision est plus lente que sur la carte graphique.

Tu peux changer de Laya à tout moment sur la page **http://127.0.0.1:8810/laya** (sauf pendant une course) : elle
envoie la même décision à Laya local, à la démo Hugging Face et à Jev, et compare la latence et les réponses.

## Ce qui change par rapport à la version à deux

| Page | Épreuve |
|---|---|
| `/trading` | Trois fonds sur le bitcoin (Jev, Laya, Opus), une bougie par seconde, même stratégie, même courtier simulé |
| `/leads` | La course aux leads à trois : mêmes leads, mêmes questions, accord par paire et à trois, export Excel |
| `/laya` | Laya local contre Laya hébergé (et Jev en référence) ; choix du Laya utilisé dans les duels |
| `/tetris`, `/avis` | Inchangées : Jev contre Opus |

### Export Excel des leads
Après une course, le bouton **« Leads en Excel »** (ou la touche **E**) télécharge un classeur : une feuille
« Résumé » et une feuille par modèle (emails, LinkedIn, GitHub, signaux du site). Les emails n'y sont **pas
masqués** : garde ce fichier pour toi, ne le publie pas.

## Les clés (fichier `.env`)
- **`OPENROUTER_API_KEY`** (obligatoire) : https://openrouter.ai/keys. Jev et Opus sont appelés avec ta clé, au prix
  réel d'OpenRouter : Jev ≈ 0,00002 $ la décision, Opus 5.5 ≈ 0,003 $. Laya coûte 0 $. Une session de trading de
  10 minutes coûte environ 0,30 $, une course de 100 leads environ 1 $.
- **`LAYA_BACKEND`** : `hosted` (démo Hugging Face) ou `local` (voir plus haut).
- **`ALPACA_KEY` / `ALPACA_SECRET`** (facultatif) : un compte **paper** Alpaca (argent fictif, gratuit) pour envoyer de
  vrais ordres. Sans ces clés, le trading tourne sur un **courtier simulé** avec le flux public Coinbase (BTC/USD).
- **`GITHUB_TOKEN`** (facultatif) : accélère la recherche de profils de la course aux leads et la recherche des
  LinkedIn de l'export Excel.

Le fichier `.env` reste sur ton PC : il n'est jamais envoyé sur GitHub (il est dans `.gitignore`).

### Quand ça s'arrête tout seul
- **Crédits OpenRouter épuisés** : la session s'arrête, un bandeau rouge te l'indique ; recharge sur
  https://openrouter.ai/credits.
- **Plafond de dépense atteint** : chaque épreuve a un plafond en dollars (`CAP_*` dans `.env`, cumulés dans
  `data/ledger.jsonl`) ; relève-le ou vide le registre.
- **Laya ne répond pas** : en mode `hosted`, la démo Hugging Face peut être en veille ou saturée (réessaie ou passe en
  local) ; en mode `local`, vérifie que le conteneur tourne (`docker compose ps`) et que `COMPOSE_PROFILES` est réglé.
- **Marché fermé** : la source « Alpaca IEX (actions) » ne fonctionne que du lundi au vendredi, 9 h 30 à 16 h, heure
  de New York. En dehors, choisis « Coinbase » (BTC/USD, 24 h/24).

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

## Police
L'interface est dessinée pour SF Pro Display. Sa licence Apple interdit de la redistribuer : elle n'est pas dans ce
dépôt, et les pages utilisent alors la police de ton système. Voir `arena/app/static/common/fonts/LISEZMOI.md`.

## Arrêter et nettoyer
`docker compose down` arrête tout. `docker compose down --rmi local -v` supprime aussi les images et les modèles de
Laya. Les sessions enregistrées sont dans `data/sessions/`.

Composants tiers et licences : voir `NOTICE.md`. Licence du projet : MIT (`LICENSE`).
