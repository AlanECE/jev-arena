"""Source des leads : recherche GitHub (pour trouver des usernames) puis le scraper GitHub de Scout
(vendor/Scout, code non modifie) sur chaque profil.

Scout n'a pas de recherche : on lui donne des usernames trouves par l'API de recherche GitHub (30 req/min avec
jeton). Son paquet s'appelle `app`, comme le notre : on charge ses modules par chemin, sans son __init__ (qui
importe toutes les plateformes), et on injecte le jeton GitHub dans ses requetes (sinon 60 req/h).
"""
import asyncio
import importlib.util
import json
import re
import sys
import time
import types
from pathlib import Path
from urllib.parse import urlparse

import httpx
import requests

from ...core.config import DATA, GITHUB_TOKEN

SCOUT_DIR = Path("/srv/vendor/Scout/app/scrapers")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_scout():
    utils = _load("scout_utils", SCOUT_DIR / "utils.py")
    stealth = _load("scout_stealth", SCOUT_DIR / "stealth.py")
    alias = {"app.scrapers": types.ModuleType("app.scrapers"), "app.scrapers.utils": utils,
             "app.scrapers.stealth": stealth}
    saved = {k: sys.modules.get(k) for k in alias}
    sys.modules.update(alias)
    try:
        github = _load("scout_github", SCOUT_DIR / "github.py")
        enrichment = _load("scout_enrichment", SCOUT_DIR / "enrichment.py")  # seulement pour ses regles d'email
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return github, enrichment


scout_github, scout_enrichment = _load_scout()
EMAIL_RE = scout_enrichment.EMAIL_RE
EMAIL_BLACKLIST = scout_enrichment.EMAIL_BLACKLIST
FILE_EXT_BLACKLIST = scout_enrichment.FILE_EXT_BLACKLIST


class _RequestsWithToken:
    """`requests` vu par le module GitHub de Scout : identique, plus le jeton sur api.github.com."""

    def __getattr__(self, k):
        return getattr(requests, k)

    @staticmethod
    def get(url, headers=None, **kw):
        headers = dict(headers or {})
        if GITHUB_TOKEN and "api.github.com" in url:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        return requests.get(url, headers=headers, **kw)


scout_github.requests = _RequestsWithToken()


def scrape_profile(username: str):
    """Scraper GitHub de Scout (synchrone : a lancer dans un thread)."""
    return scout_github.scrape_profile(username)


# Requetes de recherche : des gens qui construisent et lancent des applis / produits dans une petite structure
# (fondateurs, indie makers, startups, TPE / PME). GitHub est une source de developpeurs : c'est la limite honnete.
QUERIES = [
    '"solo founder" in:bio',
    '"building" "app" in:bio',
    '"saas" "founder" in:bio',
    '"indie hacker" in:bio',
    '"ios app" in:bio',
    '"launching" in:bio',
    '"indie developer" in:bio',
    '"app developer" "founder" in:bio',
    '"building in public" in:bio',
    '"founder" "app" in:bio',
    '"co-founder" "startup" in:bio',
    '"building" "saas" in:bio',
    '"indiehacker" in:bio',
    '"maker of" in:bio',
    '"bootstrapping" in:bio',
    '"chrome extension" in:bio',
    '"early access" in:bio',
    '"waitlist" in:bio',
    '"fondateur" in:bio',
    '"small business" in:bio',
    '"ceo" "app" in:bio',
    '"cto" "startup" in:bio',
    '"founder" "mobile app" in:bio',
    '"startup" "founder" in:bio location:France',
    '"founder" "app" in:bio location:Paris',
    '"ceo" "startup" in:bio location:France',
    '"my app" in:bio',
    '"our app" in:bio',
]
PER_PAGE = 30
SEARCH_GAP_S = 2.1  # 30 requetes/min authentifie

SOCIAL = ["youtube.com", "youtu.be", "instagram.com", "tiktok.com", "twitter.com", "x.com", "facebook.com",
          "linktr.ee", "linkedin.com", "github.com", "bit.ly", "medium.com", "dev.to", "t.me", "bsky.app",
          "threads.net", "discord.gg", "discord.com", "twitch.tv", "reddit.com", "substack.com", "hashnode.dev",
          "gitlab.com", "stackoverflow.com", "calendly.com", "about.me", "notion.so", "notion.site", "behance.net",
          "dribbble.com", "kaggle.com", "huggingface.co", "leetcode.com", "codepen.io", "mastodon.social",
          "google.com", "gmail.com", "polywork.com", "read.cv", "peerlist.io", "wellfound.com", "angel.co",
          "producthunt.com", "indiehackers.com", "buymeacoffee.com", "ko-fi.com", "patreon.com", "gumroad.com"]


