# Scène Tetris : Jev contre Claude Opus 5.5

Deux plateaux côte à côte (Jev à gauche, Opus à droite), **les mêmes pièces dans le même ordre**, la chute qui
accélère. À chaque nouvelle pièce, une vraie requête part tout de suite chez OpenRouter (Jev par l'API Decisions,
Opus en chat completions). Si la réponse arrive après l'atterrissage, la pièce tombe là où elle est apparue
(« DÉCISION RATÉE », pièce grise). Aucune pause pour le modèle lent, aucune réponse simulée.

## Lancer et filmer

1. `docker compose up -d` dans `C:\montage-work\jev-arena`, puis ouvrir **http://127.0.0.1:8800/tetris** dans Chrome.
   Pendant le tournage, ne pas modifier de `.py` (le serveur 8800 se recharge et coupe la course). Pour une instance
   isolée : `MSYS_NO_PATHCONV=1 docker compose exec -d -e ARENA_SCENES=tetris arena uvicorn app.main:app --host 0.0.0.0 --port 8801`
   puis http://127.0.0.1:8801/tetris.
2. **F11** (plein écran). La page est une scène fixe 1920×1080 mise à l'échelle de la fenêtre (même rendu à 125/150 %).
3. La page s'ouvre vierge (plateaux vides, compteurs à zéro, rien d'une course précédente). Elle fait en silence une
   vraie décision de chaque modèle pour ouvrir les connexions (hors de tout compteur, ≈ 0,002 $), puis le bouton
   **« Lancer la course »** s'active (« Jev et Claude Opus 5.5 sont connectés » ; une vraie erreur s'afficherait ici).
4. Lancer l'enregistrement (OBS), puis cliquer **« Lancer la course »** (ou **Espace**). Le curseur se cache pendant la
   course. Les deux premières requêtes partent au même instant.
5. La course s'arrête quand les deux sont morts (ou à `max`). Le tableau « Résultat » s'affiche avec le bouton
   **« Nouvelle course »** (ou **R**) qui recharge une page vierge.

En haut à droite : la graine et le **coût de cette course** seulement (part de 0 à chaque course, préchauffage exclu).

