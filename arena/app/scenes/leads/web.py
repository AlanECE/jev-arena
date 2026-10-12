"""Lecteur web partage par les deux modeles : Lightpanda (sous-processus dans le conteneur) puis Crawl4AI en
secours. Chaque URL n'est lue qu'une fois (cache) : si Jev et Opus choisissent la meme page, une seule visite.

On garde un resume compact de chaque page : titre, description, titres, debut du texte visible, signaux
(App Store, Google Play, Product Hunt, TestFlight, liste d'attente, video...), liens internes et emails
(regex de Scout + mailto + decodage Cloudflare).
"""
import asyncio
import html as _html
import json
import re
import time
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
import lxml.html

from ...core.config import CRAWL4AI_TOKEN, CRAWL4AI_URL
from .scout import EMAIL_BLACKLIST, EMAIL_RE, FILE_EXT_BLACKLIST, root_host

LP_ARGS = ["--json", "--dump", "html", "--wait-until", "load", "--wait-ms", "2000",
           "--terminate-ms", "5000", "--strip-mode", "css", "--log-level", "fatal"]
LP_TIMEOUT_S = 9
C4_TIMEOUT_S = 15
C4_PAGE_TIMEOUT_MS = 9000
NO_RETRY_ERRORS = ("CouldntResolveHost", "UnknownHostName", "ConnectionRefused")

EXTRA_BAD = ["sentry", "@2x", "@3x", "domain.com", "yourdomain", "votre", "exemple", "u003e", "wixpress", "noreply",
             "no-reply", "email@", "name@", "john.doe", "jane", "user@", "@mail.com", "prenom", "nom@", "your@",
             "you@", "someone@", "username@", "example", "@company.com", "@acme", "test@"]
FREEMAIL = ("gmail.com", "googlemail.com", "outlook.", "hotmail.", "live.", "yahoo.", "icloud.com", "me.com",
            "proton", "pm.me", "laposte.net", "orange.fr", "free.fr", "sfr.fr", "wanadoo.fr", "gmx.", "tutanota",
            "zoho", "hey.com", "fastmail", "mail.ru", "yandex", "qq.com", "163.com", "naver.com", "aol.com")
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|xml|rss|mp4|mp3|css|js|ico|avif|woff2?|dmg|exe|apk)$", re.I)


def valid_email(e: str) -> bool:
    l = e.lower()
    if any(b in l for b in EMAIL_BLACKLIST) or any(l.endswith(x) for x in FILE_EXT_BLACKLIST):
        return False
    if any(b in l for b in EXTRA_BAD):
        return False
    if re.search(r"\.(png|jpe?g|gif|webp|svg|css|js|avif|html?)$", l):
        return False
    local, _, dom = l.partition("@")
    if not local or len(l) > 80 or re.fullmatch(r"[0-9a-f]{16,}", local):
        return False
    return "." in dom


def _cf_decode(hexstr: str) -> str:
    try:
        k = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ k) for i in range(2, len(hexstr), 2))
    except Exception:
        return ""


def extract_emails(html_text: str) -> list[str]:
    if not html_text:
        return []
    t = _html.unescape(html_text)
    t = t.replace("[at]", "@").replace("(at)", "@").replace(" [dot] ", ".").replace("%40", "@")
    found = re.findall(EMAIL_RE, t)
    found += [_cf_decode(h) for h in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html_text)]
    out, seen = [], set()
    for e in found:
        e = e.strip().strip(".").lower()
        if e and "@" in e and valid_email(e) and e not in seen:
            seen.add(e)
            out.append(e)
    return out[:12]


def attributable(email: str, host: str) -> bool:
    """Email du domaine du site, ou boite perso : on ne garde que ceux-la (pas support@stripe.com...)."""
    dom = email.split("@", 1)[1]
    h = root_host(host)
    parts = h.split(".")
    root = ".".join(parts[-2:]) if len(parts) >= 2 else h
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov"):
        root = ".".join(parts[-3:])
    return (bool(root) and (dom == root or dom.endswith("." + root))) or any(f in dom for f in FREEMAIL)


