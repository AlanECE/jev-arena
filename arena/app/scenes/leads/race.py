"""La course aux leads, en vrai.

Une source partagee (recherche GitHub -> scraper GitHub de Scout -> page d'accueil lue par Lightpanda, Crawl4AI en
secours) remplit deux files identiques, une par modele. Chaque file est videe par le MEME nombre d'ouvriers (conc).
Pour chaque lead et chaque modele : 1 decision « quel lien ouvrir pour trouver un email » -> le lecteur partage
ouvre la page choisie (une seule visite si les deux choisissent la meme) -> 1 decision de qualification.
Rien n'est ralenti ni accelere : Jev attend le web, Opus fait attendre sa file.
"""
import asyncio
import logging
import statistics
import time
import traceback
from collections import deque
from urllib.parse import urlparse

from ...core.deciders import client as or_client
from ...core.deciders import decide, top_choice
from ...core.hub import HUB
from ...core.ledger import LEDGER_OBJ, BudgetExceeded
from .questions import QUAL_Q, STAGES, link_question, link_state, pick_candidates, qual_state
from .scout import GitHubSearch, root_host, scrape_profile, site_url
from .web import Fetcher, attributable, valid_email

log = logging.getLogger("leads")

SCENE = "leads"
MODELS = ("jev", "opus")
PROFILE_WORKERS = 8
HOME_WORKERS = 16
ROWS_KEPT = 14
FEED_KEPT = 14


def _med(xs):
    return round(statistics.median(xs), 1) if xs else None


class Side:
    def __init__(self, name: str):
        self.name = name
        self.q: asyncio.Queue = asyncio.Queue()
        self.queued = 0
        self.inflight = 0
        self.done = 0
        self.decisions = 0
        self.failed_calls = 0
        self.cost = 0.0
        self.emails = 0            # leads avec au moins un email
        self.emails_link = 0       # ... dont l'email vient de la page choisie par le modele
        self.email_set: set[str] = set()  # emails uniques trouves de ce cote (la liste qui s'incremente)
        self.email_list: deque = deque(maxlen=40)  # les derniers trouves, pour une page qui se (re)connecte en direct
        self.links_opened = 0
        self.oui = 0
        self.lat: list[float] = []
        self.lead_ms: list[float] = []
        self.t_done = None
        self.rows: deque = deque(maxlen=ROWS_KEPT)
        self.results: dict[int, dict] = {}
        self.workers: list[asyncio.Task] = []

    def stats(self) -> dict:
        return {"done": self.done, "decisions": self.decisions, "failed_calls": self.failed_calls,
                "cost": round(self.cost, 6), "emails": self.emails, "emails_link": self.emails_link,
                "email_count": len(self.email_set),
                "links_opened": self.links_opened, "oui": self.oui, "inflight": self.inflight,
                "waiting": max(0, self.queued), "t_done": self.t_done, "lat_med_ms": _med(self.lat),
                "lead_med_ms": _med(self.lead_ms)}


