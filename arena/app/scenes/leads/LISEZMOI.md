# La course aux 100 leads · Jev contre Claude Opus 5.5

Page : http://127.0.0.1:8800/leads (1920x1080, plein écran, sans défilement). Tout est réel : vrais profils GitHub,
vraies pages lues par Lightpanda (Crawl4AI en secours), vraies décisions via OpenRouter, coûts réels renvoyés par
OpenRouter. Rien n'est envoyé à personne ; aucune vérification SMTP.

## Filmer

1. Ouvrir la page : elle est **toujours vierge** (compteurs à 0, listes vides, coût de la course à 0 $), même si une
   course a déjà tourné dans le même serveur. Seule une course *en cours* est renvoyée à une page qui se (re)connecte.
2. Choisir l'objectif (5 / 10 / 25 / 50 / 100 leads) et le parallélisme (4 / 8 / 12 / 16, identique pour les deux
   modèles) en haut à droite, ou par l'URL : `/leads?target=100&conc=8`.
3. **« Lancer la course »** (ou **Espace**). Jev à gauche (rose), Opus à droite (argile), pipeline partagé au centre.
4. À la fin, le bilan s'affiche tout seul (temps, emails, décisions, coût, accord, désaccords). **« Nouvelle course »**
   (ou **R**) remet la page à blanc. **F** masque ou réaffiche le bilan, **Échap** le ferme.
5. **M** démasque les emails, domaines et noms (vérification privée d'Alan) : ne jamais publier une prise démasquée.
   Par défaut tout est masqué (`h***@p***.com`, `po***.consulting`).

Chaque côté affiche en permanence : leads jugés / objectif (barre), temps, décisions, latence médiane, coût réel de la
course (pas de cumul), leads à contacter, file d'attente, erreurs ; la **liste d'emails qui s'incrémente** (compteur
d'adresses uniques + chaque nouvelle adresse qui glisse en haut, avec sa source : profil GitHub, page d'accueil ou page
choisie par le modèle) ; la liste des leads jugés (catégorie, lien ouvert, verdict OUI / NON avec sa probabilité,
priorité 0-3, latence). Les désaccords entre les deux modèles sont surlignés en violet.

## Ce qui se passe vraiment

Source partagée : recherche GitHub (fondateurs, indie makers, startups, TPE / PME qui construisent une appli ; 30
requêtes/min avec jeton) → scraper GitHub de **Scout** (`vendor/Scout`, code non modifié) sur chaque profil → page
d'accueil lue **une seule fois** par Lightpanda (Crawl4AI si Lightpanda échoue). Chaque lead prêt est poussé **au même
instant** dans les deux files, vidées par le même nombre d'ouvriers.

Pour chaque lead et chaque modèle, 2 décisions avec **le même état et les mêmes questions** (`questions.py`) :
1. `contact` (choice) : quel lien interne ouvrir pour trouver un email (ou aucun) → la page choisie est lue par le
   lecteur partagé (si les deux choisissent la même, une seule visite) ;
2. qualification pour l'offre d'Alan (motion design pour l'appli d'une TPE / PME, d'un indie maker ou d'une startup) :
   `fit` (noul → verdict OUI / NON), `stage` (choice : pré-lancement, vient de sortir, PME avec produit, grande
   entreprise, agence / service, pas de produit, flou), `has_video` (noul), `priority` (score 0-3).

Emails : regex de Scout + mailto + décodage Cloudflare, sur le profil GitHub, la page d'accueil et la page choisie ;
on ne garde que les adresses du domaine du site ou d'une boîte perso. Opus répond en JSON aux mêmes questions ; sa
confiance est déclarée (pas une probabilité calculée comme celle de Jev).

