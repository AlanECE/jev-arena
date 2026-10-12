"""Sources d'avis clients, recuperes EN DIRECT a chaque course (rien n'est mis en cache entre deux courses).

1. Trustpilot FR, lu par Lightpanda (navigateur sans tete, binaire dans le conteneur) :
   https://fr.trustpilot.com/review/<domaine>?languages=fr&stars=N[&page=P]
   Les avis sont dans <script id="__NEXT_DATA__">. Trustpilot passe par un challenge AWS-WAF : sans le cookie
   `aws-waf-token`, la page se recharge et perd les filtres (on recoit alors des avis de toutes les notes).
   On recupere donc d'abord le cookie (une page sans filtre, --cookie-jar), puis toutes les pages filtrees
   par etoile en parallele (--cookie). Piege : `page=1` explicite provoque une redirection qui perd aussi les
   filtres, on ne met le parametre page qu'a partir de 2. Repli : Crawl4AI (Chromium) si Lightpanda echoue.
2. App Store FR : flux RSS JSON public d'Apple (sans compte, sans cle), 10 pages de 50 avis max :
   https://itunes.apple.com/fr/rss/customerreviews/page=P/id=APP/sortby=mostrecent/json
   Pas de filtre par note : on equilibre apres coup dans les 500 avis les plus recents.
3. Google Play FR : paquet `google-play-scraper` (pur Python, sans compte ni cle). Il appelle le point d'acces
   interne du Play Store (celui que la page web elle-meme utilise), filtre par note et par langue :
   https://play.google.com/_/PlayStoreUi/data/batchexecute?hl=fr&gl=fr  (corps = requete `UsvDTd`)
   Un lot par note, en parallele (5 fils) ; beaucoup d'avis 5 etoiles font un mot, donc on lit le double du
   besoin puis un lot de plus pour les notes qui manquent. Google peut limiter le debit (HTTP 429) : l'erreur
   est alors affichee telle quelle dans le journal de recuperation.

La note donnee par le client (1 a 5 etoiles) est la verite terrain.
"""
import asyncio
import json
import os
import random
import re
import time
from pathlib import Path

import httpx

try:  # pur Python ; s'il manque (image non reconstruite), la source Google Play l'annonce honnetement
    from google_play_scraper import Sort as GPSort, reviews as gp_reviews
except ImportError:  # pragma: no cover
    GPSort, gp_reviews = None, None

from ...core.config import CRAWL4AI_TOKEN, CRAWL4AI_URL

COOKIE = Path("/tmp/avis_trustpilot_cookie.json")
COOKIE_MAX_AGE = 20 * 60
MIN_TEXT = 20     # on ecarte les avis vides ou d'un mot (« À », « Top ») : rien a lire pour personne
_cookie_lock = asyncio.Lock()

