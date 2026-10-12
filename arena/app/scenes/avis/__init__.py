"""Scene 04 · « Le tri en direct » (avis clients).

De vrais avis clients sont recuperes en direct (Trustpilot FR via Lightpanda, App Store FR via le flux RSS
public d'Apple, ou Google Play FR via google-play-scraper), equilibres par note, puis Jev et Claude Opus 5.5
lisent EXACTEMENT les memes avis, dans le meme
ordre, avec la meme concurrence. Un seul appel par avis et par modele, 4 questions dans le meme appel :
note predite (score 1-5), sujet (choice), risque de depart (noul), reponse urgente (noul).
La vraie note laissee par le client est la verite terrain : la justesse est objective.
"""
import asyncio
import statistics
import time

from fastapi import APIRouter
from pydantic import BaseModel

from ...core import deciders
from ...core.hub import HUB, SceneBase
from ...core.ledger import LEDGER_OBJ, BudgetExceeded
from .sources import PRESET_BY_ID, PRESETS, balance, fetch_plan, fetch_reviews

SCENE = "avis"
MODELS = ("jev", "opus")
STAR_LEVELS = [
    "1 star: very negative, angry, furious, scam, disaster, never again",
    "2 stars: negative, disappointed, the main experience was bad",
    "3 stars: mixed or neutral, both good and bad points, or just factual",
    "4 stars: positive, satisfied, good experience with minor reservations at most",
    "5 stars: very positive, enthusiastic, perfect, highly recommends",
]
TOPICS = {
    "bug": "Bug or technical problem: app or website not working, login error, crash, outage",
    "price_billing": "Price, billing, invoice, payment, refund, unexpected charge, value for money",
    "customer_service": "Customer service, support, advisors, hotline, no answer, staff attitude",
    "delivery_usage": "Delivery, order, shipping, or the everyday use and quality of the product or service",
    "missing_feature": "Missing feature or feature request: something the customer wishes the product could do",
    "praise": "Praise, compliments, general satisfaction without a specific problem",
}
TOPIC_LABELS = {"bug": "Bug / technique", "price_billing": "Prix / facturation", "customer_service": "Service client",
                "delivery_usage": "Livraison / usage", "missing_feature": "Fonction manquante", "praise": "Éloge"}
QUESTIONS = {
    "stars": {"type": "score",
              "instructions": "How many stars (1 to 5) did the customer give to `brand` in `review`?",
              "criteria": STAR_LEVELS},
    "topic": {"type": "choice", "instructions": "Main topic of `review`.", "criteria": TOPICS},
    "churn_risk": {"type": "noul",
                   "instructions": "Is the customer who wrote `review` likely to stop using or leave `brand`?"},
    "urgent_reply": {"type": "noul",
                     "instructions": "Does `review` need an urgent public reply from `brand` (serious unresolved "
                                     "problem, money lost, reputation risk)?"},
}
MAX_CHARS = 1200          # texte envoye aux modeles (comme le test de septembre)
TARGETS = (10, 30, 50, 100, 200)
CONCS = (4, 8, 16)


def state_of(r: dict, brand: str) -> dict:
    txt = r["text"]
    if len(txt) > MAX_CHARS:
        txt = txt[:MAX_CHARS] + "..."
    return {"brand": brand, "review": {"title": r["title"], "text": txt}}


def read_answer(res: dict) -> dict:
    a = res.get("answers") or {}
    s = (a.get("stars") or {}).get("score")
    pred = None
    if s is not None:
        pred = int(min(4, max(0, round(float(s))))) + 1
    topic, tconf = deciders.top_choice(a.get("topic") or {})
    churn = (a.get("churn_risk") or {}).get("noul")
    urgent = (a.get("urgent_reply") or {}).get("noul")
    return {"pred": pred, "score": s, "topic": topic, "topic_p": tconf, "churn": churn, "urgent": urgent,
            "probs": (a.get("stars") or {}).get("probabilities")}


def new_stats() -> dict:
    return {"done": 0, "ok": 0, "err": 0, "exact": 0, "near": 0, "cost": 0.0, "lat": [], "elapsed": 0.0,
            "finished": False, "pred": {str(s): 0 for s in range(1, 6)}, "topics": {k: 0 for k in TOPICS},
            "churn": 0, "urgent": 0, "inflight": 0}


def public_stats(st: dict) -> dict:
    lat = st["lat"]
    ok = st["ok"]
    el = st["elapsed"]
    return {"done": st["done"], "ok": ok, "err": st["err"], "exact": st["exact"], "near": st["near"],
            "exact_pct": st["exact"] / ok if ok else None,
            "within1_pct": (st["exact"] + st["near"]) / ok if ok else None,
            "cost": round(st["cost"], 6), "lat_med": statistics.median(lat) if lat else None,
            "lat_last": lat[-1] if lat else None, "elapsed": round(el, 3),
            "rps": st["done"] / el if el > 0 else None, "finished": st["finished"], "pred": st["pred"],
            "topics": st["topics"], "churn": st["churn"], "urgent": st["urgent"], "inflight": st["inflight"]}