def site_url(profile: dict) -> str | None:
    """URL du site du profil, ou None si absent ou si c'est un reseau social."""
    w = (profile or {}).get("website") or ""
    w = w.strip().split()[0] if w.strip() else ""
    if not w:
        return None
    if not re.match(r"^https?://", w, re.I):
        w = "https://" + w
    try:
        host = (urlparse(w).hostname or "").lower()
    except ValueError:
        return None
    if not host or "." not in host or host.replace(".", "").isdigit():
        return None
    if any(host == d or host.endswith("." + d) for d in SOCIAL):
        return None
    return w


def root_host(host: str) -> str:
    h = (host or "").lower()
    if h.startswith("www."):
        h = h[4:]
    return h


class SeenStore:
    """Profils deja traites (d'une course a l'autre) + curseur de page par requete, dans /data/leads/seen.json."""

    def __init__(self):
        self.path = DATA / "leads" / "seen.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.users: set[str] = set()
        self.hosts: set[str] = set()
        self.cursors: dict[str, int] = {}
        self.exhausted: set[str] = set()
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                self.users = set(d.get("users", []))
                self.hosts = set(d.get("hosts", []))
                self.cursors = dict(d.get("cursors", {}))
                self.exhausted = set(d.get("exhausted", []))
            except Exception:
                pass

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"users": sorted(self.users), "hosts": sorted(self.hosts), "cursors": self.cursors,
                                   "exhausted": sorted(self.exhausted), "saved": time.time()}), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self):
        self.users, self.hosts, self.cursors, self.exhausted = set(), set(), {}, set()
        self.save()

    def info(self) -> dict:
        return {"users": len(self.users), "hosts": len(self.hosts)}


class GitHubSearch:
    """Usernames depuis l'API de recherche GitHub, requete par requete (tourniquet), en respectant 30 req/min."""

    def __init__(self, seen: SeenStore, on_status):
        self.seen = seen
        self.on_status = on_status  # async (dict) -> None
        self.requests = 0
        self.query = None
        self.rate_remaining = None
        self._last = 0.0
        self._client = httpx.AsyncClient(timeout=20, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "jev-arena-leads",
            **({"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {})})

    async def close(self):
        await self._client.aclose()

    async def _search(self, q: str, page: int):
        wait = self._last + SEARCH_GAP_S - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.time()
        r = await self._client.get("https://api.github.com/search/users",
                                   params={"q": q + " type:user", "per_page": PER_PAGE, "page": page})
        self.requests += 1
        try:
            self.rate_remaining = int(r.headers.get("x-ratelimit-remaining", "-1"))
        except ValueError:
            pass
        if r.status_code in (403, 429):
            reset = int(r.headers.get("x-ratelimit-reset", str(int(time.time()) + 20)))
            pause = max(2, min(65, reset - int(time.time()) + 1))
            await self.on_status({"github_pause": pause})
            await asyncio.sleep(pause)
            return None
        if r.status_code == 422:  # au-dela des 1000 premiers resultats
            return []
        if r.status_code != 200:
            return []
        return [it["login"] for it in r.json().get("items", [])]

    async def usernames(self):
        """Generateur asynchrone de (username, requete)."""
        active = [q for q in QUERIES if q not in self.seen.exhausted]
        i = 0
        while active:
            q = active[i % len(active)]
            page = self.seen.cursors.get(q, 0) + 1
            self.query = q
            await self.on_status({"query": q, "page": page})
            items = await self._search(q, page)
            if items is None:  # limite atteinte : on retente la meme page
                continue
            self.seen.cursors[q] = page
            if len(items) < PER_PAGE or page * PER_PAGE >= 1000:
                self.seen.exhausted.add(q)
                active.remove(q)
            else:
                i += 1
            for u in items:
                if u not in self.seen.users:
                    yield u, q
