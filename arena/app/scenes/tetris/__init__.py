"""Scene Tetris : Jev (a gauche) contre Claude Opus 5.5 (a droite), memes pieces, chute de plus en plus rapide.

La logique du jeu tourne dans la page (portage du test uc5 de septembre) : a chaque nouvelle piece, la page
calcule les placements possibles, en garde 6 (front de Pareto + pieges domines) et envoie la question a
choix A-F ici. On relaie la question au vrai modele via app.core.deciders (la cle OpenRouter reste cote
serveur) et on renvoie la reponse telle quelle. Une reponse arrivee apres l'atterrissage de la piece est
perdue (la piece tombe la ou elle est apparue) : c'est la page qui l'applique, pareil pour les deux couloirs.

Transport : la page envoie ses decisions par le WebSocket /ws/tetris ({"cmd":"decide", rid, page, lane, ...}).
Chaque decision tourne dans sa propre tache, la reponse revient en evenement {"type":"decided", rid, page,
result}. Pourquoi pas fetch : Chrome limite a 6 connexions HTTP/1.1 par hote, et une file de reponses
d'Opus en retard aurait retarde les requetes de Jev (biais injuste). Les routes HTTP restent disponibles.

  POST /api/tetris/decide {lane: "jev"|"opus", state, questions, tag, piece?}  -> dict de decide()
  POST /api/tetris/start  {meta}        -> demarre l'enregistrement data/sessions/tetris_<date>.jsonl
  POST /api/tetris/event  {...}         -> evenement de course (piece posee, game over...) enregistre et diffuse
  POST /api/tetris/save   {name, data}  -> ecrit data/sessions/tetris_<name>.json et arrete l'enregistrement
  GET  /api/tetris/spent                -> depense cumulee de la scene (registre) et plafond
"""
import asyncio
import json
import re
import time

from fastapi import APIRouter, Body

from ...core.config import SESSIONS
from ...core.deciders import BudgetExceeded, MODELS, decide
from ...core.hub import HUB, SceneBase
from ...core.ledger import LEDGER_OBJ

LANES = ("jev", "opus")


async def run_decision(lane: str, state, questions: dict, tag: str) -> dict:
    """Une vraie decision (Jev ou Opus). Ne leve jamais : le depassement de budget devient une erreur."""
    if lane not in LANES:
        return {"ok": False, "model": lane, "error": f"couloir inconnu : {lane}", "latency_ms": 0, "cost": 0.0,
                "answers": {}}
    try:
        return await decide(lane, "tetris", state, questions or {}, tag=tag)
    except BudgetExceeded as e:
        return {"model": lane, "model_id": MODELS[lane]["model_id"], "ok": False, "answers": {}, "latency_ms": 0,
                "cost": 0.0, "error": f"budget : {e}", "budget_exceeded": True, "raw": None, "tag": tag}
    except Exception as e:  # ne devrait pas arriver (decide ne leve que BudgetExceeded)
        return {"model": lane, "ok": False, "answers": {}, "latency_ms": 0, "cost": 0.0,
                "error": f"erreur serveur : {str(e)[:200]}", "raw": None, "tag": tag}


class TetrisScene(SceneBase):
    name = "tetris"
    title = "Tetris"

    def __init__(self):
        self.race = None      # meta de la course en cours (enregistree)
        self.last = {}        # derniere decision par couloir
        self._tasks: set[asyncio.Task] = set()

    def snapshot(self) -> dict:
        led = LEDGER_OBJ.summary().get(self.name, {})
        return {"race": self.race, "last": self.last, "spent": led.get("spent", 0.0), "cap": LEDGER_OBJ.cap(self.name)}

    async def shutdown(self):
        for t in list(self._tasks):
            t.cancel()

    def _decision_event(self, lane, tag, piece, out):
        return {"type": "decision", "lane": lane, "tag": tag, "piece": piece, "ok": out.get("ok"),
                "latency_ms": out.get("latency_ms"), "cost": out.get("cost"), "error": out.get("error"),
                "answers": out.get("answers")}

    async def _decide_ws(self, cmd: dict):
        lane, tag = cmd.get("lane"), str(cmd.get("tag") or "tetris")[:60]
        out = await run_decision(lane, cmd.get("state"), cmd.get("questions"), tag)
        # Reponse a la page (non enregistree : l'evenement "decision" compact l'est)
        await self.emit({"type": "decided", "rid": cmd.get("rid"), "page": cmd.get("page"), "result": out},
                        record=False)
        if not tag.endswith("warmup"):
            ev = self._decision_event(lane, tag, cmd.get("piece"), out)
            self.last[lane] = ev
            if self.race is not None:
                HUB.recorder(self.name).write(ev)

    async def start_race(self, meta: dict):
        self.race = {"started": time.time(), **(meta or {})}
        path = HUB.recorder(self.name).start(self.race)
        return path

    async def handle(self, cmd: dict):
        c = cmd.get("cmd")
        if c == "decide":
            t = asyncio.create_task(self._decide_ws(cmd))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
            return None
        if c == "event":
            ev = {k: v for k, v in cmd.items() if k not in ("cmd", "type")}
            if self.race is not None:
                HUB.recorder(self.name).write({"type": str(cmd.get("type") or "event")[:40], **ev})
            return None
        if c == "start":
            await self.start_race(cmd.get("meta") or {})
            return None
        if c == "ping":
            return {"type": "pong", "ts": time.time()}
        return None


scene = TetrisScene()
router = APIRouter()


@router.post("/decide")
async def api_decide(body: dict = Body(...)):
    lane, tag = body.get("lane"), str(body.get("tag") or "tetris")[:60]
    out = await run_decision(lane, body.get("state"), body.get("questions"), tag)
    if not tag.endswith("warmup") and lane in LANES:
        ev = scene._decision_event(lane, tag, body.get("piece"), out)
        scene.last[lane] = ev
        await scene.emit(ev, record=scene.race is not None)
    return out


@router.post("/start")
async def api_start(body: dict = Body(default={})):
    path = await scene.start_race(body.get("meta") or {})
    return {"ok": True, "recording": str(path)}


@router.post("/event")
async def api_event(body: dict = Body(...)):
    ev = {k: v for k, v in body.items() if k != "type"}
    await scene.emit({"type": str(body.get("type") or "event")[:40], **ev}, record=scene.race is not None)
    return {"ok": True}


@router.post("/save")
async def api_save(body: dict = Body(...)):
    name = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("name") or "course"))[:80] or "course"
    path = SESSIONS / f"tetris_{name}.json"
    path.write_text(json.dumps(body.get("data"), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if scene.race is not None:
        HUB.recorder(scene.name).write({"type": "race_end", "file": path.name})
        HUB.recorder(scene.name).stop()
        scene.race = None
    return {"ok": True, "path": str(path)}


@router.get("/spent")
async def api_spent():
    led = LEDGER_OBJ.summary().get(scene.name, {})
    return {"spent": led.get("spent", 0.0), "calls": led.get("calls", 0), "cap": LEDGER_OBJ.cap(scene.name)}
