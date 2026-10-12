"""Diffusion en direct vers les pages (WebSocket) + enregistrement des sessions.

Chaque scene publie des evenements JSON {type: ..., ...} sur son canal. Les pages connectees a
/ws/<scene> les recoivent tous. Un enregistreur ecrit chaque evenement dans data/sessions/ pour que le
motion design puisse retracer ensuite les vraies donnees.
"""
import asyncio
import json
import time
from datetime import datetime

from fastapi import WebSocket

from .config import SESSIONS


class Recorder:
    def __init__(self, scene: str):
        self.scene = scene
        self.path = None
        self._f = None

    def start(self, meta: dict | None = None):
        self.stop()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = SESSIONS / f"{self.scene}_{stamp}.jsonl"
        self._f = open(self.path, "a", encoding="utf-8")
        self.write({"type": "session_start", "meta": meta or {}})
        return self.path

    def write(self, event: dict):
        if self._f:
            self._f.write(json.dumps({"ts": time.time(), **event}, ensure_ascii=False, default=str) + "\n")
            self._f.flush()

    def stop(self):
        if self._f:
            self.write({"type": "session_stop"})
            self._f.close()
        self._f = None


class Hub:
    def __init__(self):
        self.subs: dict[str, set[WebSocket]] = {}
        self.recorders: dict[str, Recorder] = {}

    def recorder(self, scene: str) -> Recorder:
        if scene not in self.recorders:
            self.recorders[scene] = Recorder(scene)
        return self.recorders[scene]

    def add(self, scene: str, ws: WebSocket):
        self.subs.setdefault(scene, set()).add(ws)

    def remove(self, scene: str, ws: WebSocket):
        self.subs.get(scene, set()).discard(ws)

    async def publish(self, scene: str, event: dict, record: bool = True):
        if record:
            self.recorder(scene).write(event)
        targets = list(self.subs.get(scene, ()))
        if not targets:
            return
        msg = json.dumps(event, ensure_ascii=False, default=str)
        dead = []

        async def send(ws):
            try:
                await asyncio.wait_for(ws.send_text(msg), timeout=2.0)
            except Exception:
                dead.append(ws)

        await asyncio.gather(*(send(ws) for ws in targets))
        for ws in dead:
            self.remove(scene, ws)


HUB = Hub()


class SceneBase:
    """Interface commune des scenes. Chaque scene vit dans app/scenes/<nom>/__init__.py et expose
    `scene` (instance de SceneBase) et, si besoin, `router` (fastapi.APIRouter monte sous /api/<nom>)."""
    name = "base"
    title = "Scene"

    async def startup(self):
        pass

    async def shutdown(self):
        pass

    def snapshot(self) -> dict:
        """Etat complet envoye a une page qui vient de se connecter."""
        return {}

    async def handle(self, cmd: dict) -> dict | None:
        """Commande envoyee par la page (start, stop, reset, config...)."""
        return None

    async def emit(self, event: dict, record: bool = True):
        await HUB.publish(self.name, event, record=record)
