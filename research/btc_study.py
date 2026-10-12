"""Etude BTC/USD sur bougies d'1 s (klines Binance BTCUSDT, data/btc/BTCUSDT-1s-<jour>.csv).
Couts : ecart aller-retour parametrable (Coinbase BTC-USD ~1 bps ; Binance ~0,01 bps). Pas de frais (courtier simule).
Usage : python btc_study.py [spread_bps=1.0]
"""
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from grid2 import extra
from grid3 import NONE, PROFILES, run
from study import features

DATA = Path(__file__).resolve().parent / "data" / "btc"
H = (5, 10, 30, 60, 120, 240)


def load_btc(day: str) -> pd.DataFrame | None:
    f = DATA / f"BTCUSDT-1s-{day}.csv"
    if not f.exists():
        return None
    df = pd.read_csv(f, header=None, usecols=range(10),
                     names=["t", "o", "h", "l", "c", "v", "tc", "qv", "n", "vb"])
    df["t"] = (df["t"] // 1_000_000 if df["t"].iloc[0] > 1e14 else df["t"] // 1000).astype(int)
    df["vs"] = df["v"] - df["vb"]
    df = df.set_index("t")[["o", "h", "l", "c", "v", "vb", "vs", "n"]].astype(float)
    df.index.name = "t"
    return df


if __name__ == "__main__":
    spread = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    days = sorted(p.name[11:21] for p in DATA.glob("BTCUSDT-1s-*.csv"))
    print("journees :", days, "· ecart aller-retour", spread, "bps")
    data = {}
    for d in days:
        df = load_btc(d)
        ft = features(df)
        data[d] = (df, extra(df, ft), ft)
    # 1) avantage brut par evenement
    pd.set_option("display.width", 230)
    z = pd.concat([pd.DataFrame({"z10": ft["z10"], "z30": ft["z30"], "z60": ex["z60"], "vwapz": ex["vwapz"]}) for df, ex, ft in data.values()])
    fwd = pd.concat([pd.DataFrame({h: (np.log(df["c"]).shift(-h) - np.log(df["c"])) * 1e4 for h in H}) for df, ex, ft in data.values()])
    rows = []
    for sig in z.columns:
        for thr in (1.5, 2.0, 2.5, 3.0):
            m = z[sig].abs() > thr
            if m.sum() < 50:
                continue
            rev = fwd[m].mul(-np.sign(z[sig][m]), axis=0)
            rows.append({"signal": sig, "thr": thr, "n": int(m.sum()), **{f"{h}s": round(rev[h].mean(), 2) for h in H},
                         "se60": round(rev[60].std() / np.sqrt(m.sum()), 2)})
    print("\n=== retour moyen apres un exces (bps, signe dans le sens du retour)")
    print(pd.DataFrame(rows).to_string(index=False))
    vol = np.median([ft["vol60"].median() for df, ex, ft in data.values()])
    print(f"\nvolatilite 1 s mediane : {vol:.2f} bps")
    # 2) grille de la regle propre
    GRID = {"sig": ["z30", "z60"], "thr": [2.0, 2.5, 3.0], "hold": [30, 60, 90, 120, 180],
            "tp": [NONE, 4.0, 8.0], "sl": [NONE, 12.0, 24.0]}
    res = []
    for combo in itertools.product(*GRID.values()):
        p = dict(zip(GRID.keys(), combo))
        for prof, pp in PROFILES.items():
            rs = [run(df, ex, spread, **p, **pp) for df, ex, ft in data.values()]
            per = [r["sum"] for r in rs]
            res.append({"prof": prof, **{k: (v if v != NONE else "-") for k, v in p.items()},
                        "n/j": round(np.mean([r["n"] for r in rs]), 0), "bps/j": round(np.mean(per), 1),
                        "min": round(min(per), 1), "max": round(max(per), 1),
                        "j+": sum(1 for x in per if x > 0), "avg": round(np.mean([r["avg"] for r in rs]), 2),
                        "win": round(np.mean([r["win"] for r in rs]), 2)})
    res = pd.DataFrame(res)
    res.to_csv(DATA.parent.parent / "btc_grid.csv", index=False)
    for prof in PROFILES:
        sub = res[(res.prof == prof) & (res["n/j"] >= 8)].sort_values("bps/j", ascending=False)
        print(f"\n=== BTC · {prof} · {len(days)} journees · 10 meilleures (bps nets par jour)")
        print(sub.head(10).drop(columns=["prof"]).to_string(index=False))
        print(f"  combinaisons positives : {(sub['bps/j'] > 0).mean():.0%} · mediane {sub['bps/j'].median():.1f}")