PRESETS = [
    # Trustpilot FR (Lightpanda) : grandes marques avec assez d'avis FR pour chaque note (releve du 12/10/2026)
    {"id": "tp-free", "source": "trustpilot", "label": "Free", "domain": "www.free.fr", "sub": "Opérateur télécom"},
    {"id": "tp-vinted", "source": "trustpilot", "label": "Vinted", "domain": "www.vinted.fr", "sub": "Seconde main"},
    {"id": "tp-amazon", "source": "trustpilot", "label": "Amazon", "domain": "www.amazon.fr", "sub": "E-commerce"},
    {"id": "tp-backmarket", "source": "trustpilot", "label": "Back Market", "domain": "www.backmarket.fr",
     "sub": "Reconditionné"},
    {"id": "tp-boursobank", "source": "trustpilot", "label": "BoursoBank", "domain": "www.boursobank.com",
     "sub": "Banque en ligne"},
    {"id": "tp-leboncoin", "source": "trustpilot", "label": "leboncoin", "domain": "www.leboncoin.fr",
     "sub": "Petites annonces"},
    {"id": "tp-fnac", "source": "trustpilot", "label": "Fnac", "domain": "www.fnac.com", "sub": "Culture et tech"},
    {"id": "tp-edf", "source": "trustpilot", "label": "EDF", "domain": "www.edf.fr", "sub": "Énergie"},
    {"id": "tp-doctolib", "source": "trustpilot", "label": "Doctolib", "domain": "www.doctolib.fr", "sub": "Santé"},
    {"id": "tp-orange", "source": "trustpilot", "label": "Orange", "domain": "www.orange.fr", "sub": "Opérateur télécom"},
    # App Store FR (flux RSS public d'Apple)
    {"id": "as-leboncoin", "source": "appstore", "label": "leboncoin", "app_id": 484115113, "sub": "Appli iPhone"},
    {"id": "as-duolingo", "source": "appstore", "label": "Duolingo", "app_id": 570060128, "sub": "Appli iPhone"},
    {"id": "as-bereal", "source": "appstore", "label": "BeReal", "app_id": 1459645446, "sub": "Appli iPhone"},
    {"id": "as-doctolib", "source": "appstore", "label": "Doctolib", "app_id": 925339063, "sub": "Appli iPhone"},
    {"id": "as-free", "source": "appstore", "label": "Free", "app_id": 1643534870, "sub": "Appli iPhone"},
    {"id": "as-uber-eats", "source": "appstore", "label": "Uber Eats", "app_id": 1058959277, "sub": "Appli iPhone"},
    # Google Play FR (google-play-scraper) : identifiants de paquet verifies avec app() le 12/10/2026
    {"id": "gp-doctolib", "source": "googleplay", "label": "Doctolib", "package": "fr.doctolib.www",
     "sub": "Appli Android"},
    {"id": "gp-leboncoin", "source": "googleplay", "label": "leboncoin", "package": "fr.leboncoin",
     "sub": "Appli Android"},
    {"id": "gp-vinted", "source": "googleplay", "label": "Vinted", "package": "fr.vinted", "sub": "Appli Android"},
    {"id": "gp-sumeria", "source": "googleplay", "label": "Sumeria (ex-Lydia)", "package": "com.lydia",
     "sub": "Appli Android"},
    {"id": "gp-yuka", "source": "googleplay", "label": "Yuka", "package": "io.yuka.android", "sub": "Appli Android"},
    {"id": "gp-blablacar", "source": "googleplay", "label": "BlaBlaCar", "package": "com.comuto",
     "sub": "Appli Android"},
]
PRESET_BY_ID = {p["id"]: p for p in PRESETS}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}(?!\d)")
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def clean_text(s: str) -> str:
    s = (s or "").replace("\r", "")
    s = _EMAIL.sub("[email masqué]", s)
    s = _PHONE.sub("[numéro masqué]", s)
    return s.strip()


def mask_name(name: str) -> str:
    name = (name or "").strip()
    return (name[:1].upper() + "***") if name else "anonyme"


# ---------------------------------------------------------------- Trustpilot (Lightpanda, repli Crawl4AI)

def tp_url(domain: str, stars: int | None = None, page: int = 1) -> str:
    u = f"https://fr.trustpilot.com/review/{domain}?languages=fr"
    if stars:
        u += f"&stars={stars}"
    if page > 1:
        u += f"&page={page}"
    return u


def tp_parse(html: str):
    """-> (reviews, selected_stars, total_count) ou None si la page n'a pas de __NEXT_DATA__."""
    m = _NEXT.search(html or "")
    if not m:
        return None
    try:
        pp = json.loads(m.group(1))["props"]["pageProps"]
    except Exception:
        return None
    f = pp.get("filters") or {}
    sel = (f.get("selected") or {}).get("stars")
    total = (f.get("pagination") or {}).get("totalCount")
    bu = (pp.get("businessUnit") or {}).get("displayName")
    out = []
    for r in pp.get("reviews") or []:
        rating = r.get("rating")
        text = clean_text(r.get("text") or "")
        title = clean_text(r.get("title") or "")
        if not rating or len(text) < MIN_TEXT:
            continue
        if r.get("language") and r.get("language") != "fr":
            continue
        out.append({"id": "tp-" + str(r.get("id")), "stars": int(rating), "title": title, "text": text,
                    "author": mask_name((r.get("consumer") or {}).get("displayName")),
                    "date": ((r.get("dates") or {}).get("publishedDate") or "")[:10], "brand_page": bu})
    return out, sel, total


