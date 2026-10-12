"""Configuration lue dans l'environnement (fichier .env monte par docker compose)."""
import os
from pathlib import Path

DATA = Path(os.environ.get("ARENA_DATA", "/data"))
SESSIONS = DATA / "sessions"
SESSIONS.mkdir(parents=True, exist_ok=True)


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


OPENROUTER_KEY = env("OPENROUTER_API_KEY")
JEV_MODEL = env("JEV_MODEL", "~typesafe/jev-latest")
OPUS_MODEL = env("OPUS_MODEL", "anthropic/claude-opus-5.5")
# Opus 5.5 n'accepte pas de raisonnement desactive sur OpenRouter : "low" est le reglage le plus rapide.
OPUS_EFFORT = env("OPUS_EFFORT", "low")

ALPACA_ENDPOINT = (env("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets") or "").rstrip("/")
if ALPACA_ENDPOINT.endswith("/v2"):
    ALPACA_ENDPOINT = ALPACA_ENDPOINT[:-3]
ALPACA_KEY = env("ALPACA_KEY")
ALPACA_SECRET = env("ALPACA_SECRET")
ALPACA_OPUS_KEY = env("ALPACA_OPUS_KEY")
ALPACA_OPUS_SECRET = env("ALPACA_OPUS_SECRET")

# Laya (open source, Convai Innovations) : meme format de questions que Jev.
# "local"  = le conteneur `laya` de ce projet (laya-serve, POST /v1/systemone), sur la carte graphique ;
# "hosted" = la demo officielle de Laya hebergee gratuitement par Hugging Face (Space Gradio, sans compte).
LAYA_BACKEND = env("LAYA_BACKEND", "local")
LAYA_URL = (env("LAYA_URL", "http://laya:8000") or "").rstrip("/")
LAYA_HOSTED_URL = (env("LAYA_HOSTED_URL", "https://convaiinnovations-laya-demo.hf.space") or "").rstrip("/")

GITHUB_TOKEN = env("GITHUB_TOKEN")
CRAWL4AI_URL = env("CRAWL4AI_URL", "http://crawl4ai:11235")
CRAWL4AI_TOKEN = env("CRAWL4AI_API_TOKEN")

# Plafonds de depense OpenRouter par scene (en dollars, cumules dans data/ledger.jsonl).
CAPS = {
    "trading": float(env("CAP_TRADING", "6")),
    "tetris": float(env("CAP_TETRIS", "3")),
    "leads": float(env("CAP_LEADS", "6")),
    "avis": float(env("CAP_AVIS", "5")),
    "probe": float(env("CAP_PROBE", "0.5")),
}