VIDEO_PATTERNS = [
    ("youtube", re.compile(r"youtube(-nocookie)?\.com/embed/|youtube\.com/watch|youtu\.be/", re.I)),
    ("vimeo", re.compile(r"player\.vimeo\.com|vimeo\.com/\d", re.I)),
    ("wistia", re.compile(r"wistia\.(com|net)", re.I)),
    ("loom", re.compile(r"loom\.com/(embed|share)", re.I)),
]
WAITLIST_RE = re.compile(r"wait ?list|early access|join (the )?beta|private beta|coming soon|launching soon|"
                         r"get notified|notify me|pre-?order|liste d'attente|acc[eè]s anticip[ée]|bient[ôo]t disponible",
                         re.I)
SIGNUP_RE = re.compile(r"sign ?up|get started|start (your )?free|free trial|try (it )?(for )?free|create (an )?account|"
                       r"download (now|the app|for)|inscri|essai gratuit|commencer", re.I)


def summarize(url: str, html_text: str) -> dict:
    """Resume compact d'une page (appele dans un thread : lxml peut prendre quelques dizaines de ms)."""
    out = {"title": "", "description": "", "headings": [], "text": "", "text_len": 0, "links": [],
           "signals": {}, "emails": extract_emails(html_text)}
    try:
        doc = lxml.html.fromstring(html_text)
    except Exception:
        return out
    low_html = html_text.lower()
    hrefs = [a.get("href", "") for a in doc.xpath("//a[@href]")]
    srcs = [x.get("src", "") for x in doc.xpath("//iframe[@src]|//video[@src]|//video/source[@src]|//embed[@src]")]
    all_refs = " ".join(hrefs + srcs)
    videos = []
    for name, rx in VIDEO_PATTERNS:
        if any(rx.search(s) for s in srcs):
            videos.append(f"{name} player")
        elif rx.search(" ".join(hrefs)):
            videos.append(f"{name} link")
    if doc.xpath("//video"):
        videos.append("html5 video")
    has_email_input = bool(doc.xpath("//input[@type='email']"))

    for bad in doc.xpath("//script|//style|//noscript|//svg|//template"):
        bad.drop_tree()
    title = re.sub(r"\s+", " ", doc.findtext(".//title") or "").strip()
    desc = ""
    for m in doc.xpath("//meta[@name='description']/@content|//meta[@property='og:description']/@content"):
        if m.strip():
            desc = m.strip()
            break
    heads = []
    for h in doc.xpath("//h1|//h2|//h3"):
        t = re.sub(r"\s+", " ", h.text_content() or "").strip()
        if t and t not in heads and len(t) > 2:
            heads.append(t[:90])
        if len(heads) >= 8:
            break
    body = doc.find(".//body")
    text = re.sub(r"\s+", " ", (body if body is not None else doc).text_content() or "").strip()
    buttons = " ".join((b.text_content() or "") for b in doc.xpath("//button|//a[@href]"))[:4000]

    host = root_host(urlparse(url).hostname or "")
    links, seen_paths = [], set()
    for a in doc.xpath("//a[@href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absu = urldefrag(urljoin(url, href))[0]
        p = urlparse(absu)
        if p.scheme not in ("http", "https") or SKIP_EXT.search(p.path or ""):
            continue
        if root_host(p.hostname or "") != host:
            continue
        query = "&".join(x for x in p.query.split("&") if x and not x.lower().startswith(("utm_", "ref=", "fbclid")))
        path = (p.path.rstrip("/") or "/") + (("?" + query) if query else "")
        if path == "/" or path in seen_paths:
            continue
        seen_paths.add(path)
        txt = re.sub(r"\s+", " ", a.text_content() or a.get("title") or a.get("aria-label") or "").strip()
        links.append({"url": absu if not p.query or query == p.query else absu.split("?")[0] + (("?" + query) if query else ""),
                      "path": path[:100], "text": txt[:50]})
        if len(links) >= 80:
            break

    blob = " ".join([title, desc, " ".join(heads), buttons])
    out.update({
        "title": title[:140], "description": desc[:300], "headings": heads, "text": text[:1500],
        "text_len": len(text), "links": links,
        "signals": {
            "app_store_link": bool(re.search(r"apps\.apple\.com|itunes\.apple\.com", all_refs, re.I)),
            "google_play_link": "play.google.com" in all_refs,
            "product_hunt_link": "producthunt.com" in all_refs.lower() or "producthunt" in low_html[:400000],
            "testflight_link": "testflight.apple.com" in all_refs,
            "chrome_extension_link": bool(re.search(r"chromewebstore\.google\.com|chrome\.google\.com/webstore", all_refs)),
            "waitlist_or_early_access": bool(WAITLIST_RE.search(blob)),
            "signup_or_download_button": bool(SIGNUP_RE.search(buttons)),
            "email_capture_form": has_email_input,
            "pricing": bool(re.search(r"/pricing|/tarifs|/plans\b", all_refs, re.I)) or bool(re.search(r"\bpricing\b|\btarifs\b", buttons, re.I)),
            "video": videos,
        },
    })
    return out


class Fetcher:
    """Lecture partagee et mise en cache. `get` renvoie (resultat, deja_lu)."""

    def __init__(self, on_event, lp_conc: int = 24, c4_conc: int = 4):
        self.on_event = on_event  # async (dict) -> None
        self.cache: dict[str, asyncio.Task] = {}
        self.kinds: dict[str, str] = {}
        self.lp_sem = asyncio.Semaphore(lp_conc)
        self.c4_sem = asyncio.Semaphore(c4_conc)
        self.stats = {"pages": 0, "ok": 0, "fail": 0, "lightpanda": 0, "crawl4ai": 0, "c4_tries": 0,
                      "cache_hits": 0, "inflight": 0, "ms_total": 0.0}
        self._c4 = httpx.AsyncClient(timeout=C4_TIMEOUT_S, headers=(
            {"Authorization": f"Bearer {CRAWL4AI_TOKEN}"} if CRAWL4AI_TOKEN else {}))
        self._procs: set = set()

    async def close(self):
        for p in list(self._procs):
            try:
                p.kill()
            except Exception:
                pass
        await self._c4.aclose()

    def cancel_kind(self, kind: str):
        """Abandonne les lectures encore en cours d'un type (ex. pages d'accueil en trop une fois l'objectif atteint)."""
        n = 0
        for k, t in self.cache.items():
            if self.kinds.get(k) == kind and not t.done():
                t.cancel()
                n += 1
        return n

    @staticmethod
    def key(url: str) -> str:
        u = urldefrag(url)[0]
        p = urlparse(u)
        return f"{root_host(p.hostname or '')}{p.path.rstrip('/') or '/'}{('?' + p.query) if p.query else ''}"

    async def get(self, url: str, kind: str, meta: dict | None = None):
        k = self.key(url)
        t = self.cache.get(k)
        if t is not None:
            self.stats["cache_hits"] += 1
            res = await asyncio.shield(t)
            await self.on_event({"type": "web", "kind": kind, "cached": True, "host": res["host"],
                                 "path": res["path"], "ok": res["ok"], "tool": res["tool"], "ms": 0, **(meta or {})})
            return res, True
        t = asyncio.create_task(self._fetch(url, kind, meta or {}))
        self.cache[k] = t
        self.kinds[k] = kind
        return await asyncio.shield(t), False

    async def _fetch(self, url: str, kind: str, meta: dict) -> dict:
        t0 = time.time()
        self.stats["inflight"] += 1
        p = urlparse(url)
        res = {"url": url, "final_url": url, "host": root_host(p.hostname or ""), "path": p.path or "/",
               "ok": False, "tool": None, "status": None, "error": None, "page": None, "kind": kind}
        try:
            html, final, status, err = await self._lightpanda(url)
            page = await asyncio.to_thread(summarize, final or url, html) if html else None
            if self._good(page, status):
                res.update(ok=True, tool="lightpanda", final_url=final or url, status=status, page=page)
                self.stats["lightpanda"] += 1
            else:
                lp_err = err or (f"HTTP {status}" if status and status >= 400 else "page vide")
                if err and any(x in err for x in NO_RETRY_ERRORS):
                    res.update(error=f"Lightpanda : {err}", status=status)
                else:
                    self.stats["c4_tries"] += 1
                    html2, final2, status2, err2 = await self._crawl4ai(url)
                    page2 = await asyncio.to_thread(summarize, final2 or url, html2) if html2 else None
                    if self._good(page2, status2):
                        res.update(ok=True, tool="crawl4ai", final_url=final2 or url, status=status2, page=page2,
                                   error=f"Lightpanda : {lp_err}")
                        self.stats["crawl4ai"] += 1
                    else:
                        c4_err = err2 or (f"HTTP {status2}" if status2 and status2 >= 400 else "page vide")
                        res.update(error=f"Lightpanda : {lp_err} · Crawl4AI : {c4_err}", status=status2 or status)
        except asyncio.CancelledError:  # abandonnee (objectif atteint) : ni comptee, ni affichee
            raise
        except Exception as e:  # jamais bloquant : une page ratee reste une page ratee
            res["error"] = f"lecture : {str(e)[:120]}"
        finally:
            self.stats["inflight"] -= 1
        ms = round((time.time() - t0) * 1000)
        res["ms"] = ms
        self.stats["pages"] += 1
        self.stats["ms_total"] += ms
        self.stats["ok" if res["ok"] else "fail"] += 1
        await self.on_event({"type": "web", "kind": kind, "cached": False, "host": res["host"], "path": res["path"],
                             "ok": res["ok"], "tool": res["tool"], "ms": ms, "status": res["status"],
                             "error": None if res["ok"] else res["error"], **meta})
        return res

    @staticmethod
    def _good(page, status) -> bool:
        if not page:
            return False
        if status and status >= 400:
            return False
        return page["text_len"] > 150 or len(page["links"]) >= 3

    async def _lightpanda(self, url: str):
        async with self.lp_sem:
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    "lightpanda", "fetch", *LP_ARGS, url,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                self._procs.add(proc)
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=LP_TIMEOUT_S)
            except asyncio.TimeoutError:
                if proc:
                    proc.kill()
                return "", None, None, "delai depasse"
            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.kill()
                raise
            except FileNotFoundError:
                return "", None, None, "lightpanda introuvable"
            finally:
                if proc:
                    self._procs.discard(proc)
            txt = out.decode("utf-8", "replace").strip()
            if not txt:
                return "", None, None, "sortie vide"
            try:
                d = json.loads(txt)
            except Exception:
                return "", None, None, "JSON illisible"
            if isinstance(d, dict) and "results" in d:
                d = (d["results"] or [{}])[0]
            return (d.get("content") or ""), d.get("url"), d.get("http_status"), d.get("error")

    async def _crawl4ai(self, url: str):
        async with self.c4_sem:
            try:
                r = await self._c4.post(f"{CRAWL4AI_URL}/crawl", json={
                    "urls": [url],
                    "crawler_config": {"type": "CrawlerRunConfig", "params": {
                        "page_timeout": C4_PAGE_TIMEOUT_MS, "stream": False, "cache_mode": "bypass"}}})
                if r.status_code != 200:
                    return "", None, None, f"HTTP {r.status_code}"
                x = (r.json().get("results") or [{}])[0]
            except Exception as e:
                return "", None, None, type(e).__name__
            if not x.get("success"):
                return "", None, x.get("status_code"), (x.get("error_message") or "echec")[:80]
            return (x.get("html") or ""), x.get("redirected_url") or url, x.get("status_code"), None
