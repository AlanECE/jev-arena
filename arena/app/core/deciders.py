"""Les trois decideurs de l'arene : Jev et Claude Opus 5.5 via OpenRouter, Laya en local ou sur Hugging Face.

- Jev (TypeSafe) : API Decisions d'OpenRouter, POST /api/alpha/decisions. Etat + questions typees
  -> reponses typees avec probabilites. Jev n'ecrit jamais de texte.
- Claude Opus 5.5 : chat completions OpenRouter. Il recoit EXACTEMENT le meme etat et les memes questions,
  et repond en JSON. Sa "confiance" est declaree par lui (ce n'est pas une probabilite calculee).

- Laya (Convai Innovations, open source) : meme requete que Jev ({state, questions} -> answers), servie par
  laya-serve dans le conteneur `laya` (GPU) ou par la demo gratuite de Laya sur Hugging Face. Cout : 0 $.

Les trois renvoient la meme forme :
  {model, model_id, ok, answers, latency_ms, cost, error, raw, t0, t1, tag}
  answers = {nom_question: {type, choice|noul|score, probabilities?, confidence?, declared?}}

Types de questions (format Jev) :
  choice : {"type":"choice","instructions":"...","criteria":{"cle": "description", ...}}
  noul   : {"type":"noul","instructions":"..."}                       -> probabilite 0-1 que ce soit vrai
  score  : {"type":"score","instructions":"...","criteria":["niveau 0", "niveau 1", ...]}
L'etat (state) est un dict ou une chaine ; les instructions peuvent citer ses cles entre `backticks`.
"""
import asyncio
import json
import os
import re
import time

import httpx

from .config import JEV_MODEL, LAYA_BACKEND, LAYA_HOSTED_URL, LAYA_URL, OPENROUTER_KEY, OPUS_EFFORT, OPUS_MODEL
from .ledger import LEDGER_OBJ, BudgetExceeded

LAYA_BACKENDS = {
    "local": {"label": "Laya · local", "where": "ton GPU (laya-serve)", "model_id": "laya (local)"},
    "hosted": {"label": "Laya · hébergé", "where": "Hugging Face (démo gratuite)", "model_id": "laya (Hugging Face)"},
}
LAYA = {"backend": LAYA_BACKEND if LAYA_BACKEND in LAYA_BACKENDS else "local"}

MODELS = {
    "jev": {"label": "Jev", "maker": "TypeSafe AI", "model_id": JEV_MODEL},
    "laya": {"label": "Laya", "maker": "Convai · open source", "model_id": LAYA_BACKENDS[LAYA["backend"]]["model_id"]},
    "opus": {"label": "Claude Opus 5.5", "maker": "Anthropic", "model_id": OPUS_MODEL},
}


def set_laya_backend(backend: str) -> dict:
    if backend not in LAYA_BACKENDS:
        raise ValueError(f"backend Laya inconnu : {backend}")
    LAYA["backend"] = backend
    MODELS["laya"]["model_id"] = LAYA_BACKENDS[backend]["model_id"]
    return laya_info()


def laya_info() -> dict:
    b = LAYA["backend"]
    return {"backend": b, **LAYA_BACKENDS[b], "backends": LAYA_BACKENDS}

_HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": os.environ.get("APP_URL", "https://github.com/AlanECE/jev-arena"),
    "X-Title": "jev-arena",
}
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(headers=_HEADERS, timeout=httpx.Timeout(60.0, connect=10.0),
                                    limits=httpx.Limits(max_connections=96, max_keepalive_connections=48))
    return _client


def _result(model, t0, t1, **kw):
    out = {"model": model, "model_id": MODELS[model]["model_id"], "ok": False, "answers": {},
           "latency_ms": round((t1 - t0) * 1000, 1), "cost": 0.0, "error": None, "raw": None,
           "t0": t0, "t1": t1}
    out.update(kw)
    return out


async def _post(url: str, payload: dict, retries: int):
    last = None
    for attempt in range(retries + 1):
        try:
            r = await client().post(url, json=payload)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                last = f"HTTP {r.status_code}"
                await asyncio.sleep(0.8 * (attempt + 1))
                continue
            return r
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = repr(e)[:160]
            if attempt < retries:
                await asyncio.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"requete echouee : {last}")


async def jev_decide(scene: str, state, questions: dict, tag: str = "", retries: int = 0) -> dict:
    LEDGER_OBJ.check(scene, 0.0005)
    t0 = time.time()
    try:
        r = await _post("https://openrouter.ai/api/alpha/decisions",
                        {"model": JEV_MODEL, "state": state, "questions": questions}, retries)
        t1 = time.time()
        d = r.json()
        if r.status_code != 200 or "answers" not in d:
            err = (d.get("error") or {}).get("message") if isinstance(d.get("error"), dict) else str(d)[:200]
            out = _result("jev", t0, t1, error=f"HTTP {r.status_code} : {str(err)[:200]}", tag=tag)
        else:
            u = d.get("usage") or {}
            out = _result("jev", t0, t1, ok=True, answers=d["answers"], cost=float(u.get("cost") or 0),
                          tag=tag, model_id=d.get("model", JEV_MODEL),
                          tokens={"in": u.get("input_tokens"), "out": u.get("output_tokens")})
    except Exception as e:  # reseau, timeout
        out = _result("jev", t0, time.time(), error=str(e)[:200], tag=tag)
    LEDGER_OBJ.add(scene, {"model": "jev", "ok": out["ok"], "latency_ms": out["latency_ms"], "cost": out["cost"],
                           "tag": tag, "error": out["error"]})
    return out