Les profils déjà traités sont mémorisés dans `/data/leads/seen.json` : chaque course prend de **nouveaux** leads.
`POST /api/leads/forget` les oublie. Chaque course est enregistrée dans `/data/sessions/leads_<date>.jsonl` (tous
les événements, dont l'état envoyé aux modèles et leurs réponses) et son bilan dans `GET /api/leads/summary`.

## Mesures réelles (12/10/2026, 8 en parallèle de chaque côté, questions finales)

| | 10 leads | 25 leads |
|---|---|---|
| Source épuisée (GitHub + Scout + pages d'accueil) | ≈ 2 s | 4,4 s |
| **Jev** : temps pour tous les leads | **4,8 s** | **6,5 s** |
| **Opus** : temps pour tous les leads | 12,5 s | 22,0 s |
| Décisions (chaque côté) | 20 | 44 |
| Latence médiane d'une décision | 308 ms / 2,9 s | 309 ms / 2,7 s |
| Coût réel Jev / Opus | 0,0008 $ / 0,0935 $ | 0,0019 $ / 0,2314 $ |
| Emails uniques Jev / Opus | 12 / 12 | 27 / 28 (24 leads sur 25 avec email) |
| Accord sur le verdict OUI / NON | 100 % | 88 % (catégorie 76 %, même lien 63 %) |
| Rapport temps / coût | ×2,6 / ×118 | ×3,4 / ×123 |

Course de 50 (même pipeline, questions précédentes, 11/10) : source épuisée en 8 s, Jev 9,3 s, Opus 35,3 s
(0,0034 $ contre 0,44 $), 41 et 42 leads avec email, accord 90 %. Pour 100 leads, extrapolation : Jev ≈ 20 s,
Opus ≈ 70 s, ≈ 0,9 $ d'Opus par course (plafond de la scène : 6 $, `CAP_LEADS` dans `.env`).

Opus 5.5 coûte ≈ 0,006 $ par qualification (état ≈ 1 400 jetons) : avec un objectif de 100, prévoir ≈ 1 $ par prise.

## Limites honnêtes

- **RGPD / CNIL** : uniquement des adresses publiées par les personnes elles-mêmes (profil GitHub public, page
  d'accueil, contact, mentions légales). Les boîtes gmail / proton de freelances restent des données personnelles.
  Masquage par défaut à l'écran, personne n'est contacté par l'arène. Si Alan démarche ensuite, c'est à lui d'informer
  de la source et de proposer un désabonnement.
- **Le web est le goulot** : Jev finit quelques secondes après la dernière page lue ; ses décisions (0,3 s) ne sont
  jamais ce qu'on attend. Opus, lui, fait attendre sa file (2,5-3 s par décision, 2 par lead).
- **GitHub** : 30 recherches/min et 1 000 résultats max par requête (28 requêtes en tourniquet). Si la limite est
  atteinte, la pause s'affiche dans la case Scout · GitHub. GitHub est une source de *développeurs* : les « TPE / PME »
  trouvées sont surtout des fondateurs d'applis et de SaaS, rarement des commerces classiques.
- **Pages** : ≈ 5-10 % d'accueils illisibles (Cloudflare, délai > 9 s, DNS) ; les échecs s'affichent dans « Le web en
  direct » et dans le compteur d'échecs. Crawl4AI ne rattrape qu'une partie.
- **Accord ≠ vérité** : Opus n'est pas la vérité terrain ; les désaccords sont montrés tels quels. Avec 10-25 leads,
  ± 10 points sur les taux d'accord.
- **Erreurs réelles** affichées honnêtement : une décision en erreur (OpenRouter 5xx, JSON illisible) donne une ligne
  « erreur » grise ; le plafond de dépense arrête la course proprement (bandeau noir).
- Reproductibilité : chaque course prend de nouveaux profils, les leads changent à chaque prise.

## Réglages et fichiers

- `app/scenes/leads/questions.py` : offre, questions, état envoyé aux modèles (identique pour les deux).
- `app/scenes/leads/scout.py` : requêtes GitHub (`QUERIES`), chargement de Scout, mémoire des profils vus.
- `app/scenes/leads/web.py` : Lightpanda (`LP_TIMEOUT_S` 9 s), Crawl4AI, résumé de page, emails.
- `app/scenes/leads/race.py` : la course (files, ouvriers, accord, bilan). `PROFILE_WORKERS` 8, `HOME_WORKERS` 16.
- Page : `app/static/leads/` (index.html, leads.css, leads.js).
- Instance de test isolée (port 8802, sans rechargement) : voir `ARENA_DEV.md`.
