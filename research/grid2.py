"""Backtest n°2 : retour a la moyenne sur un horizon plus long (tenue 45 a 240 s), signaux d'etirement :
  - z60   : rendement des 60 dernieres secondes, normalise par la volatilite
  - vwapz : ecart au VWAP de seance, normalise par la volatilite x racine(300)
  - rng   : position dans la fourchette des 5 dernieres minutes (-1 bas .. +1 haut)
Execution realiste (premier echange de la seconde suivante + demi-ecart NBBO). Profils jev / opus comme grid.py.
Usage : python grid2.py SPY,QQQ 2026-10-07,2026-10-08 2026-10-09
"""
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from study import features, load_day, load_spread_bps

ROOT = Path(__file__).resolve().parent


def extra(df, feat):
    c = df["c"]
    lc = np.log(c)
    vol = feat["vol60"].replace(0, np.nan)
    f = pd.DataFrame(index=df.index)
    f["z60"] = feat["r60"] / (vol * np.sqrt(60))
    f["z180"] = ((lc - lc.shift(180)) * 1e4) / (vol * np.sqrt(180))
    f["vwapz"] = feat["vwap_dev"] / (vol * np.sqrt(300))
    hi = df["h"].rolling(300).max()
    lo = df["l"].rolling(300).min()
    f["rng300"] = ((c - lo) / (hi - lo).replace(0, np.nan) - 0.5) * 2
    f["z10"] = feat["z10"]
    f["z30"] = feat["z30"]
    return f.fillna(0.0)


SIGNALS = {
    "z60": lambda f: -f["z60"],
    "vwapz": lambda f: -f["vwapz"],
    "stretch": lambda f: -(0.5 * f["z60"] + 0.5 * f["vwapz"]),
    "stretch_rng": lambda f: -(0.4 * f["z60"] + 0.3 * f["vwapz"] + 0.3 * f["rng300"] * 2.5),
    "short": lambda f: -(0.6 * f["z10"] + 0.4 * f["z30"]),
}


def run(df, f, spread_bps, *, signal, thr, hold_max, tp, sl, latency, every, min_hold=5, allow_short=True):
    c = df["c"].to_numpy()
    o = df["o"].to_numpy()
    n = len(c)
    half = spread_bps / 2
    s = SIGNALS[signal](f).to_numpy()
    pos, entry_px, entry_i = 0, 0.0, 0
    trades = []
    i, next_dec = 301, 301
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
            elif held >= min_hold and i >= next_dec and s[i] * pos < -thr:
                reason = "flip"
            if reason:
                j = i + 1 + latency
                fill = (o[j] - c[j] * half / 1e4) if pos > 0 else (o[j] + c[j] * half / 1e4)
                trades.append({"side": pos, "net": (np.log(fill / entry_px) * 1e4) * pos, "reason": reason,
                               "held": int(j - entry_i), "entry": int(entry_i)})
                pos = 0
                next_dec = i + every
            i += 1
            continue
        if i >= next_dec:
            next_dec = i + every
            want = 1 if s[i] > thr else -1 if (s[i] < -thr and allow_short) else 0
            if want:
                j = i + 1 + latency
                entry_px = (o[j] + c[j] * half / 1e4) if want > 0 else (o[j] - c[j] * half / 1e4)
                pos, entry_i = want, j
                i = j + 1
                continue
        i += 1
    if not trades:
        return {"trades": 0, "sum": 0.0, "avg": 0.0, "win": 0.0, "reasons": {}}
    t = pd.DataFrame(trades)
    net = t["net"]
    return {"trades": int(len(t)), "sum": float(net.sum()), "avg": float(net.mean()), "win": float((net > 0).mean()),
            "reasons": t["reason"].value_counts().to_dict(), "held": float(t["held"].median()),
            "t": float(net.mean() / net.std() * np.sqrt(len(net))) if net.std() > 0 else 0.0}


PROFILES = {"jev": {"latency": 0, "every": 1}, "opus": {"latency": 3, "every": 4}}
GRID = {"signal": list(SIGNALS), "thr": [1.5, 2.0, 2.5, 3.0], "hold_max": [60, 120, 240],
        "tp": [2.0, 4.0, 8.0], "sl": [4.0, 8.0, 16.0]}

if __name__ == "__main__":
    syms = sys.argv[1].split(",")
    train = sys.argv[2].split(",")
    test = sys.argv[3].split(",") if len(sys.argv) > 3 else []
    data = {}
    for sym in syms:
        for day in train + test:
            df = load_day(sym, day)
            if df is not None:
                ft = features(df)
                data[(sym, day)] = (df, extra(df, ft), load_spread_bps(sym, day))
    rows = []
    for sym in syms:
        for combo in itertools.product(*GRID.values()):
            p = dict(zip(GRID.keys(), combo))
            for prof, pp in PROFILES.items():
                tr = [run(*data[(sym, d)], **p, **pp) for d in train if (sym, d) in data]
                te = [run(*data[(sym, d)], **p, **pp) for d in test if (sym, d) in data]
                rows.append({"sym": sym, "prof": prof, **p,
                             "tr_n": int(np.mean([r["trades"] for r in tr])) if tr else 0,
                             "tr_bps": round(np.mean([r["sum"] for r in tr]), 1) if tr else None,
                             "tr_avg": round(np.mean([r["avg"] for r in tr]), 2) if tr else None,
                             "tr_win": round(np.mean([r["win"] for r in tr]), 2) if tr else None,
                             "te_n": int(np.mean([r["trades"] for r in te])) if te else 0,
                             "te_bps": round(np.mean([r["sum"] for r in te]), 1) if te else None,
                             "te_avg": round(np.mean([r["avg"] for r in te]), 2) if te else None,
                             "te_win": round(np.mean([r["win"] for r in te]), 2) if te else None})
    res = pd.DataFrame(rows)
    res.to_csv(ROOT / "grid2_results.csv", index=False)
    pd.set_option("display.width", 250)
    for sym in syms:
        for prof in PROFILES:
            sub = res[(res.sym == sym) & (res.prof == prof) & (res.tr_n >= 15)].sort_values("tr_bps", ascending=False)
            print(f"\n=== {sym} · {prof} : meilleures en apprentissage (>= 15 trades/jour) ; te_* = validation")
            print(sub.head(10).drop(columns=["sym", "prof"]).to_string(index=False))