def questions_to_prompt(questions: dict) -> str:
    lines = []
    for name, q in questions.items():
        t = q["type"]
        if t == "choice":
            opts = "; ".join(f'"{k}" = {v}' for k, v in q["criteria"].items())
            lines.append(f'- "{name}" (choice): {q["instructions"]} Options: {opts}. '
                         f'Answer with the option key. Also give "{name}_confidence": your probability (0 to 1) '
                         f'that this option is the right one.')
        elif t == "noul":
            lines.append(f'- "{name}" (probability): {q["instructions"]} Answer with a number from 0 to 1 '
                         f'(probability that it is true).')
        elif t == "score":
            lv = "; ".join(f"{i} = {c}" for i, c in enumerate(q["criteria"]))
            lines.append(f'- "{name}" (level): {q["instructions"]} Levels: {lv}. Answer with the level integer.')
    return "\n".join(lines)


def build_opus_prompt(state, questions: dict) -> str:
    st = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    return ("You are a decision function inside a live system. Read the STATE and answer every question.\n"
            "Reply with ONE JSON object only (no prose, no code fence). Keys = question names.\n\n"
            f"STATE:\n{st}\n\nQUESTIONS:\n{questions_to_prompt(questions)}")


def parse_llm_answers(text: str, questions: dict) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("pas de JSON dans la reponse")
    j = json.loads(m.group(0))
    answers = {}
    for name, q in questions.items():
        v = j.get(name)
        if v is None:
            raise ValueError(f"question '{name}' sans reponse")
        if q["type"] == "choice":
            key = str(v).strip().strip('"')
            if key not in q["criteria"]:
                raise ValueError(f"option inconnue '{key}' pour '{name}'")
            conf = j.get(f"{name}_confidence")
            a = {"type": "choice", "choice": key, "declared": True}
            if conf is not None:
                try:
                    a["confidence"] = max(0.0, min(1.0, float(conf)))
                except (TypeError, ValueError):
                    pass
            answers[name] = a
        elif q["type"] == "noul":
            answers[name] = {"type": "noul", "noul": max(0.0, min(1.0, float(v))), "declared": True}
        elif q["type"] == "score":
            answers[name] = {"type": "score", "score": float(v), "declared": True}
    return answers


async def opus_decide(scene: str, state, questions: dict, tag: str = "", retries: int = 0,
                      max_tokens: int = 400) -> dict:
    LEDGER_OBJ.check(scene, 0.01)
    payload = {"model": OPUS_MODEL, "max_tokens": max_tokens, "usage": {"include": True},
               "reasoning": {"effort": OPUS_EFFORT, "exclude": True},
               "messages": [{"role": "user", "content": build_opus_prompt(state, questions)}]}
    t0 = time.time()
    try:
        r = await _post("https://openrouter.ai/api/v1/chat/completions", payload, retries)
        t1 = time.time()
        d = r.json()
        if r.status_code != 200 or "error" in d:
            err = d.get("error") if isinstance(d, dict) else None
            msg = err.get("message") if isinstance(err, dict) else str(d)[:200]
            out = _result("opus", t0, t1, error=f"HTTP {r.status_code} : {str(msg)[:200]}", tag=tag)
        else:
            u = d.get("usage") or {}
            ch = (d.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            text = msg.get("content") or ""
            cost = float(u.get("cost") or 0)
            if not text.strip():
                why = msg.get("refusal") or ch.get("native_finish_reason") or ch.get("finish_reason") or "vide"
                out = _result("opus", t0, t1, error=f"réponse vide ({str(why)[:160]})", cost=cost, tag=tag)
                LEDGER_OBJ.add(scene, {"model": "opus", "ok": False, "latency_ms": out["latency_ms"],
                                       "cost": cost, "tag": tag, "error": out["error"]})
                return out
            try:
                answers = parse_llm_answers(text, questions)
                out = _result("opus", t0, t1, ok=True, answers=answers, cost=cost, raw=text[:400], tag=tag,
                              model_id=d.get("model", OPUS_MODEL),
                              tokens={"in": u.get("prompt_tokens"), "out": u.get("completion_tokens")})
            except Exception as e:
                out = _result("opus", t0, t1, error=f"reponse illisible : {e}", cost=cost, raw=text[:400], tag=tag)
    except Exception as e:
        out = _result("opus", t0, time.time(), error=str(e)[:200], tag=tag)
    LEDGER_OBJ.add(scene, {"model": "opus", "ok": out["ok"], "latency_ms": out["latency_ms"], "cost": out["cost"],
                           "tag": tag, "error": out["error"]})
    return out


# ---------------------------------------------------------------------------------------------- Laya
# Client HTTP a part : il ne porte PAS l'en-tete d'autorisation OpenRouter (la cle ne part que chez OpenRouter).
_laya_http: httpx.AsyncClient | None = None
_KEEP = ("type", "choice", "probabilities", "confidence", "noul", "score")


def laya_client() -> httpx.AsyncClient:
    global _laya_http
    if _laya_http is None:
        _laya_http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0),
                                       limits=httpx.Limits(max_connections=64, max_keepalive_connections=32))
    return _laya_http


