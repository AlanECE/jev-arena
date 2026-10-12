"""Avantage brut par evenement : apres un etirement de k secondes au-dela de `thr` ecarts-types (dans un sens ou
l'autre), de combien le prix revient-il en moyenne (en bps, signe dans le sens du retour) a 5 s ... 240 s ?
A comparer a l'ecart NBBO (ce qu'un ordre au marche paie a l'aller-retour).
Usage : python condmean.py SPY,QQQ,TSLA,NVDA 2026-10-07,2026-10-08,2026-10-09
"""
import sys

import numpy as np
import pandas as pd

from grid2 import extra
from study import features, load_day, load_spread_bps

H = (5, 10, 30, 60, 120, 240)

if __name__ == "__main__":
    syms = sys.argv[1].split(",")
    days = sys.argv[2].split(",")
    pd.set_option("display.width", 220)
    for sym in syms:
        parts = []
        spreads = []
        for day in days:
            df = load_day(sym, day)
            if df is None:
                continue
            ft = features(df)
            ex = extra(df, ft)
            lc = np.log(df["c"])
            fwd = pd.DataFrame({h: (lc.shift(-h) - lc) * 1e4 for h in H}, index=df.index)
            z = pd.DataFrame({"z10": ft["z10"], "z30": ft["z30"], "z60": ex["z60"], "vwapz": ex["vwapz"]}, index=df.index)
            parts.append((z, fwd))
            spreads.append(load_spread_bps(sym, day))
        if not parts:
            continue
        z = pd.concat([p[0] for p in parts])
        fwd = pd.concat([p[1] for p in parts])
        print(f"\n=== {sym} · {len(parts)} journees · ecart NBBO median {np.mean(spreads):.2f} bps (aller-retour au marche)")
        rows = []
        for sig in z.columns:
            for thr in (1.5, 2.0, 2.5, 3.0):
                m = z[sig].abs() > thr
                if m.sum() < 30:
                    continue
                sign = -np.sign(z[sig][m])           # on parie sur le retour
                rev = fwd[m].mul(sign, axis=0)
                rows.append({"signal": sig, "thr": thr, "n": int(m.sum()),
                             **{f"{h}s": round(rev[h].mean(), 2) for h in H},
                             "se60": round(rev[60].std() / np.sqrt(m.sum()), 2)})
        print(pd.DataFrame(rows).to_string(index=False))
