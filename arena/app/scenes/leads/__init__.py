"""Scene « La course aux 100 leads » : Jev, Laya et Opus sur le meme flux de vrais leads (GitHub + Scout,
Lightpanda / Crawl4AI). Page : /leads. API : /api/leads/... (state, start, reset, forget, summary, export, linkedin)."""
import asyncio
import re

import httpx
from fastapi import APIRouter

from ...core.config import GITHUB_TOKEN
from ...core.hub import SceneBase
from ...core.ledger import LEDGER_OBJ
from .questions import STAGES
from .race import SCENE, Race
from .scout import SeenStore

GH_HEADERS = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {})}

TARGETS = (5, 10, 25, 50, 100)
CONCS = (4, 8, 12, 16)


class LeadsScene(SceneBase):
    name = SCENE
    title = "La course aux 100 leads"

    def __init__(self):
        self.seen = SeenStore()
        self.race: Race | None = None
        self.task: asyncio.Task | None = None
        self.target = 100
        self.conc = 8

    async def shutdown(self):
        await self._stop()

    def busy(self) -> bool:
        return bool(self.task and not self.task.done())

    async def _stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        self.task = None

    def config(self) -> dict:
        led = LEDGER_OBJ.summary().get(SCENE, {})
        return {"target": self.target, "conc": self.conc, "targets": TARGETS, "concs": CONCS,
                "seen": self.seen.info(), "stages": STAGES,
                "budget": {"spent": led.get("spent", 0.0), "cap": LEDGER_OBJ.cap(SCENE)}}

    def snapshot(self) -> dict:
        """Une page qui s'ouvre est toujours vierge : on ne lui montre une course que si elle tourne en ce moment
        (rechargement en plein direct). Les courses finies restent dans /data/sessions et /api/leads/summary."""
        live = self.race is not None and self.race.phase == "running"
        snap = {"phase": "running" if live else "idle", "config": self.config()}
        if live:
            snap.update(self.race.snapshot())
        return snap

    async def start(self, target=None, conc=None) -> dict:
        if self.task and not self.task.done():
            return {"type": "error", "error": "une course tourne deja (R pour remettre a zero)"}
        if target:
            self.target = max(1, min(500, int(target)))
        if conc:
            self.conc = max(1, min(32, int(conc)))
        self.race = Race(self, self.target, self.conc)
        self.task = asyncio.create_task(self.race.run())
        return {"type": "ok", "started": True, "target": self.target, "conc": self.conc}

    async def reset(self) -> dict:
        await self._stop()
        self.race = None
        await self.emit({"type": "reset", "config": self.config()}, record=False)
        return {"type": "ok", "reset": True}

    async def handle(self, cmd: dict) -> dict | None:
        c = cmd.get("cmd")
        if c == "start":
            return await self.start(cmd.get("target"), cmd.get("conc"))
        if c == "reset":
            return await self.reset()
        if c == "config":
            if self.task and not self.task.done():
                return {"type": "error", "error": "course en cours"}
            if cmd.get("target"):
                self.target = max(1, min(500, int(cmd["target"])))
            if cmd.get("conc"):
                self.conc = max(1, min(32, int(cmd["conc"])))
            await self.emit({"type": "config", "config": self.config()}, record=False)
            return None
        if c == "forget":
            if self.task and not self.task.done():
                return {"type": "error", "error": "course en cours"}
            self.seen.reset()
            await self.emit({"type": "config", "config": self.config()}, record=False)
            return {"type": "ok", "forgot": True}
        return None


scene = LeadsScene()
router = APIRouter()


@router.get("/state")
async def state():
    return scene.snapshot()


@router.post("/start")
async def start(target: int | None = None, conc: int | None = None):
    return await scene.start(target, conc)


@router.post("/reset")
async def reset():
    return await scene.reset()


@router.post("/forget")
async def forget():
    """Oublie les profils deja vus (les prochaines courses pourront les reprendre)."""
    if scene.task and not scene.task.done():
        return {"type": "error", "error": "course en cours"}
    scene.seen.reset()
    return {"type": "ok", "seen": scene.seen.info()}


@router.get("/summary")
async def summary():
    return (scene.race.summary if scene.race else None) or {}


# ------------------------------------------------------------------ export Excel (bouton de la page)
LEAD_KEYS = ("id", "username", "name", "host", "url", "title", "query", "signals", "tool", "t_ready")


@router.get("/export")
async def export():
    """Toute la derniere course (en cours ou finie) : leads, reponse de chaque modele, bilan. Donnees non masquees :
    le classeur sert a Alan pour prospecter, il ne doit pas etre publie."""
    r = scene.race
    if r is None:
        return {"leads": {}, "results": {}, "summary": None}
    leads = {lid: {**{k: L.get(k) for k in LEAD_KEYS}, "state": L.get("qual_state")} for lid, L in r.leads.items()}
    return {"t0": r.t0, "phase": r.phase, "models": list(r.sides), "leads": leads,
            "results": {m: s.results for m, s in r.sides.items()}, "agree_flags": r.agree_flags,
            "summary": r.summary, "stats": r.stats()}


LI_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|company)/[A-Za-z0-9_%\-.]+/?", re.I)


async def _linkedin_github(cli: httpx.AsyncClient, username: str):
    try:
        r = await cli.get(f"https://api.github.com/users/{username}/social_accounts", headers=GH_HEADERS, timeout=10)
        if r.status_code == 200:
            for a in r.json():
                if a.get("provider") == "linkedin" or "linkedin.com" in (a.get("url") or ""):
                    return a["url"], "profil GitHub"
    except Exception:
        pass
    return None


async def _linkedin_site(cli: httpx.AsyncClient, host: str):
    for url in (f"https://{host}/", f"http://{host}/"):
        try:
            r = await cli.get(url, timeout=8, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
            found = [u for u in LI_RE.findall(r.text) if not re.search(r"linkedin\.com/(company/linkedin|in/share)", u, re.I)]
            return (found[0].rstrip("/"), "site") if found else None
        except Exception:
            continue
    return None


@router.post("/linkedin")
async def linkedin(body: dict):
    """LinkedIn de chaque lead : reseaux declares sur le profil GitHub, sinon lien linkedin.com trouve sur le site."""
    items = (body or {}).get("items") or []
    sem = asyncio.Semaphore(12)
    found = {}
    async with httpx.AsyncClient() as cli:
        async def one(it):
            async with sem:
                res = None
                if it.get("username"):
                    res = await _linkedin_github(cli, it["username"])
                if not res and it.get("host"):
                    res = await _linkedin_site(cli, it["host"])
                if res:
                    found[str(it.get("id"))] = {"url": res[0], "src": res[1]}
        await asyncio.gather(*(one(it) for it in items[:300]))
    return {"found": found, "checked": min(len(items), 300)}