async def _run(args: list[str], timeout: float = 45.0) -> tuple[int, bytes, bytes]:
    pr = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(pr.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        pr.kill()
        await pr.wait()
        return -9, b"", b"timeout"
    return pr.returncode, out, err


async def lightpanda_html(url: str, cookie: bool = True, save_cookie: bool = False) -> tuple[str, int]:
    args = ["lightpanda", "fetch", "--log-level", "err"]
    if cookie and COOKIE.exists():
        args += ["--cookie", str(COOKIE)]
    if save_cookie:
        args += ["--cookie-jar", str(COOKIE)]
    args += ["--wait-selector", "script#__NEXT_DATA__", "--wait-ms", "25000", "--dump", "html", url]
    rc, out, _ = await _run(args)
    return out.decode("utf-8", "replace"), rc


async def refresh_cookie(domain: str, force: bool = False) -> dict:
    """Passe le challenge AWS-WAF une fois et garde le cookie pour les pages filtrees."""
    async with _cookie_lock:
        if not force and COOKIE.exists() and time.time() - COOKIE.stat().st_mtime < COOKIE_MAX_AGE:
            return {"refreshed": False}
        t = time.time()
        html, rc = await lightpanda_html(tp_url(domain), cookie=False, save_cookie=True)
        return {"refreshed": True, "ok": COOKIE.exists() and tp_parse(html) is not None,
                "ms": round((time.time() - t) * 1000)}


async def crawl4ai_html(url: str) -> str:
    h = {"Authorization": f"Bearer {CRAWL4AI_TOKEN}"} if CRAWL4AI_TOKEN else {}
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(f"{CRAWL4AI_URL}/crawl", headers=h, json={"urls": [url], "crawler_config": {
            "type": "CrawlerRunConfig", "params": {"wait_for": "css:article", "page_timeout": 30000,
                                                   "cache_mode": "bypass"}}})
        return ((r.json().get("results") or [{}])[0].get("html")) or ""


async def fetch_trustpilot(preset: dict, target: int, on_event) -> list[dict]:
    domain = preset["domain"]
    per_star = -(-target // 5)
    pages = -(-per_star // 20)
    ck = await refresh_cookie(domain)
    if ck.get("refreshed"):
        await on_event({"tool": "lightpanda", "kind": "cookie", "label": "jeton anti-robot (AWS WAF)",
                        "url": tp_url(domain), "ok": ck.get("ok"), "ms": ck.get("ms"), "n": 0})
    sem = asyncio.Semaphore(6)

    async def one(star: int, page: int):
        url = tp_url(domain, star, page)
        async with sem:
            t = time.time()
            note, tool = "", "lightpanda"
            html, rc = await lightpanda_html(url)
            parsed = tp_parse(html)
            if parsed and parsed[1] != [star]:
                # le filtre a saute (cookie perime) : nouveau jeton puis un seul nouvel essai
                await refresh_cookie(domain, force=True)
                html, rc = await lightpanda_html(url)
                parsed = tp_parse(html) or parsed
                note = "nouveau jeton"
            if parsed is None:
                tool = "crawl4ai"
                try:
                    parsed = tp_parse(await crawl4ai_html(url))
                    note = "repli Crawl4AI"
                except Exception as e:
                    note = f"échec : {str(e)[:80]}"
            ms = round((time.time() - t) * 1000)
        revs = parsed[0] if parsed else []
        filtered = bool(parsed and parsed[1] == [star])
        if parsed and not filtered:
            note = (note + " · " if note else "") + "filtre perdu (toutes notes)"
        await on_event({"tool": tool, "kind": "page", "label": f"{star} ★ · page {page}", "url": url, "star": star,
                        "page": page, "n": len(revs), "ms": ms, "ok": bool(revs), "note": note,
                        "total": parsed[2] if parsed else None})
        return revs

    res = await asyncio.gather(*(one(s, p) for s in range(1, 6) for p in range(1, pages + 1)))
    pool, seen = [], set()

    def add(batches):
        for revs in batches:
            for r in revs:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    pool.append(r)

    add(res)
    # quelques avis ecartes (trop courts) : une page de plus seulement pour les notes qui manquent
    short = [s for s in range(1, 6) if sum(1 for r in pool if r["stars"] == s) < per_star]
    if short:
        add(await asyncio.gather(*(one(s, pages + 1) for s in short)))
    return pool


# ---------------------------------------------------------------- App Store (RSS public d'Apple)

def as_url(app_id: int, page: int) -> str:
    return f"https://itunes.apple.com/fr/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json"


async def fetch_appstore(preset: dict, target: int, on_event) -> list[dict]:
    app_id = preset["app_id"]
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0 (jev-arena)"}) as c:
        async def one(page: int):
            url = as_url(app_id, page)
            t = time.time()
            revs, note = [], ""
            try:
                r = await c.get(url)
                entries = (r.json().get("feed") or {}).get("entry") or []
                if isinstance(entries, dict):
                    entries = [entries]
                for e in entries:
                    if "im:rating" not in e:
                        continue
                    text = clean_text(((e.get("content") or {}).get("label")) or "")
                    title = clean_text(((e.get("title") or {}).get("label")) or "")
                    if len(text) < MIN_TEXT:
                        continue
                    revs.append({"id": "as-" + str((e.get("id") or {}).get("label")),
                                 "stars": int(e["im:rating"]["label"]), "title": title, "text": text,
                                 "author": mask_name(((e.get("author") or {}).get("name") or {}).get("label")),
                                 "date": (((e.get("updated") or {}).get("label")) or "")[:10]})
            except Exception as ex:
                note = f"échec : {str(ex)[:80]}"
            ms = round((time.time() - t) * 1000)
            by = {s: sum(1 for x in revs if x["stars"] == s) for s in range(1, 6)}
            await on_event({"tool": "rss", "kind": "page", "label": f"page {page}", "url": url, "page": page,
                            "n": len(revs), "ms": ms, "ok": bool(revs), "note": note, "by_star": by})
            return revs

        res = await asyncio.gather(*(one(p) for p in range(1, 11)))
    pool, seen = [], set()
    for revs in res:
        for r in revs:
            if r["id"] not in seen:
                seen.add(r["id"])
                pool.append(r)
    return pool


# ---------------------------------------------------------------- Google Play (google-play-scraper, pur Python)

GP_LANG, GP_COUNTRY = "fr", "fr"
GP_MAX_LOTS = 3       # lots par note au maximum (le premier fait le double du besoin)


def gp_url(package: str) -> str:
    """Page publique dont on lit les avis (le scraper appelle le point d'acces interne de cette page)."""
    return f"https://play.google.com/store/apps/details?id={package}&hl=fr&gl=FR"


def gp_convert(x: dict) -> dict | None:
    """Avis brut du scraper -> avis de la scene (sans titre : Google Play n'en a pas), ou None s'il est trop court."""
    text = clean_text(x.get("content") or "")
    score = x.get("score")
    if not score or len(text) < MIN_TEXT:
        return None
    at = x.get("at")
    date = at.strftime("%Y-%m-%d") if hasattr(at, "strftime") else str(at or "")[:10]
    return {"id": "gp-" + str(x.get("reviewId")), "stars": int(score), "title": "", "text": text,
            "author": mask_name(x.get("userName")), "date": date,
            "version": x.get("appVersion") or x.get("reviewCreatedVersion") or ""}


async def fetch_googleplay(preset: dict, target: int, on_event) -> list[dict]:
    if gp_reviews is None:
        raise RuntimeError("google-play-scraper n'est pas installé dans le conteneur "
                           "(pip install google-play-scraper, ou reconstruire l'image)")
    package = preset["package"]
    url = gp_url(package)
    per_star = -(-target // 5)
    batch = max(20, per_star * 2)       # beaucoup d'avis 5 ★ d'un mot : on lit le double, puis un lot de plus si besoin

    async def one(star: int) -> list[dict]:
        got, token, lot = [], None, 1
        while True:
            t = time.time()
            revs, note, exhausted = [], "", False
            try:
                raw, token = await asyncio.wait_for(asyncio.to_thread(
                    gp_reviews, package, lang=GP_LANG, country=GP_COUNTRY, sort=GPSort.NEWEST, count=batch,
                    filter_score_with=star, continuation_token=token), 40)
                exhausted = len(raw) < batch
                for x in raw:
                    r = gp_convert(x)
                    if r is None:
                        continue
                    if r["stars"] != star:
                        note = "filtre perdu"          # Google n'a pas respecte le filtre : on garde la vraie note
                    revs.append(r)
                dropped = len(raw) - len(revs)
                if dropped:
                    note = (note + " · " if note else "") + f"{dropped} trop courts écartés"
            except Exception as e:                    # 429 (debit limite), appli introuvable, reseau : affiche tel quel
                note = f"échec : {type(e).__name__} {str(e)[:70]}".strip()
                exhausted = True
            ms = round((time.time() - t) * 1000)
            await on_event({"tool": "googleplay", "kind": "page", "label": f"{star} ★ · lot {lot}", "url": url,
                            "star": star, "page": lot, "n": len(revs), "ms": ms, "ok": bool(revs), "note": note})
            got.extend(revs)
            if exhausted or lot >= GP_MAX_LOTS or sum(1 for r in got if r["stars"] == star) >= per_star:
                return got
            lot += 1

    res = await asyncio.gather(*(one(s) for s in range(1, 6)))
    pool, seen = [], set()
    for revs in res:
        for r in revs:
            if r["id"] not in seen:
                seen.add(r["id"])
                pool.append(r)
    return pool


# ---------------------------------------------------------------- equilibrage

def balance(pool: list[dict], target: int, seed: int = 7) -> list[dict]:
    """Autant d'avis que possible par note (target/5 chacune), les manques combles par les notes les plus
    fournies, puis melange (graine fixe) pour que les notes arrivent entremelees."""
    by = {s: [r for r in pool if r["stars"] == s] for s in range(1, 6)}
    quota = {s: target // 5 + (1 if s <= target % 5 else 0) for s in range(1, 6)}
    pick = {s: by[s][:quota[s]] for s in range(1, 6)}
    missing = target - sum(len(v) for v in pick.values())
    while missing > 0:
        spare = sorted(((len(by[s]) - len(pick[s]), s) for s in range(1, 6)), reverse=True)
        n_spare, s = spare[0]
        if n_spare <= 0:
            break
        pick[s].append(by[s][len(pick[s])])
        missing -= 1
    out = [r for s in range(1, 6) for r in pick[s]]
    random.Random(seed).shuffle(out)
    return out


def fetch_plan(preset: dict, target: int) -> list[dict]:
    """Pages qui vont etre lues (affichees en attente avant leur arrivee)."""
    if preset["source"] == "trustpilot":
        pages = -(-(-(-target // 5)) // 20)
        return [{"tool": "lightpanda", "label": f"{s} ★ · page {pg}", "url": tp_url(preset["domain"], s, pg)}
                for s in range(1, 6) for pg in range(1, pages + 1)]
    if preset["source"] == "googleplay":
        return [{"tool": "googleplay", "label": f"{s} ★ · lot 1", "url": gp_url(preset["package"])}
                for s in range(1, 6)]
    return [{"tool": "rss", "label": f"page {pg}", "url": as_url(preset["app_id"], pg)} for pg in range(1, 11)]


async def fetch_reviews(preset: dict, target: int, on_event) -> list[dict]:
    if preset["source"] == "trustpilot":
        pool = await fetch_trustpilot(preset, target, on_event)
    elif preset["source"] == "googleplay":
        pool = await fetch_googleplay(preset, target, on_event)
    else:
        pool = await fetch_appstore(preset, target, on_event)
    return pool


__all__ = ["PRESETS", "PRESET_BY_ID", "fetch_reviews", "fetch_plan", "balance"]
