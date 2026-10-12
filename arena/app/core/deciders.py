"""Les deux decideurs de l'arene, appeles pour de vrai via OpenRouter.

- Jev (TypeSafe) : API Decisions d'OpenRouter, POST /api/alpha/decisions. Etat + questions typees
  -> reponses typees avec probabilites. Jev n'ecrit jamais de texte.
- Claude Opus 5.5 : chat completions OpenRouter. Il recoit EXACTEMENT le meme etat et les memes questions,
  et repond en JSON. Sa "confiance" est declaree par lui (ce n'est pas une probabilite calculee).

Les deux renvoient la meme forme :
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

from .config import JEV_MODEL, OPENROUTER_KEY, OPUS_EFFORT, OPUS_MODEL
from .ledger import LEDGER_OBJ, BudgetExceeded

MODELS = {
    "jev": {"label": "Jev", "maker": "TypeSafe AI", "model_id": JEV_MODEL},
    "opus": {"label": "Claude Opus 5.5", "maker": "Anthropic", "model_id": OPUS_MODEL},
}

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


async def decide(model: str, scene: str, state, questions: dict, tag: str = "", retries: int = 0) -> dict:
    """Point d'entree unique. model = 'jev' ou 'opus'. Ne leve jamais d'exception sauf BudgetExceeded."""
    if model == "jev":
        return await jev_decide(scene, state, questions, tag=tag, retries=retries)
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


__all__ = ["MODELS", "decide", "jev_decide", "opus_decide", "top_choice", "BudgetExceeded",
           "build_opus_prompt", "parse_llm_answers"]
