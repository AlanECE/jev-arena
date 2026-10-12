"""Jev Arena : serveur FastAPI. Pages statiques + WebSocket par scene + API.

Scenes chargees automatiquement depuis app/scenes/<nom>/ (une scene cassee n'empeche pas les autres de
tourner : son erreur s'affiche sur la page d'accueil).
"""
import asyncio
import importlib
import json
import os
import pkgutil
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core import deciders
from .core.hub import HUB
from .core.ledger import LEDGER_OBJ

APP_DIR = Path(__file__).resolve().parent
STATIC = APP_DIR / "static"
SCENES: dict = {}
SCENE_ERRORS: dict = {}


def load_scenes():
    from . import scenes as pkg
    only = {s.strip() for s in os.environ.get("ARENA_SCENES", "").split(",") if s.strip()}
    for m in pkgutil.iter_modules(pkg.__path__):
        if not m.ispkg or (only and m.name not in only):
            continue
        try:
            mod = importlib.import_module(f"app.scenes.{m.name}")
            sc = getattr(mod, "scene", None)
            if sc is None:
                continue
            SCENES[sc.name] = (sc, getattr(mod, "router", None))
        except Exception:
            SCENE_ERRORS[m.name] = traceback.format_exc()[-1500:]


load_scenes()


@asynccontextmanager
async def lifespan(app):
    for name, (sc, _) in SCENES.items():
        try:
            await sc.startup()
        except Exception:
            SCENE_ERRORS[name] = traceback.format_exc()[-1500:]
    yield
    for sc, _ in SCENES.values():
        try:
            await sc.shutdown()
        except Exception:
            pass
    for rec in HUB.recorders.values():
        rec.stop()


app = FastAPI(title="Jev Arena", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

for name, (sc, router) in SCENES.items():
    if router is not None:
        app.include_router(router, prefix=f"/api/{name}")


@app.get("/")
async def home():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
async def status():
    return {"scenes": {n: sc.title for n, (sc, _) in SCENES.items()}, "errors": SCENE_ERRORS,
            "ledger": LEDGER_OBJ.summary(), "models": deciders.MODELS}


@app.get("/api/ledger")
async def ledger():
    return LEDGER_OBJ.summary()


@app.post("/api/probe")
async def probe():
    """Verifie que Jev et Opus repondent (2 vraies decisions, ~0,003 $)."""
    q = {"up": {"type": "noul", "instructions": "Is `x` greater than 10?"}}
    st = {"x": 12}
    j, o = await asyncio.gather(deciders.decide("jev", "probe", st, q), deciders.decide("opus", "probe", st, q))
    return {"jev": j, "opus": o}


@app.websocket("/ws/{scene_name}")
async def ws_endpoint(ws: WebSocket, scene_name: str):
    await ws.accept()
    if scene_name not in SCENES:
        await ws.send_text(json.dumps({"type": "error", "error": f"scene inconnue : {scene_name}",
                                       "detail": SCENE_ERRORS.get(scene_name)}))
        await ws.close()
        return
    sc, _ = SCENES[scene_name]
    HUB.add(scene_name, ws)
    try:
        await ws.send_text(json.dumps({"type": "snapshot", "data": sc.snapshot(),
                                       "models": deciders.MODELS}, default=str))
        while True:
            raw = await ws.receive_text()
            try:
                cmd = json.loads(raw)
            except Exception:
                continue
            try:
                res = await sc.handle(cmd)
            except Exception as e:
                res = {"type": "error", "error": str(e)[:300]}
            if res:
                await ws.send_text(json.dumps(res, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        HUB.remove(scene_name, ws)


@app.get("/{scene_name}")
async def scene_page(scene_name: str):
    page = STATIC / scene_name / "index.html"
    if page.exists():
        return FileResponse(page)
    return JSONResponse({"error": "page inconnue"}, status_code=404)