class Race:
    def __init__(self, scene, target: int, conc: int):
        self.scene = scene
        self.seen = scene.seen
        self.target = target
        self.conc = conc
        self.phase = "idle"
        self.sides = {m: Side(m) for m in MODELS}
        self.fetcher: Fetcher | None = None
        self.search: GitHubSearch | None = None
        self.pipe = {"usernames": 0, "profiles": 0, "empty": 0, "no_site": 0, "dup": 0, "with_site": 0,
                     "home_ok": 0, "home_fail": 0, "dispatched": 0, "query": None, "page": None,
                     "github_pause": 0, "search_requests": 0, "rate_remaining": None, "exhausted": False}
        self.feed: deque = deque(maxlen=FEED_KEPT)
        self.leads: dict[int, dict] = {}
        self.agree = {"n": 0, "verdict": 0, "stage": 0, "prio": 0, "link_n": 0, "link": 0}
        self.agree_flags: dict[int, bool] = {}
        self.disagreements: list[dict] = []
        self.t0 = None
        self.t_source_done = None
        self.t_end = None
        self.summary = None
        self.error = None
        self.enough = asyncio.Event()
        self.stop = asyncio.Event()
        self.q_users: asyncio.Queue = asyncio.Queue(maxsize=80)
        self.q_sites: asyncio.Queue = asyncio.Queue(maxsize=10)
        self.home_inflight: dict[str, tuple] = {}
        self.tasks: list[asyncio.Task] = []
        self.session_path = None

    # ------------------------------------------------------------------ outils
    def el(self) -> float:
        return round(time.time() - self.t0, 3) if self.t0 else 0.0

    async def emit(self, evt: dict, record: bool = True):
        await self.scene.emit(evt, record=record)

    def stats(self) -> dict:
        led = LEDGER_OBJ.summary().get(SCENE, {})
        a = self.agree
        return {"phase": self.phase, "elapsed": self.el() if self.phase == "running" else self._end_elapsed(),
                "target": self.target, "conc": self.conc,
                "pipe": {**self.pipe, "fetch": dict(self.fetcher.stats) if self.fetcher else {},
                         "source_done": self.t_source_done},
                "models": {m: s.stats() for m, s in self.sides.items()},
                "agree": {"n": a["n"], "verdict": (a["verdict"] / a["n"]) if a["n"] else None,
                          "stage": (a["stage"] / a["n"]) if a["n"] else None,
                          "link": (a["link"] / a["link_n"]) if a["link_n"] else None},
                "budget": {"spent": led.get("spent", 0.0), "cap": LEDGER_OBJ.cap(SCENE)}}

    def _end_elapsed(self):
        if self.t_end is not None:
            return self.t_end
        ts = [s.t_done for s in self.sides.values() if s.t_done is not None]
        return max(ts) if ts else self.el()

    # ------------------------------------------------------------------ course
    async def run(self):
        self.phase = "running"
        self.t0 = time.time()
        self.fetcher = Fetcher(self._on_web)
        self.search = GitHubSearch(self.seen, self._on_search)
        self.session_path = HUB.recorder(SCENE).start({"target": self.target, "conc": self.conc,
                                                       "models": list(MODELS), "t0": self.t0})
        await self.emit({"type": "race_start", "target": self.target, "conc": self.conc, "t0": self.t0})
        waiter = None
        try:
            self.tasks.append(asyncio.create_task(self._warm()))
            for side in self.sides.values():
                side.workers = [asyncio.create_task(self._model_worker(side)) for _ in range(self.conc)]
                self.tasks += side.workers
            self.tasks.append(asyncio.create_task(self._ticker()))
            source = asyncio.create_task(self._source())
            self.tasks.append(source)
            sides_done = asyncio.gather(*(self._side_done(s) for s in self.sides.values()))
            waiter = asyncio.create_task(self.stop.wait())
            await asyncio.wait([sides_done, waiter], return_when=asyncio.FIRST_COMPLETED)
            if self.stop.is_set():
                self.t_end = self.el()
                self.phase = "error"
                sides_done.cancel()
                await self.emit({"type": "race_error", "error": self.error, "elapsed": self.el()})
            else:
                self.t_end = self._end_elapsed()
                self.phase = "done"
                self.summary = self._summary()
                await self.emit({"type": "race_end", "summary": self.summary})
        except asyncio.CancelledError:
            self.t_end = self.el()
            self.phase = "stopped"
            raise
        except Exception as e:
            self.t_end = self.el()
            self.phase = "error"
            self.error = f"{type(e).__name__} : {e}"
            log.error(traceback.format_exc())
            await self.emit({"type": "race_error", "error": self.error, "elapsed": self.el()})
        finally:
            if waiter:
                waiter.cancel()
            await self._cleanup()

    async def _cleanup(self):
        for t in self.tasks:
            if not t.done():
                t.cancel()
        # profils pris dans la file mais jamais lus : on les rend aux prochaines courses
        while not self.q_sites.empty():
            it = self.q_sites.get_nowait()
            if it:
                self.seen.users.discard(it[0].get("username"))
                self.seen.hosts.discard(it[2])
        for host, user in list(self.home_inflight.items()):
            self.seen.users.discard(user)
            self.seen.hosts.discard(host)
        try:
            self.seen.save()
        except Exception:
            pass
        try:
            await self.emit({"type": "stats", **self.stats()})
        except Exception:
            pass
        HUB.recorder(SCENE).stop()
        if self.fetcher:
            await self.fetcher.close()
        if self.search:
            await self.search.close()

    async def _warm(self):
        """Ouvre les connexions TLS vers OpenRouter (requetes gratuites) : la 1re decision n'est pas penalisee."""
        try:
            await asyncio.gather(*(or_client().head("https://openrouter.ai/") for _ in range(4)),
                                 return_exceptions=True)
        except Exception:
            pass

    async def _ticker(self):
        i = 0
        while self.phase == "running":
            await asyncio.sleep(0.25)
            i += 1
            await self.emit({"type": "stats", **self.stats()}, record=(i % 4 == 0))

    async def _side_done(self, side: Side):
        await asyncio.gather(*side.workers, return_exceptions=True)
        side.t_done = self.el()
        await self.emit({"type": "model_done", "model": side.name, "t": side.t_done, **side.stats()})

    # ------------------------------------------------------------------ source partagee
    async def _on_search(self, st: dict):
        if "query" in st:
            self.pipe["query"], self.pipe["page"] = st["query"], st.get("page")
        if "github_pause" in st:
            self.pipe["github_pause"] = st["github_pause"]
        self.pipe["search_requests"] = self.search.requests if self.search else 0
        self.pipe["rate_remaining"] = self.search.rate_remaining if self.search else None
        await self.emit({"type": "search", "t": self.el(), **st})

    async def _on_web(self, evt: dict):
        evt = {**evt, "t": self.el()}
        self.feed.append(evt)
        await self.emit(evt)

    async def _source(self):
        search = asyncio.create_task(self._search_loop())
        profs = [asyncio.create_task(self._profile_worker()) for _ in range(PROFILE_WORKERS)]
        homes = [asyncio.create_task(self._home_worker()) for _ in range(HOME_WORKERS)]

        async def closer():
            await asyncio.gather(*profs, return_exceptions=True)
            for _ in homes:
                await self.q_sites.put(None)

        cl = asyncio.create_task(closer())
        homes_done = asyncio.gather(*homes, return_exceptions=True)
        enough = asyncio.create_task(self.enough.wait())
        try:
            await asyncio.wait([homes_done, enough], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in [search, *profs, cl, enough, *homes]:
                if not t.done():
                    t.cancel()
        self.t_source_done = self.el()
        self.fetcher.cancel_kind("accueil")  # pages d'accueil en trop : on n'attend pas le web pour rien
        self.pipe["exhausted"] = self.pipe["dispatched"] < self.target
        for side in self.sides.values():
            for _ in range(self.conc):
                side.q.put_nowait(None)
        await self.emit({"type": "source_done", "t": self.t_source_done, "dispatched": self.pipe["dispatched"],
                         "exhausted": self.pipe["exhausted"]})

    async def _search_loop(self):
        async for u, q in self.search.usernames():
            self.pipe["usernames"] += 1
            await self.q_users.put((u, q))
        for _ in range(PROFILE_WORKERS):
            await self.q_users.put(None)

    async def _profile_worker(self):
        while True:
            item = await self.q_users.get()
            if item is None:
                return
            u, q = item
            if u in self.seen.users:
                continue
            self.seen.users.add(u)
            try:
                p = await asyncio.to_thread(scrape_profile, u)
            except Exception:
                p = None
            if not p:
                self.pipe["empty"] += 1
                continue
            self.pipe["profiles"] += 1
            url = site_url(p)
            if not url:
                self.pipe["no_site"] += 1
                continue
            host = root_host(urlparse(url).hostname or "")
            if host in self.seen.hosts:
                self.pipe["dup"] += 1
                continue
            self.seen.hosts.add(host)
            self.pipe["with_site"] += 1
            p["source_query"] = q
            await self.q_sites.put((p, url, host))

    async def _home_worker(self):
        while True:
            item = await self.q_sites.get()
            if item is None:
                return
            p, url, host = item
            if self.pipe["dispatched"] >= self.target:
                self.enough.set()
                return
            self.home_inflight[host] = p.get("username")
            try:
                res, _ = await self.fetcher.get(url, "accueil")
            except asyncio.CancelledError:
                raise
            finally:
                if self.pipe["dispatched"] < self.target:
                    self.home_inflight.pop(host, None)
            if not res["ok"]:
                self.pipe["home_fail"] += 1
                continue
            self.pipe["home_ok"] += 1
            if self.pipe["dispatched"] >= self.target:
                self.enough.set()
                return
            await self._dispatch(self._make_lead(p, res, host))
            if self.pipe["dispatched"] >= self.target:
                self.enough.set()
                return

    def _make_lead(self, p: dict, res: dict, host: str) -> dict:
        page = res["page"]
        fhost = root_host(urlparse(res["final_url"]).hostname or "") or host
        gh = (p.get("email") or "").strip().lower()
        gh_emails = [gh] if gh and "@" in gh and valid_email(gh) else []
        home_emails = [e for e in page["emails"] if attributable(e, fhost) and e not in gh_emails]
        cands = {f"l{i + 1}": c for i, c in enumerate(pick_candidates(page["links"]))}
        lid = self.pipe["dispatched"] + 1
        return {"id": lid, "username": p.get("username"), "name": p.get("full_name") or "", "host": fhost,
                "url": res["final_url"], "tool": res["tool"], "fetch_ms": res["ms"], "query": p.get("source_query"),
                "title": page.get("title", ""), "signals": page.get("signals", {}), "cands": cands,
                "gh_emails": gh_emails, "home_emails": home_emails,
                "link_state": link_state(fhost, page, cands) if cands else None,
                "link_q": link_question(cands) if cands else None,
                "qual_state": qual_state(p, fhost, page, home_emails),
                "t_ready": self.el()}

    async def _dispatch(self, lead: dict):
        self.pipe["dispatched"] += 1
        self.leads[lead["id"]] = lead
        for side in self.sides.values():  # les deux files recoivent le lead au meme instant
            side.queued += 1
            side.q.put_nowait(lead)
        await self.emit({
            "type": "lead", "id": lead["id"], "t": lead["t_ready"], "host": lead["host"], "username": lead["username"],
            "name": lead["name"], "title": lead["title"][:80], "tool": lead["tool"], "fetch_ms": lead["fetch_ms"],
            "links": len(lead["cands"]), "signals": lead["signals"], "query": lead["query"],
            "gh_email": bool(lead["gh_emails"]), "home_emails": len(lead["home_emails"]),
            "state": lead["qual_state"], "link_options": {k: c["path"] for k, c in lead["cands"].items()}})

    # ------------------------------------------------------------------ decisions
    async def _model_worker(self, side: Side):
        while True:
            lead = await side.q.get()
            if lead is None:
                return
            side.queued -= 1
            side.inflight += 1
            try:
                await self._process(side, lead)
            except BudgetExceeded as e:
                self.error = f"Budget : {e}"
                self.stop.set()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(traceback.format_exc())
                side.done += 1
                row = {"lead": lead["id"], "model": side.name, "n": side.done, "host": lead["host"],
                       "error": f"{type(e).__name__} : {str(e)[:120]}", "verdict": None, "t": self.el()}
                side.results[lead["id"]] = row
                side.rows.append(row)
                await self.emit({"type": "lead_done", "row": row})
            finally:
                side.inflight -= 1

    def _account(self, side: Side, row: dict, r: dict):
        side.decisions += 1
        side.cost += r["cost"] or 0.0
        side.lat.append(r["latency_ms"])
        row["decisions"] += 1
        row["ms"] += r["latency_ms"]
        row["cost"] += r["cost"] or 0.0
        if not r["ok"]:
            side.failed_calls += 1

    async def _process(self, side: Side, lead: dict):
        m = side.name
        t_start = time.time()
        row = {"lead": lead["id"], "model": m, "host": lead["host"], "username": lead["username"],
               "name": lead["name"], "decisions": 0, "ms": 0.0, "cost": 0.0, "errors": [], "link": None,
               "verdict": None}
        # 1. quel lien ouvrir pour trouver un email ?
        link_emails = []
        if lead["cands"]:
            r = await decide(m, SCENE, lead["link_state"], lead["link_q"], tag="lien", retries=1)
            self._account(side, row, r)
            await self.emit({"type": "decision", "model": m, "lead": lead["id"], "tag": "lien", "ok": r["ok"],
                             "ms": r["latency_ms"], "cost": r["cost"], "error": r["error"],
                             "answers": r["answers"], "tokens": r.get("tokens"), "t": self.el()})
            if r["ok"]:
                key, p = top_choice(r["answers"].get("contact"))
                link = {"key": key, "p": p}
                c = lead["cands"].get(key) if key and key != "none" else None
                if c:
                    link["path"] = c["path"]
                    side.links_opened += 1
                    res, cached = await self.fetcher.get(c["url"], "contact", {"model": m, "lead": lead["id"]})
                    link.update(fetched=res["ok"], cached=cached, tool=res["tool"])
                    if res["ok"]:
                        link_emails = [e for e in res["page"]["emails"] if attributable(e, lead["host"])]
                    else:
                        link["error"] = (res["error"] or "page illisible")[:90]
                    link["emails"] = len(link_emails)
                row["link"] = link
            else:
                row["errors"].append(f"lien : {r['error']}")
        else:
            row["link"] = {"key": None, "skipped": True}
        # 2. qualification pour l'offre d'Alan
        r = await decide(m, SCENE, lead["qual_state"], QUAL_Q, tag="qualif", retries=1)
        self._account(side, row, r)
        await self.emit({"type": "decision", "model": m, "lead": lead["id"], "tag": "qualif", "ok": r["ok"],
                         "ms": r["latency_ms"], "cost": r["cost"], "error": r["error"], "answers": r["answers"],
                         "tokens": r.get("tokens"), "t": self.el()})
        if r["ok"]:
            a = r["answers"]
            fit = float(a["fit"]["noul"])
            stage, stage_p = top_choice(a.get("stage"))
            pr = a.get("priority") or {}
            if pr.get("probabilities"):
                prio = int(max(pr["probabilities"].items(), key=lambda kv: kv[1])[0])
            else:
                prio = int(round(float(pr.get("score") or 0)))
            row.update(fit=fit, verdict=fit >= 0.5, stage=stage, stage_p=stage_p,
                       has_video=float(a["has_video"]["noul"]), priority=max(0, min(3, prio)),
                       priority_raw=pr.get("score"))
        else:
            row["errors"].append(f"qualification : {r['error']}")
        emails = list(dict.fromkeys(lead["gh_emails"] + lead["home_emails"] + link_emails))
        row["emails"] = emails
        row["email"] = emails[0] if emails else None
        # emails jamais vus de ce cote : ce sont eux qui glissent dans la liste a l'ecran
        row["new_emails"] = [{"email": e, "src": ("lien" if e in link_emails and e not in lead["gh_emails"]
                                                  and e not in lead["home_emails"] else
                                                  "github" if e in lead["gh_emails"] else "accueil")}
                             for e in emails if e not in side.email_set]
        side.email_set.update(emails)
        for ne in row["new_emails"]:
            side.email_list.append({**ne, "host": lead["host"], "lead": lead["id"]})
        row["email_src"] = ("github" if lead["gh_emails"] else "accueil" if lead["home_emails"]
                            else "lien" if link_emails else None)
        row["error"] = " · ".join(row.pop("errors"))[:200] or None
        row["lead_ms"] = round((time.time() - t_start) * 1000, 1)
        row["t"] = self.el()
        row["cost"] = round(row["cost"], 6)
        row["ms"] = round(row["ms"], 1)
        side.done += 1
        row["n"] = side.done
        side.lead_ms.append(row["lead_ms"])
        if emails:
            side.emails += 1
            if row["email_src"] == "lien":
                side.emails_link += 1
        if row["verdict"]:
            side.oui += 1
        side.results[lead["id"]] = row
        side.rows.append(row)
        await self.emit({"type": "lead_done", "row": row})
        await self._check_agreement(lead["id"])

    async def _check_agreement(self, lid: int):
        a, b = self.sides["jev"].results.get(lid), self.sides["opus"].results.get(lid)
        if not a or not b or a.get("verdict") is None or b.get("verdict") is None:
            return
        same = a["verdict"] == b["verdict"]
        self.agree["n"] += 1
        self.agree["verdict"] += int(same)
        self.agree["stage"] += int(a.get("stage") == b.get("stage"))
        self.agree["prio"] += int(a.get("priority") == b.get("priority"))
        la, lb = (a.get("link") or {}).get("key"), (b.get("link") or {}).get("key")
        if la and lb:
            self.agree["link_n"] += 1
            self.agree["link"] += int(la == lb)
        self.agree_flags[lid] = same
        if not same:
            self.disagreements.append({
                "lead": lid, "host": a["host"], "email": a.get("email") or b.get("email"),
                "jev": {"fit": a["fit"], "stage": a["stage"], "priority": a["priority"]},
                "opus": {"fit": b["fit"], "stage": b["stage"], "priority": b["priority"]}})
        await self.emit({"type": "agree", "lead": lid, "agree": same, "stage_agree": a.get("stage") == b.get("stage")})

    # ------------------------------------------------------------------ bilan
    def _summary(self) -> dict:
        out = {"target": self.target, "dispatched": self.pipe["dispatched"], "exhausted": self.pipe["exhausted"],
               "source_done_s": self.t_source_done, "models": {}, "pipe": {**self.pipe,
                                                                         "fetch": dict(self.fetcher.stats)}}
        for m, s in self.sides.items():
            st = s.stats()
            st["time_s"] = s.t_done
            st["oui_pct"] = (s.oui / s.done) if s.done else None
            st["emails_pct"] = (s.emails / s.done) if s.done else None
            st["email_count"] = len(s.email_set)
            st["stages"] = {}
            for r in s.results.values():
                if r.get("stage"):
                    st["stages"][r["stage"]] = st["stages"].get(r["stage"], 0) + 1
            out["models"][m] = st
        a = self.agree
        out["agreement"] = {"n": a["n"], "verdict": (a["verdict"] / a["n"]) if a["n"] else None,
                            "stage": (a["stage"] / a["n"]) if a["n"] else None,
                            "priority": (a["prio"] / a["n"]) if a["n"] else None,
                            "link": (a["link"] / a["link_n"]) if a["link_n"] else None, "link_n": a["link_n"]}
        out["disagreements"] = self.disagreements[:40]
        j, o = out["models"]["jev"], out["models"]["opus"]
        out["ratio"] = {"time": (o["time_s"] / j["time_s"]) if j["time_s"] else None,
                        "cost": (o["cost"] / j["cost"]) if j["cost"] else None,
                        "latency": (o["lat_med_ms"] / j["lat_med_ms"]) if j["lat_med_ms"] and o["lat_med_ms"] else None}
        out["session"] = str(self.session_path) if self.session_path else None
        return out

    def snapshot(self) -> dict:
        rows = {m: list(s.rows) for m, s in self.sides.items()}
        flags = {lid: v for lid, v in self.agree_flags.items()
                 if any(r["lead"] == lid for rs in rows.values() for r in rs)}
        return {"stats": self.stats(), "rows": rows, "feed": list(self.feed), "agree_flags": flags,
                "emails": {m: list(s.email_list) for m, s in self.sides.items()},
                "summary": self.summary, "error": self.error}


__all__ = ["Race", "STAGES"]