def _clean_answers(answers: dict) -> dict:
    return {k: {f: v for f, v in (a or {}).items() if f in _KEEP} for k, a in (answers or {}).items()}


async def _laya_local(state, questions: dict) -> dict:
    r = await laya_client().post(f"{LAYA_URL}/v1/systemone", json={"state": state, "questions": questions})
    try:
        d = r.json()
    except Exception:
        raise RuntimeError(f"HTTP {r.status_code} : {r.text[:160]}")
    if r.status_code != 200 or "answers" not in d:
        det = d.get("detail") if isinstance(d, dict) else d
        raise RuntimeError(f"HTTP {r.status_code} : {str(det)[:180]}")
    return d


async def _laya_hosted(state, questions: dict) -> dict:
    """Demo officielle de Laya sur Hugging Face (Gradio) : point d'acces /run_playground (etat + questions en JSON)."""
    base = f"{LAYA_HOSTED_URL}/gradio_api/call/run_playground"
    st = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    r = await laya_client().post(base, json={"data": [st, json.dumps(questions, ensure_ascii=False)]})
    if r.status_code != 200:
        raise RuntimeError(f"Hugging Face HTTP {r.status_code} : {r.text[:160]}")
    eid = r.json().get("event_id")
    r2 = await laya_client().get(f"{base}/{eid}")
    event, data = None, None
    for line in r2.text.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
    if event != "complete" or not data:
        raise RuntimeError(f"Hugging Face : {event or 'pas de réponse'} {str(data)[:120] if data not in (None, 'null') else ''}".strip())
    out = json.loads(data)
    raw = out[1] if isinstance(out, list) and len(out) > 1 else None
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        raise RuntimeError(f"réponse illisible : {str(raw)[:160]}")
    if not isinstance(d, dict) or "answers" not in d:
        raise RuntimeError(f"réponse sans answers : {str(raw)[:160]}")
    return d


async def laya_decide(scene: str, state, questions: dict, tag: str = "", retries: int = 0,
                      backend: str | None = None) -> dict:
    """Laya en local (laya-serve sur le GPU) ou heberge (demo Hugging Face). Gratuit dans les deux cas : cout 0.
    `backend` force l'un des deux pour cet appel (page /laya) sans changer celui des epreuves."""
    backend = backend if backend in LAYA_BACKENDS else LAYA["backend"]
    fn = _laya_hosted if backend == "hosted" else _laya_local
    t0 = time.time()
    last = None
    out = None
    for attempt in range(retries + 1):
        try:
            d = await fn(state, questions)
            out = _result("laya", t0, time.time(), ok=True, answers=_clean_answers(d["answers"]), cost=0.0, tag=tag,
                          backend=backend, tokens={"in": (d.get("usage") or {}).get("input_tokens"), "out": 0},
                          routed=((d.get("routing") or {}).get("model")))
            break
        except Exception as e:
            last = str(e)[:200] or type(e).__name__
            if attempt < retries:
                await asyncio.sleep(0.5 * (attempt + 1))
    if out is None:
        out = _result("laya", t0, time.time(), error=last, tag=tag, backend=backend)
    LEDGER_OBJ.add(scene, {"model": "laya", "ok": out["ok"], "latency_ms": out["latency_ms"], "cost": 0.0,
                           "tag": tag, "error": out["error"], "backend": backend})
    return out


async def decide(model: str, scene: str, state, questions: dict, tag: str = "", retries: int = 0) -> dict:
    """Point d'entree unique. model = 'jev', 'laya' ou 'opus'. Ne leve jamais d'exception sauf BudgetExceeded."""
    if model == "jev":
        return await jev_decide(scene, state, questions, tag=tag, retries=retries)
    if model == "laya":
        return await laya_decide(scene, state, questions, tag=tag, retries=retries)
    if model == "opus":
        return await opus_decide(scene, state, questions, tag=tag, retries=retries)
    raise ValueError(f"modele inconnu : {model}")


def top_choice(answer: dict) -> tuple[str | None, float | None]:
    """(option choisie, probabilite ou confiance) pour une reponse de type choice."""
    if not answer:
        return None, None
    c = answer.get("choice")
    p = None
    if answer.get("probabilities") and c in answer["probabilities"]:
        p = float(answer["probabilities"][c])
    elif answer.get("confidence") is not None:
        p = float(answer["confidence"])
    return c, p


__all__ = ["MODELS", "LAYA", "decide", "jev_decide", "laya_decide", "opus_decide", "top_choice", "BudgetExceeded",
           "build_opus_prompt", "parse_llm_answers", "set_laya_backend", "laya_info"]
