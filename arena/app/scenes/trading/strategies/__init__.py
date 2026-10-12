"""Chargement automatique des strategies (chaque fichier qui definit STRATEGY)."""
import importlib
import pkgutil
import traceback

ERRORS = {}


def load_all() -> dict:
    out = {}
    for m in pkgutil.iter_modules(__path__):
        if m.name in ("base", "indicators"):
            continue
        try:
            mod = importlib.reload(importlib.import_module(f"{__name__}.{m.name}"))
            for name in dir(mod):
                if name.startswith("STRATEGY"):
                    s = getattr(mod, name)
                    if s is not None and hasattr(s, "id"):
                        out[s.id] = s
        except Exception:
            ERRORS[m.name] = traceback.format_exc()[-1200:]
    return out