Chaque course est enregistrée : `data/sessions/tetris_course_<date>_g<graine>.json` (stats + journal décision par
décision + chaque pièce posée) et `data/sessions/tetris_<date>.jsonl` (flux d'événements, pour le motion design).

## À l'écran

Par couloir : dernière décision en gros (aller-retour, rouge si elle est arrivée trop tard), médiane, lignes,
pièces posées, décisions reçues, **décisions ratées**, coût réel cumulé, pièges choisis ; la barre « pièce en cours »
(réflexion qui monte contre le temps avant l'atterrissage, « N réponses en retard encore en route ») ; le dernier choix
traduit en français (lignes, trous, hauteur, surface) avec les probabilités calculées de Jev ou la confiance
*déclarée* d'Opus ; un graphique log du temps de chaque décision avec la courbe « chute complète » (la vitesse du jeu)
qui descend : quand les points passent au-dessus, le modèle rate.

## Paramètres d'URL

| paramètre | défaut | effet |
|---|---|---|
| `seed` | 7 | graine de la séquence de pièces (7 et 8 mesurées) |
| `g0` | 280 | ms par ligne de chute au départ (pièce sur plateau vide ≈ 5,3 s) |
| `hold` | 40 | secondes à `g0` avant que la chute accélère (niveau 1) |
| `levelsec` | 5 | durée d'un niveau ensuite |
| `speedup` | 0.87 | facteur par niveau (−13 % de temps par ligne toutes les 5 s) |
| `gmin` | 4 | plancher en ms par ligne |
| `max` | 240 | durée maximale en secondes |
| `auto` | 0 | `1` = départ automatique sans Espace |
| `name` | auto | nom du fichier de course |
| `k` | 6 | nombre d'options proposées (A-F) |
| `inflight` | 8 | requêtes en attente max par couloir (même règle pour les deux ; au-delà : « pas de requête ») |
| `st` | – | `full` = état enrichi (pièce suivante, hauteur, trous) au lieu de la seule pièce |

**Pourquoi cette courbe.** Opus répond en ≈ 2 à 5,6 s (médiane 2,4 à 3,6 s selon la course), Jev en 0,22 à 0,6 s
(médiane ≈ 0,26 s). Pendant 40 s, une pièce met ≈ 4,5 à 5,3 s à tomber : Opus a le temps et joue (≈ 8 pièces,
souvent les mêmes coups que Jev, parfois devant au score). Ensuite la chute raccourcit de 13 % toutes les 5 s :
Opus commence à rater vers 0:50, la tour grise de pièces perdues monte et il meurt vers 1:05-1:10. Jev tient seul
jusqu'à ce que la chute passe sous ≈ 0,5 s (≈ 40 ms par ligne), vers 1:50, puis meurt à son tour.

## Résultats mesurés (12/10, courses réelles en Chrome sans tête, réglages par défaut)

| course | Jev | Claude Opus 5.5 |
|---|---|---|
| graine 8 (1:58) | **22 lignes**, 117,8 s, 72 pièces, 10 ratées (1re à 1:50) | 3 lignes, 67,8 s, 22 pièces, 11 ratées (1re à 0:52) |
| graine 7 (1:53) | **21 lignes**, 112,6 s, 76 pièces, 4 ratées (1re à 1:51) | 4 lignes, 65,7 s, 21 pièces, 12 ratées (1re à 0:15, réponse de 5,6 s) |
| temps de décision médian · p95 | 257-304 ms · 339-771 ms | 2,44-3,62 s · 3,9-4,1 s |
| pièges choisis | 3/68 et 4/74 (≈ 5 %) | 1/22 et 4/21 |
| coût d'une course | ≈ 0,0015 $ (≈ 0,000021 $ la décision) | ≈ 0,05 $ (≈ 0,0022 $ la décision) |

Réglages essayés avant (mêmes mesures) : 400 ms ×0,9/5 s sans palier → course de 2:07 mais Jev seul pendant 70 s ;
280 ms, palier 35 s, ×0,82 → 1:26 (trop court). Coût total des essais de la scène : 0,25 $ (plafond 3 $, cumulé dans
`data/ledger.jsonl` : chaque course filmée coûte ≈ 0,055 $ avec le préchauffage).

## Limites honnêtes

- **Le jeu fait le gros du travail** : il calcule toutes les positions possibles, garde les 6 meilleures (front de
  Pareto lignes / trous / hauteur / relief) complétées par des « pièges » dominés, et envoie leur description en texte.
  Le modèle ne voit pas le plateau : il **choisit une lettre**. Une règle écrite à la main ferait aussi bien (mesuré en
  septembre). Ce qu'on compare ici, c'est surtout **la vitesse de décision**, à qualité de choix voisine.
- « Piège » = option objectivement moins bonne sur les 4 critères qu'une autre option proposée.
- La confiance d'Opus est déclarée par lui dans son JSON, ce n'est pas une probabilité calculée (Jev en renvoie une).
- Une décision arrivée à temps peut quand même échouer si la pièce est déjà descendue sous les colonnes voisines
  (« réponse arrivée, mais la pièce était déjà trop bas ») : c'est la règle du Tetris, la même pour les deux.
- Les latences varient d'une course à l'autre (Opus : médiane 2,4 s une fois, 3,6 s une autre) ; le résultat exact
  change donc à chaque course, l'ordre (Opus meurt d'abord) n'a jamais changé en 5 courses.
- Transport : la page envoie les décisions par le WebSocket `/ws/tetris` (une tâche par décision côté serveur), pas par
  `fetch` : Chrome limite à 6 connexions HTTP par hôte, et les réponses en retard d'Opus auraient fait attendre Jev.
- Préchauffage : une question bidon (« pick A ») déclenchait le **filtre de sécurité d'Anthropic** (refus « cyber
  content », contenu vide). Le préchauffage utilise donc une vraie question Tetris. Si un refus arrive en course, il
  s'affiche comme erreur (« réponse illisible : pas de JSON ») et la pièce tombe.
