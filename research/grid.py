"""Backtest d'une regle de retour a la moyenne sur bougies d'1 s, avec executions PESSIMISTES
(achat au plus haut de la seconde d'execution + demi-ecart NBBO, vente au plus bas - demi-ecart) et deux profils :
  - "jev"  : decision a chaque seconde, execution dans la seconde suivante (latence 0,3 s)
  - "opus" : decision toutes les 4 s, execution 3 s plus tard (latence ~3 s)

Usage : python grid.py SPY,TSLA,NVDA 2026-10-07,2026-10-08 [2026-10-09]   (derniere liste = validation)
"""
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from study import features, load_day, load_spread_bps

ROOT = Path(__file__).resolve().parent


def run(df, feat, spread_bps, *, thr, hold_max, tp, sl, latency, every, min_hold=2, exit_thr=None,
        gate=0.0, allow_short=True, fee_bps=0.0):
    c = df["c"].to_numpy()
    h = df["h"].to_numpy()
    lo = df["l"].to_numpy()
    n = len(c)
    half = spread_bps / 2 + fee_bps
    # score de retour a la moyenne : on vend ce qui vient de monter trop vite, on achete ce qui vient de chuter
    z = feat["z10"].fillna(0).to_numpy()
    z30 = feat["z30"].fillna(0).to_numpy()
    rng = feat["rng60"].fillna(0).to_numpy()
    s = -(0.6 * z + 0.4 * z30)
    pos, entry_px, entry_i = 0, 0.0, 0
    trades = []
    i, next_dec = 61, 61
    exit_thr = thr if exit_thr is None else exit_thr

    o = df["o"].to_numpy()

    def buy_px(j):   # execution realiste : premier echange de la seconde j, au prix de l'offre (demi-ecart paye)
        return o[j] + c[j] * half / 1e4

    def sell_px(j):
        return o[j] - c[j] * half / 1e4

    while i < n - latency - 2:
        if pos != 0:
            pnl = (np.log(c[i] / entry_px) * 1e4) * pos
            held = i - entry_i
            reason = None
            if pnl >= tp:
                reason = "tp"
            elif pnl <= -sl:
                reason = "sl"
            elif held >= hold_max:
                reason = "time"
            elif held >= min_hold and i >= next_dec and s[i] * pos < -exit_thr:
                reason = "flip"
            if reason:
                j = i + 1 + latency
                fill = sell_px(j) if pos > 0 else buy_px(j)
                gross = (np.log(fill / entry_px) * 1e4) * pos
                trades.append({"side": pos, "entry": int(entry_i), "exit": int(j), "net": gross, "reason": reason,
                               "held": int(j - entry_i)})
                pos = 0
                next_dec = i + every
            i += 1
            continue
        if i >= next_dec:
            next_dec = i + every
            want = 0
            if s[i] > thr and rng[i] <= -gate:
                want = 1
            elif s[i] < -thr and rng[i] >= gate and allow_short:
                want = -1
            if want:
                j = i + 1 + latency
                entry_px = buy_px(j) if want > 0 else sell_px(j)
                pos, entry_i = want, j
                i = j + 1
                continue
        i += 1
    if not trades:
        return {"trades": 0, "sum_net_bps": 0.0}
    t = pd.DataFrame(trades)
    net = t["net"]
    return {"trades": int(len(t)), "win_rate": round(float((net > 0).mean()), 3),
            "avg_net_bps": round(float(net.mean()), 3), "sum_net_bps": round(float(net.sum()), 1),
            "pnl_10k": round(float(net.sum()), 2), "held_med": float(t["held"].median()),
            "tstat": round(float(net.mean() / net.std() * np.sqrt(len(net))), 2) if net.std() > 0 else 0.0,
            "reasons": t["reason"].value_counts().to_dict(),
            "longs": int((t["side"] > 0).sum())}


PROFILES = {"jev": {"latency": 0, "every": 1}, "opus": {"latency": 3, "every": 4}}
GRID = {
    "thr": [1.5, 2.0, 2.5, 3.0],
    "hold_max": [10, 20, 40],
    "tp": [1.0, 2.0, 4.0],
    "sl": [2.0, 4.0, 8.0],
    "gate": [0.0, 0.5],
}

if __name__ == "__main__":
    syms = sys.argv[1].split(",")
    train_days = sys.argv[2].split(",")
    test_days = sys.argv[3].split(",") if len(sys.argv) > 3 else []
    data = {}
    for sym in syms:
        for day in train_days + test_days:
            df = load_day(sym, day)
            if df is not None:
                data[(sym, day)] = (df, features(df), load_spread_bps(sym, day))
                v = data[(sym, day)][1]["vol60"].median()
                print(f"{sym} {day}: vol 1s mediane {v:.2f} bps, ecart {data[(sym, day)][2]:.2f} bps")
    results = []
    combos = list(itertools.product(*GRID.values()))
    print(f"{len(combos)} combinaisons x {len(syms)} symboles x 2 profils")
    for sym in syms:
        for combo in combos:
            p = dict(zip(GRID.keys(), combo))
            for prof, pp in PROFILES.items():
                tr = [run(*data[(sym, d)], **p, **pp) for d in train_days if (sym, d) in data]
                te = [run(*data[(sym, d)], **p, **pp) for d in test_days if (sym, d) in data]
                row = {"sym": sym, "profile": prof, **p,
                       "train_trades": sum(r["trades"] for r in tr),
                       "train_bps_per_day": round(np.mean([r["sum_net_bps"] for r in tr]), 1) if tr else None,
                       "train_avg": round(np.mean([r.get("avg_net_bps", 0) for r in tr]), 3) if tr else None,
                       "train_win": round(np.mean([r.get("win_rate", 0) for r in tr]), 3) if tr else None,
                       "test_trades": sum(r["trades"] for r in te),
                       "test_bps_per_day": round(np.mean([r["sum_net_bps"] for r in te]), 1) if te else None,
                       "test_avg": round(np.mean([r.get("avg_net_bps", 0) for r in te]), 3) if te else None,
                       "test_win": round(np.mean([r.get("win_rate", 0) for r in te]), 3) if te else None}
                results.append(row)
    res = pd.DataFrame(results)
    res.to_csv(ROOT / "grid_results.csv", index=False)
    pd.set_option("display.width", 250)
    for sym in syms:
        for prof in PROFILES:
            sub = res[(res.sym == sym) & (res.profile == prof)].sort_values("train_bps_per_day", ascending=False)
            print(f"\n=== {sym} · {prof} : 8 meilleures combinaisons en apprentissage (bps nets par jour)")
            print(sub.head(8).drop(columns=["sym", "profile"]).to_string(index=False))
