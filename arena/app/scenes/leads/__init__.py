"""Scene « La course aux 100 leads » : Jev contre Opus sur le meme flux de vrais leads (GitHub + Scout,
Lightpanda / Crawl4AI). Page : /leads. API : /api/leads/... (state, start, reset, forget, summary)."""
import asyncio

from fastapi import APIRouter

from ...core.hub import SceneBase
from ...core.ledger import LEDGER_OBJ
from .questions import STAGES
from .race import SCENE, Race
from .scout import SeenStore

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
