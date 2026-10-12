"""Registre des appels payants : chaque decision (Jev ou Opus) y laisse modele, latence et cout reel
renvoye par OpenRouter. Un plafond par scene refuse les appels une fois atteint.
Le fichier est partage par tous les serveurs (8800 et instances de test) : il est relu des qu'un autre
processus y a ecrit."""
import json
import threading
import time

from .config import CAPS, DATA

LEDGER = DATA / "ledger.jsonl"
_lock = threading.Lock()


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self):
        self._load()

    def _load(self):
        self.spent: dict[str, float] = {}
        self.calls: dict[str, int] = {}
        self._size = LEDGER.stat().st_size if LEDGER.exists() else 0
        if LEDGER.exists():
            for line in LEDGER.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                s = e.get("scene", "?")
                self.spent[s] = self.spent.get(s, 0.0) + float(e.get("cost") or 0)
                self.calls[s] = self.calls.get(s, 0) + 1

    def _refresh(self):
        try:
            size = LEDGER.stat().st_size if LEDGER.exists() else 0
        except OSError:
            return
        if size != self._size:
            with _lock:
                self._load()

    def cap(self, scene: str) -> float:
        return CAPS.get(scene, 1.0)

    def check(self, scene: str, reserve: float = 0.0):
        self._refresh()
        if self.spent.get(scene, 0.0) + reserve > self.cap(scene):
            raise BudgetExceeded(f"plafond {scene} atteint : {self.spent.get(scene, 0):.3f} $ / {self.cap(scene):.2f} $")

    def add(self, scene: str, entry: dict):
        entry = {"ts": time.time(), "scene": scene, **entry}
        with _lock:
            self.spent[scene] = self.spent.get(scene, 0.0) + float(entry.get("cost") or 0)
            self.calls[scene] = self.calls.get(scene, 0) + 1
            with open(LEDGER, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            try:
                self._size = LEDGER.stat().st_size
            except OSError:
                pass

    def summary(self) -> dict:
        self._refresh()
        return {s: {"spent": round(v, 5), "cap": self.cap(s), "calls": self.calls.get(s, 0)}
                for s, v in self.spent.items()}


LEDGER_OBJ = Ledger()
