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
            "ledger": LEDGER_OBJ.summary(), "models": deciders.MODELS, "laya": deciders.laya_info()}


@app.get("/api/ledger")
async def ledger():
    return LEDGER_OBJ.summary()


@app.post("/api/probe")
async def probe():
    """Verifie que Jev, Laya et Opus repondent (3 vraies decisions, ~0,003 $)."""
    q = {"up": {"type": "noul", "instructions": "Is `x` greater than 10?"}}
    st = {"x": 12}
    j, l, o = await asyncio.gather(deciders.decide("jev", "probe", st, q), deciders.decide("laya", "probe", st, q),
                                   deciders.decide("opus", "probe", st, q))
    return {"jev": j, "laya": l, "opus": o}


# ---------------------------------------------------------------------------------------------- Laya
async def _laya_check(backend: str) -> dict:
    """Une vraie decision Laya (gratuite) sur le backend demande, sans changer le backend actif."""
    r = await deciders.laya_decide("probe", {"x": 12}, {"up": {"type": "choice", "instructions": "Is `x` greater than 10?",
                                                           "criteria": {"yes": "x is greater than 10", "no": "x is 10 or less"}}},
                                   backend=backend)
    return {"ok": r["ok"], "latency_ms": r["latency_ms"], "error": r["error"],
            "answer": (r["answers"].get("up") or {}).get("choice") if r["ok"] else None}


@app.get("/api/laya")
async def laya_status(check: int = 0):
    out = deciders.laya_info()
    if check:
        loc, hos = await asyncio.gather(_laya_check("local"), _laya_check("hosted"))
        out["check"] = {"local": loc, "hosted": hos}
    return out


@app.post("/api/laya/compare")
async def laya_compare(body: dict):
    """Page /laya : la meme decision envoyee n fois a Laya local, Laya heberge (Hugging Face) et Jev (reference).
    Chaque cible enchaine ses appels un par un (latence propre) ; les cibles tournent en parallele."""
    state, questions = body.get("state"), body.get("questions")
    if not isinstance(questions, dict) or not questions:
        return JSONResponse({"error": "questions manquantes (un objet JSON au format de Jev)"}, 400)
    n = max(1, min(30, int(body.get("n") or 1)))
    targets = [t for t in (body.get("targets") or ["local", "hosted", "jev"]) if t in ("local", "hosted", "jev")]

    async def run(t):
        out = []
        for _ in range(n):
            if t == "jev":
                r = await deciders.decide("jev", "probe", state, questions)
            else:
                r = await deciders.laya_decide("probe", state, questions, backend=t)
            out.append({"ok": r["ok"], "latency_ms": r["latency_ms"], "cost": r.get("cost") or 0.0,
                        "error": r["error"], "answers": r["answers"]})
        return t, out

    try:
        res = dict(await asyncio.gather(*(run(t) for t in targets)))
    except deciders.BudgetExceeded as e:
        return JSONResponse({"error": str(e)}, 402)
    return {"n": n, "results": res, "laya": deciders.laya_info()}


@app.post("/api/laya/backend")
async def laya_backend(body: dict):
    busy = [n for n, (sc, _) in SCENES.items() if getattr(sc, "busy", lambda: False)()]
    if busy:
        return JSONResponse({"error": f"une course tourne ({', '.join(busy)}) : arrête-la avant de changer Laya"}, 409)
    try:
        info = deciders.set_laya_backend(str(body.get("backend")))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)
    for name in SCENES:
        await HUB.publish(name, {"type": "laya", "laya": info}, record=False)
    return info


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
                                       "models": deciders.MODELS, "laya": deciders.laya_info()}, default=str))
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
