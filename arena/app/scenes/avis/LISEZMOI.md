# Scène 04 · « Le tri en direct » (avis clients)

De vrais avis clients sont récupérés **en direct**, puis Jev (gauche) et Claude Opus 5.5 (droite) lisent les
**mêmes avis, dans le même ordre, avec la même concurrence**. Un seul appel par avis et par modèle, avec 4 questions :
note devinée (score 1 à 5 ★), sujet (6 choix), risque de départ (oui/non), réponse urgente (oui/non).
La **vraie note laissée par le client** sert de vérité : la justesse est objective, sans juge.

## Lancer et filmer
- `docker compose up -d` dans `C:\montage-work\jev-arena`, puis **http://127.0.0.1:8800/avis** (plein écran, F11).
- Pour filmer sans risque de rechargement (si quelqu'un modifie un `.py` pendant la prise), lancer une instance dédiée :
  `MSYS_NO_PATHCONV=1 docker compose exec -d -e ARENA_SCENES=avis arena uvicorn app.main:app --host 0.0.0.0 --port 8803`
  puis http://127.0.0.1:8803/avis.
- **Boutons** : « Lancer la course » (= Espace : récupère les avis puis lance), « Charger les avis » (= C : récupère
  seulement, on montre les avis, puis « Lancer la course »), « Nouvelle course » sur le bilan (= R). Autres touches :
  B = afficher / masquer le bilan, H = masquer les réglages. Les boutons sont grisés pendant une course.
- **Plateforme vierge** : toute page fraîchement ouverte part de zéro (colonnes vides, compteurs à 0), même si une course
  a déjà tourné dans le serveur ; seule une course en cours au moment de l'ouverture est affichée telle quelle.
  En haut à droite : **coût de cette course** (Jev + Opus, coût réel renvoyé par OpenRouter), remis à 0 à chaque course.
- Réglages en haut : marque (10 sur Trustpilot, 6 applis sur l'App Store, 6 applis sur Google Play), nombre d'avis (10 à 200),
  concurrence (4, 8 ou 16, **identique pour les deux**). Conseillé : **100 avis, 8 en parallèle** (≈ 35 s, ≈ 0,45 $).
- Déroulé à l'écran : pages lues par Lightpanda (une ligne par page) → 100 cartes d'avis au centre (auteur masqué,
  vraie note cachée « ? ») → chaque modèle fait tomber ses jetons dans 5 colonnes (note devinée), vert = exacte,
  ambre = à 1 ★, rouge = fausse, le chiffre du jeton = vraie note ; pointillés = vraie répartition. La carte centrale
  se retourne et révèle la vraie note quand les deux ont répondu. Bilan automatique à la fin (B pour le masquer).
- Chaque course est enregistrée dans `data/sessions/avis_<date>.jsonl` (pages lues, avis, chaque réponse avec latence
  et coût, bilan) pour retracer les vraies données au montage.

## Source des avis
- **Trustpilot FR lu par Lightpanda** (par défaut) : `fr.trustpilot.com/review/<domaine>?languages=fr&stars=N`.
  Choisi parce que Trustpilot filtre par note : on obtient exactement 20 avis par note pour 100 avis, donc une justesse
  qui veut dire quelque chose. Les avis sont lus dans `__NEXT_DATA__`. Piège : sans le cookie anti-robot AWS-WAF,
  ou avec `page=1` explicite, Trustpilot perd le filtre ; la scène prend d'abord le cookie (ligne « jeton anti-robot »)
  et refait une page si le filtre saute (signalé « filtre perdu » à l'écran). Repli automatique sur Crawl4AI.
  Durée mesurée : 2,3 à 7 s pour 5-6 pages en parallèle selon Trustpilot.
- **App Store FR** : flux RSS JSON public d'Apple (sans compte), 10 pages × 50 avis récents en 0,3 s. Pas de filtre
  par note : on équilibre au mieux dans ces 500 avis (ex. Doctolib n'a que 18 avis à 2 ★ → 18/20/20/20/22).
- **Google Play FR** (ajouté le 12/10) : paquet Python `google-play-scraper` (pur Python, sans compte ni clé, épinglé
  en 1.2.7 dans `requirements.txt`). Il appelle le point d'accès interne du Play Store, celui que la page web utilise
  elle-même (`play.google.com/_/PlayStoreUi/data/batchexecute?hl=fr&gl=fr`), avec un **filtre par note** : un lot par
  note, les 5 en parallèle, donc un échantillon équilibré comme sur Trustpilot. Beaucoup d'avis 5 ★ font un mot
  (« Top », « Parfait ») : chaque lot lit le double du besoin et un lot de plus part pour les notes qui manquent
  (3 lots maximum par note). Durée mesurée : **0,24 s pour les 5 lots** (Doctolib, 20 avis). Les avis Google Play
  n'ont pas de titre : la carte affiche le texte sur 3 lignes, avec la version de l'appli à côté de la date.
  Applis : Doctolib `fr.doctolib.www`, leboncoin `fr.leboncoin`, Vinted `fr.vinted`, Sumeria (ex-Lydia) `com.lydia`,
  Yuka `io.yuka.android`, BlaBlaCar `com.comuto` (identifiants vérifiés avec `app()` le 12/10 ; les paquets Free
  n'existent pas sous ce nom sur Google Play).
- Écartés : les actors Apify de `vendor/API-mega-list` (compte payant).
- Avis de moins de 20 caractères écartés (« Top », « À »). Auteurs masqués (`C***`), emails et numéros masqués.

## Mesures réelles (12/10/2026, concurrence 8 sauf mention)
| course | Jev : temps · exacte · à ±1 ★ · coût | Opus 5.5 : temps · exacte · à ±1 ★ · coût | même sujet |
|---|---|---|---|
| Free, 99 avis Trustpilot | 3,4 s · 45 % · 91 % · 0,0032 $ | 32,7 s · 51 % · 88 % · 0,448 $ | 85 % |
| Back Market, 50 avis | 2,1 s · 64 % · 98 % · 0,0016 $ | 17,6 s · 64 % · 98 % · 0,224 $ | 96 % |
| leboncoin, 30 avis App Store | 1,3 s · 33 % · 87 % · 0,0009 $ | 10,5 s · 37 % · 90 % · 0,128 $ | 73 % |
| Free 10 avis / Vinted 10 avis (conc. 4) | 0,6 s / 1,3 s | 5,3 s / 8,1 s | 100 % / – |
| Doctolib, 20 avis Google Play (conc. 4) | 1,7 s · 55 % · 75 % · 0,0006 $ | 14,1 s · 40 % · 75 % · 0,083 $ | 95 % |

- Latence médiane : Jev 250-450 ms, Opus 2,5-2,7 s. Coût par avis : Jev ≈ 0,000032 $, Opus ≈ 0,0045 $ (**×141**).
  Jev finit **×8 à ×10 plus tôt** à concurrence égale. 0 erreur sur 443 appels.
- Essais : 1,035 $ dépensés au total (8 courses). Une course de 100 avis coûte ≈ 0,45 $ (vérifier le cumul avec
  `curl -s http://127.0.0.1:8800/api/ledger`).

## Limites honnêtes
- Échantillon **équilibré artificiellement** (20 avis par note) ; en vrai, Trustpilot est surtout à 1 ★.
- La note du client n'est pas une vérité parfaite (3 ★ avec un texte furieux, 4 ★ avec des reproches) : les deux
  modèles tournent autour de 50 % de note exacte ; « à une étoile près » est le bon indicateur. Sur 100 avis la marge
  est d'environ ±10 points : l'écart de justesse entre les deux **n'est pas significatif sur une seule course**
  (ça bascule d'une marque à l'autre). Ce qui est stable : la vitesse et le coût.
- Sujet, risque de départ et urgence n'ont pas de vérité terrain : on montre l'accord entre les deux modèles.
- Note de Jev = arrondi de l'espérance du score (même résultat que le niveau le plus probable sur nos essais).
- Temps et coût « pour 10 000 avis » = **extrapolés** (coût moyen réel × 10 000, débit mesuré à la même concurrence).
  Plus de concurrence accélère Opus, mais son coût reste le même.
- R pendant une course annule les appels en vol : OpenRouter peut quand même facturer ces appels Opus
  (≈ 0,005 $ chacun) sans qu'ils apparaissent dans le registre.
- Deux serveurs (8800 et 8803) gardent chacun leur compteur de plafond en mémoire ; la vérité est `data/ledger.jsonl`.
- **Google Play** : point d'accès non documenté par Google, sans garantie. Google peut **limiter le débit** (HTTP 429)
  ou changer le format : l'erreur s'affiche alors telle quelle dans le journal de récupération (« échec : … ») et la
  course s'arrête sur « aucun avis récupéré ». Le filtre par note est vérifié avis par avis (0 écart sur 300 avis
  testés) ; s'il sautait, l'avis est gardé avec sa vraie note et la ligne est marquée « filtre perdu ». Les avis
  5 ★ étant souvent d'un mot, le lot de 5 ★ peut rester sous le quota (l'équilibrage comble avec les autres notes).
  Un paquet absent ou renommé sur le Play Store (ex. Lydia devenu Sumeria) donne « NotFoundError ».
- Une page fraîchement ouverte sur un serveur en état « terminé » le remet à zéro (règle de la plateforme vierge) :
  pour relire un bilan, utiliser `data/sessions/avis_<date>.jsonl`, pas une nouvelle page.

## Fichiers
`app/scenes/avis/__init__.py` (questions, course, bilan, API `/api/avis/presets|state|start|reset`),
`app/scenes/avis/sources.py` (Trustpilot/Lightpanda, App Store RSS, Google Play via google-play-scraper, équilibrage),
`app/static/avis/index.html`, `avis.css`, `avis.js` (page 1920×1080, mise à l'échelle si l'écran diffère).