class AvisScene(SceneBase):
    name = SCENE
    title = "Le tri en direct"

    def __init__(self):
        self.tasks: list[asyncio.Task] = []
        self.main_task: asyncio.Task | None = None
        self.emitter: asyncio.Task | None = None
        self.out: asyncio.Queue | None = None
        self._reset_state()

    # ------------------------------------------------------------------ etat
    def _reset_state(self):
        self.phase = "idle"            # idle -> fetch -> ready -> race -> done (ou error)
        self.config = {"preset": "tp-free", "target": 100, "conc": 8}
        self.preset = PRESET_BY_ID["tp-free"]
        self.reviews: list[dict] = []
        self.fetch_log: list[dict] = []
        self.fetch_plan: list[dict] = []
        self.fetch_info: dict = {}
        self.answers = {m: {} for m in MODELS}
        self.inflight = {m: set() for m in MODELS}
        self.stats = {m: new_stats() for m in MODELS}
        self.t0 = None
        self.summary = None
        self.error = None
        self.run_id = 0

    def snapshot(self) -> dict:
        now = time.time()
        return {"phase": self.phase, "config": self.config, "preset": self.preset, "presets": PRESETS,
                "targets": TARGETS, "concs": CONCS, "topics": TOPIC_LABELS, "reviews": self.reviews,
                "fetch_log": self.fetch_log, "fetch_plan": self.fetch_plan, "fetch_info": self.fetch_info,
                "answers": {m: list(self.answers[m].values()) for m in MODELS},
                "inflight": {m: sorted(self.inflight[m]) for m in MODELS},
                "stats": {m: public_stats(self.stats[m]) for m in MODELS},
                "elapsed": (now - self.t0) if (self.t0 and self.phase == "race") else None,
                "summary": self.summary, "error": self.error, "budget": self.budget()}

    def budget(self) -> dict:
        return {"spent": round(LEDGER_OBJ.spent.get(SCENE, 0.0), 5), "cap": LEDGER_OBJ.cap(SCENE)}

    # ------------------------------------------------------------------ diffusion ordonnee, sans bloquer les workers
    def push(self, event: dict):
        if self.out is not None:
            self.out.put_nowait(event)

    async def _emit_loop(self, q: asyncio.Queue):
        while True:
            ev = await q.get()
            if ev is None:
                break
            try:
                await self.emit(ev)
            except Exception:
                pass

    def _start_emitter(self):
        self.out = asyncio.Queue()
        self.emitter = asyncio.create_task(self._emit_loop(self.out))

    async def _stop_emitter(self):
        if self.out is not None and self.emitter is not None:
            self.out.put_nowait(None)
            try:
                await asyncio.wait_for(self.emitter, 5)
            except Exception:
                self.emitter.cancel()
        self.out, self.emitter = None, None

    # ------------------------------------------------------------------ commandes
    async def handle(self, cmd: dict):
        c = cmd.get("cmd")
        if c == "config" and self.phase in ("idle", "done", "error"):
            self._apply_config(cmd)
            await self.emit({"type": "config", "config": self.config, "preset": self.preset,
                             "budget": self.budget()}, record=False)
            return None
        if c == "load":            # recuperer les avis sans lancer la course (touche C)
            return await self.start(cmd, race=False)
        if c == "start":           # Espace : recupere (si besoin) puis lance la course
            return await self.start(cmd, race=True)
        if c == "reset":
            await self.reset()
            return None
        if c == "snapshot":
            return {"type": "snapshot", "data": self.snapshot()}
        return None

    def _apply_config(self, cmd: dict):
        if cmd.get("preset") in PRESET_BY_ID:
            self.config["preset"] = cmd["preset"]
            self.preset = PRESET_BY_ID[cmd["preset"]]
        if cmd.get("target"):
            self.config["target"] = max(5, min(200, int(cmd["target"])))
        if cmd.get("conc"):
            self.config["conc"] = max(1, min(32, int(cmd["conc"])))

    async def start(self, cmd: dict, race: bool = True):
        if self.phase in ("fetch", "race"):
            return {"type": "error", "error": "une course est déjà en cours"}
        if self.phase == "ready" and race:
            if cmd.get("conc"):
                self.config["conc"] = max(1, min(32, int(cmd["conc"])))
            self.main_task = asyncio.create_task(self._race_only())
            return None
        if self.phase in ("done", "error", "ready"):
            await self.reset(silent=True)
        self._apply_config(cmd)
        self.main_task = asyncio.create_task(self._run(race))
        return None

    async def reset(self, silent: bool = False):
        for t in self.tasks:
            t.cancel()
        if self.main_task and not self.main_task.done():
            self.main_task.cancel()
            try:
                await self.main_task
            except BaseException:
                pass
        self.tasks = []
        await self._stop_emitter()
        HUB.recorder(SCENE).stop()
        cfg, preset = dict(self.config), self.preset
        self._reset_state()
        self.config, self.preset = cfg, preset
        if not silent:
            await self.emit({"type": "reset", "data": self.snapshot()}, record=False)

    async def shutdown(self):
        await self.reset(silent=True)

    # ------------------------------------------------------------------ course
    async def _run(self, race: bool):
        self._start_emitter()
        self.run_id += 1
        try:
            HUB.recorder(SCENE).start({"preset": self.preset, "config": self.config, "questions": QUESTIONS,
                                       "models": deciders.MODELS})
            ok = await self._fetch()
            if ok and race:
                await self._race()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # erreur reelle : affichee telle quelle
            self.phase, self.error = "error", f"{type(e).__name__} : {str(e)[:300]}"
            self.push({"type": "phase", "phase": "error", "error": self.error})
        finally:
            if self.phase != "ready":
                await self._stop_emitter()
                HUB.recorder(SCENE).stop()

    async def _race_only(self):
        if self.out is None:
            self._start_emitter()
        try:
            await self._race()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.phase, self.error = "error", f"{type(e).__name__} : {str(e)[:300]}"
            self.push({"type": "phase", "phase": "error", "error": self.error})
        finally:
            await self._stop_emitter()
            HUB.recorder(SCENE).stop()

    async def _fetch(self) -> bool:
        self.phase = "fetch"
        p, target = self.preset, self.config["target"]
        self.fetch_plan = fetch_plan(p, target)
        self.push({"type": "phase", "phase": "fetch", "config": self.config, "preset": p, "plan": self.fetch_plan})
        t = time.time()

        async def on_event(ev):
            ev = {**ev, "t": round(time.time() - t, 3)}
            self.fetch_log.append(ev)
            self.push({"type": "fetch_page", **ev})

        pool = await fetch_reviews(p, target, on_event)
        fetch_ms = round((time.time() - t) * 1000)
        picked = balance(pool, target)
        self.reviews = [{"i": i, "id": r["id"], "stars": r["stars"], "title": r["title"], "text": r["text"],
                         "author": r["author"], "date": r["date"], "version": r.get("version", "")}
                        for i, r in enumerate(picked)]
        by = {str(s): sum(1 for r in self.reviews if r["stars"] == s) for s in range(1, 6)}
        pool_by = {str(s): sum(1 for r in pool if r["stars"] == s) for s in range(1, 6)}
        pages_ok = sum(1 for e in self.fetch_log if e.get("kind") == "page" and e.get("ok"))
        pages = sum(1 for e in self.fetch_log if e.get("kind") == "page")
        tools = sorted({e["tool"] for e in self.fetch_log if e.get("kind") == "page" and e.get("ok")})
        self.fetch_info = {"ms": fetch_ms, "pool": len(pool), "pool_by": pool_by, "by_star": by, "n": len(self.reviews),
                           "pages": pages, "pages_ok": pages_ok, "tools": tools, "source": p["source"]}
        if not self.reviews:
            self.phase, self.error = "error", "aucun avis récupéré (voir le journal de récupération)"
            self.push({"type": "phase", "phase": "error", "error": self.error, "fetch_info": self.fetch_info})
            return False
        self.phase = "ready"
        self.push({"type": "reviews", "items": self.reviews, "fetch_info": self.fetch_info, "phase": "ready"})
        return True

    async def _race(self):
        n = len(self.reviews)
        conc = self.config["conc"]
        brand = self.preset["label"]
        self.answers = {m: {} for m in MODELS}
        self.inflight = {m: set() for m in MODELS}
        self.stats = {m: new_stats() for m in MODELS}
        self.summary, self.error = None, None
        self.phase = "race"
        self.t0 = time.time()
        self.push({"type": "race_start", "n": n, "conc": conc, "t0": self.t0, "phase": "race",
                   "budget": self.budget()})
        stop = asyncio.Event()

        async def worker(model: str, it):
            st = self.stats[model]
            for i in it:
                if stop.is_set():
                    return
                r = self.reviews[i]
                self.inflight[model].add(i)
                st["inflight"] = len(self.inflight[model])
                self.push({"type": "item_start", "model": model, "i": i})
                try:
                    res = await deciders.decide(model, SCENE, state_of(r, brand), QUESTIONS,
                                                tag=f"avis-{self.run_id}-{i}", retries=1)
                except BudgetExceeded as e:
                    self.inflight[model].discard(i)
                    stop.set()
                    self.error = str(e)
                    self.push({"type": "budget", "error": str(e), "budget": self.budget()})
                    return
                self.inflight[model].discard(i)
                now = time.time()
                a = read_answer(res) if res["ok"] else {}
                pred = a.get("pred")
                ok = bool(res["ok"] and pred is not None)
                verdict = None
                if ok:
                    d = abs(pred - r["stars"])
                    verdict = "exact" if d == 0 else ("near" if d == 1 else "off")
                st["done"] += 1
                st["cost"] += float(res.get("cost") or 0)
                st["lat"].append(res["latency_ms"])
                st["elapsed"] = now - self.t0
                st["inflight"] = len(self.inflight[model])
                if ok:
                    st["ok"] += 1
                    st["exact"] += verdict == "exact"
                    st["near"] += verdict == "near"
                    st["pred"][str(pred)] += 1
                    if a.get("topic") in st["topics"]:
                        st["topics"][a["topic"]] += 1
                    st["churn"] += (a.get("churn") or 0) >= 0.5
                    st["urgent"] += (a.get("urgent") or 0) >= 0.5
                else:
                    st["err"] += 1
                ans = {"model": model, "i": i, "ok": ok, "pred": pred, "truth": r["stars"], "verdict": verdict,
                       "topic": a.get("topic"), "topic_p": a.get("topic_p"), "churn": a.get("churn"),
                       "urgent": a.get("urgent"), "probs": a.get("probs"), "score": a.get("score"),
                       "latency_ms": res["latency_ms"], "cost": res.get("cost"),
                       "error": None if ok else (res.get("error") or "réponse sans note"),
                       "t": round(now - self.t0, 3), "model_id": res.get("model_id")}
                self.answers[model][i] = ans
                self.push({"type": "answer", **ans, "stats": public_stats(st), "budget": self.budget()})

        async def side(model: str):
            it = iter(range(n))          # meme ordre pour les deux, chaque modele a sa propre file
            ws = [asyncio.create_task(worker(model, it)) for _ in range(conc)]
            self.tasks.extend(ws)
            await asyncio.gather(*ws)
            st = self.stats[model]
            st["finished"] = True
            st["elapsed"] = (max((x["t"] for x in self.answers[model].values()), default=0.0))
            self.push({"type": "side_done", "model": model, "stats": public_stats(st)})

        await asyncio.gather(*(side(m) for m in MODELS))
        self.summary = self._summary(n, conc)
        self.phase = "done"
        self.push({"type": "done", "phase": "done", "summary": self.summary, "error": self.error,
                   "budget": self.budget()})

    def _summary(self, n: int, conc: int) -> dict:
        out = {"n": n, "conc": conc, "preset": self.preset, "fetch": self.fetch_info, "models": {}}
        for m in MODELS:
            st = self.stats[m]
            ps = public_stats(st)
            per = st["cost"] / st["done"] if st["done"] else None
            rps = ps["rps"]
            out["models"][m] = {**{k: ps[k] for k in ("done", "ok", "err", "exact_pct", "within1_pct", "cost",
                                                      "lat_med", "elapsed", "rps", "churn", "urgent")},
                                "cost_per_review": per,
                                "cost_10k_extrapolated": per * 10000 if per is not None else None,
                                "time_10k_extrapolated_s": 10000 / rps if rps else None}
        both = [i for i in range(n) if self.answers["jev"].get(i, {}).get("ok")
                and self.answers["opus"].get(i, {}).get("ok")]
        if both:
            J, O = self.answers["jev"], self.answers["opus"]
            out["agree"] = {"n": len(both),
                            "topic": sum(J[i]["topic"] == O[i]["topic"] for i in both) / len(both),
                            "stars": sum(J[i]["pred"] == O[i]["pred"] for i in both) / len(both)}
        mj, mo = out["models"]["jev"], out["models"]["opus"]
        if mj["cost_per_review"] and mo["cost_per_review"]:
            out["cost_ratio"] = mo["cost_per_review"] / mj["cost_per_review"]
        if mj["elapsed"] and mo["elapsed"]:
            out["speed_ratio"] = mo["elapsed"] / mj["elapsed"]
        return out


scene = AvisScene()
router = APIRouter()


class StartBody(BaseModel):
    preset: str | None = None
    target: int | None = None
    conc: int | None = None
    race: bool = True


@router.get("/presets")
async def presets():
    return {"presets": PRESETS, "targets": TARGETS, "concs": CONCS, "topics": TOPIC_LABELS}


@router.get("/state")
async def state():
    return scene.snapshot()


@router.post("/start")
async def start(body: StartBody):
    """Pour les essais sans page : lance une course (meme chemin que la touche Espace)."""
    res = await scene.start(body.model_dump(exclude_none=True), race=body.race)
    return res or {"ok": True, "phase": scene.phase}


@router.post("/reset")
async def reset():
    await scene.reset()
    return {"ok": True}
