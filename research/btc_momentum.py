"""BTC : regle de RUPTURE (suivre l'exces) sur bougies d'1 s, 10 journees Binance, avec l'ecart Coinbase mesure.
Usage : python btc_momentum.py 0.2,0.5   (ecarts aller-retour en bps a tester)
"""
import itertools
import sys

import numpy as np
import pandas as pd

from btc_study import load_btc, DATA
from grid2 import extra
from study import features

PROFILES = {"jev": {"latency": 0, "every": 1}, "opus": {"latency": 3, "every": 4}}
NONE = 1e9


def run(df, ex, spread_bps, *, sig, thr, hold, tp, sl, latency, every, follow=True, trail=None):
    c = df["c"].to_numpy()
    o = df["o"].to_numpy()
    n = len(c)
    half = spread_bps / 2
    z = ex[sig].to_numpy()
    pos, entry_px, entry_i, best = 0, 0.0, 0, 0.0
    trades = []
    i, next_dec = 301, 301
    while i < n - latency - 2:
        if pos != 0:
            pnl = (np.log(c[i] / entry_px) * 1e4) * pos
            best = max(best, pnl)
            held = i - entry_i
            reason = ("tp" if pnl >= tp else "sl" if pnl <= -sl else "time" if held >= hold
                      else "trail" if (trail and best >= trail and pnl <= best - trail) else None)
            if reason:
                j = i + 1 + latency
                fill = (o[j] - c[j] * half / 1e4) if pos > 0 else (o[j] + c[j] * half / 1e4)
                trades.append({"net": (np.log(fill / entry_px) * 1e4) * pos, "reason": reason, "side": pos, "held": j - entry_i})
                pos = 0
                next_dec = i + every
            i += 1
            continue
        if i >= next_dec:
            next_dec = i + every
            d = 1 if z[i] > thr else -1 if z[i] < -thr else 0
            want = d if follow else -d
            if want:
                j = i + 1 + latency
                entry_px = (o[j] + c[j] * half / 1e4) if want > 0 else (o[j] - c[j] * half / 1e4)
                pos, entry_i, best = want, j, 0.0
                i = j + 1
                continue
        i += 1
    if not trades:
        return {"n": 0, "sum": 0.0, "avg": 0.0, "win": 0.0}
    t = pd.DataFrame(trades)
    net = t["net"]
    return {"n": len(t), "sum": float(net.sum()), "avg": float(net.mean()), "win": float((net > 0).mean()),
            "reasons": t["reason"].value_counts().to_dict()}


GRID = {"sig": ["z10", "z30"], "thr": [2.0, 2.5, 3.0], "hold": [30, 60, 120, 240],
        "tp": [NONE, 4.0, 8.0], "sl": [2.0, 4.0, 8.0]}

if __name__ == "__main__":
    spreads = [float(x) for x in (sys.argv[1] if len(sys.argv) > 1 else "0.2,0.5").split(",")]
    days = sorted(p.name[11:21] for p in DATA.glob("BTCUSDT-1s-*.csv"))
    data = {}
    for d in days:
        df = load_btc(d)
        data[d] = (df, extra(df, features(df)))
    pd.set_option("display.width", 230)
    for spread in spreads:
        rows = []
        for combo in itertools.product(*GRID.values()):
            p = dict(zip(GRID.keys(), combo))
            for prof, pp in PROFILES.items():
                rs = [run(df, ex, spread, **p, **pp) for df, ex in data.values()]
                per = [r["sum"] for r in rs]
                rows.append({"prof": prof, **{k: (v if v != NONE else "-") for k, v in p.items()},
                             "n/j": round(np.mean([r["n"] for r in rs]), 0), "bps/j": round(np.mean(per), 1),
                             "min": round(min(per), 1), "max": round(max(per), 1), "j+": sum(1 for x in per if x > 0),
                             "avg": round(np.mean([r["avg"] for r in rs]), 2), "win": round(np.mean([r["win"] for r in rs]), 2)})
        res = pd.DataFrame(rows)
        res.to_csv(DATA.parent.parent / f"btc_momentum_{spread}.csv", index=False)
        for prof in PROFILES:
            sub = res[(res.prof == prof) & (res["n/j"] >= 8)].sort_values("bps/j", ascending=False)
            print(f"\n=== BTC RUPTURE · ecart {spread} bps · {prof} · {len(days)} journees · 12 meilleures")
            print(sub.head(12).drop(columns=["prof"]).to_string(index=False))
            print(f"  combinaisons positives : {(sub['bps/j'] > 0).mean():.0%} · mediane {sub['bps/j'].median():.1f}")
