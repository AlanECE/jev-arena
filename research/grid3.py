"""Backtest n°3 : la regle propre de retour a la moyenne.
  entree  : |z| > thr sur l'etirement des k dernieres secondes (z30 ou z60), position inverse, une seule a la fois
  sortie  : tenue fixe `hold` secondes (ou prise de profit / stop LARGES, optionnels)
Execution realiste (premier echange de la seconde suivante +/- demi-ecart NBBO). Profils jev / opus.
Usage : python grid3.py TSLA,SPY,QQQ,NVDA 2026-10-07,2026-10-08,2026-10-09
"""
import itertools
import sys

import numpy as np
import pandas as pd

from grid2 import extra
from study import features, load_day, load_spread_bps

PROFILES = {"jev": {"latency": 0, "every": 1}, "opus": {"latency": 3, "every": 4}}
NONE = 1e9


def run(df, ex, spread_bps, *, sig, thr, hold, tp, sl, latency, every, allow_short=True):
    c = df["c"].to_numpy()
    o = df["o"].to_numpy()
    n = len(c)
    half = spread_bps / 2
    z = ex[sig].to_numpy()
    pos, entry_px, entry_i = 0, 0.0, 0
    trades = []
    i, next_dec = 301, 301
    while i < n - latency - 2:
        if pos != 0:
            pnl = (np.log(c[i] / entry_px) * 1e4) * pos
            held = i - entry_i
            reason = "tp" if pnl >= tp else "sl" if pnl <= -sl else "time" if held >= hold else None
            if reason:
                j = i + 1 + latency
                fill = (o[j] - c[j] * half / 1e4) if pos > 0 else (o[j] + c[j] * half / 1e4)
                trades.append({"net": (np.log(fill / entry_px) * 1e4) * pos, "reason": reason, "side": pos,
                               "held": j - entry_i, "t": int(df.index[entry_i])})
                pos = 0
                next_dec = i + every
            i += 1
            continue
        if i >= next_dec:
            next_dec = i + every
            want = -1 if z[i] > thr else 1 if z[i] < -thr else 0
            if want == -1 and not allow_short:
                want = 0
            if want:
                j = i + 1 + latency
                entry_px = (o[j] + c[j] * half / 1e4) if want > 0 else (o[j] - c[j] * half / 1e4)
                pos, entry_i = want, j
                i = j + 1
                continue
        i += 1
    if not trades:
        return {"n": 0, "sum": 0.0, "avg": 0.0, "win": 0.0, "t": 0.0}
    t = pd.DataFrame(trades)
    net = t["net"]
    return {"n": len(t), "sum": float(net.sum()), "avg": float(net.mean()), "win": float((net > 0).mean()),
            "t": float(net.mean() / net.std() * np.sqrt(len(net))) if net.std() > 0 else 0.0,
            "longs": int((t.side > 0).sum()), "trades": t}


GRID = {"sig": ["z30", "z60"], "thr": [1.5, 2.0, 2.5], "hold": [30, 60, 90, 120],
        "tp": [NONE, 4.0, 8.0], "sl": [NONE, 16.0, 30.0]}

if __name__ == "__main__":
    syms = sys.argv[1].split(",")
    days = sys.argv[2].split(",")
    data = {}
    for sym in syms:
        for day in days:
            df = load_day(sym, day)
            if df is not None:
                data[(sym, day)] = (df, extra(df, features(df)), load_spread_bps(sym, day))
    pd.set_option("display.width", 250)
    for sym in syms:
        rows = []
        dd = [d for d in days if (sym, d) in data]
        for combo in itertools.product(*GRID.values()):
            p = dict(zip(GRID.keys(), combo))
            for prof, pp in PROFILES.items():
                rs = [run(*data[(sym, d)], **p, **pp) for d in dd]
                per_day = [r["sum"] for r in rs]
                rows.append({"prof": prof, **{k: (v if v != NONE else "-") for k, v in p.items()},
                             "n/j": round(np.mean([r["n"] for r in rs]), 0),
                             "bps/j": round(np.mean(per_day), 1), "min j": round(min(per_day), 1),
                             "max j": round(max(per_day), 1),
                             "avg": round(np.mean([r["avg"] for r in rs]), 2),
                             "win": round(np.mean([r["win"] for r in rs]), 2),
                             "t": round(np.mean([r["t"] for r in rs]), 1)})
        res = pd.DataFrame(rows)
        for prof in PROFILES:
            sub = res[(res.prof == prof) & (res["n/j"] >= 8)].sort_values("bps/j", ascending=False)
            print(f"\n=== {sym} · {prof} · {len(dd)} journees ({', '.join(dd)}) · bps nets par jour, 10 meilleures")
            print(sub.head(10).drop(columns=["prof"]).to_string(index=False))
            sub2 = res[(res.prof == prof) & (res["n/j"] >= 8)]
            print(f"  part des combinaisons positives : {(sub2['bps/j'] > 0).mean():.0%}  ·  mediane {sub2['bps/j'].median():.1f} bps/j")
