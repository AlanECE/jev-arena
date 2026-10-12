"""Etude de strategie de scalping sur bougies d'1 seconde (donnees SIP Alpaca, voir fetch_sip.py).

1. Signaux de micro-structure calcules a chaque seconde (uniquement avec le passe) ;
2. pouvoir predictif de chaque signal sur le rendement des 5 a 60 secondes suivantes (correlation de rang) ;
3. backtest d'une regle de decision avec couts reels (ecart achat-vente NBBO mesure) et latence de decision
   (0,3 s = Jev qui decide a chaque seconde ; 3 s = Opus qui decide toutes les ~4 s).

Usage : python study.py [SPY,TSLA,NVDA] [2026-10-07,2026-10-08,2026-10-09]
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
HORIZONS = (5, 10, 20, 30, 60)


# ----------------------------------------------------------------------------- chargement
def load_day(sym: str, day: str) -> pd.DataFrame | None:
    f = DATA / f"{sym}_{day}_1s.csv"
    if not f.exists():
        return None
    df = pd.read_csv(f).set_index("t")
    # grille complete d'1 s sur la seance (les secondes sans trade gardent le dernier prix, volume 0)
    t0 = int(df.index.min()) // 60 * 60
    t1 = int(df.index.max())
    grid = pd.RangeIndex(t0, t1 + 1)
    df = df.reindex(grid)
    df["c"] = df["c"].ffill()
    for col in ("o", "h", "l"):
        df[col] = df[col].fillna(df["c"])
    df[["v", "vb", "vs", "n"]] = df[["v", "vb", "vs", "n"]].fillna(0.0)
    df = df.dropna(subset=["c"])
    df.index.name = "t"
    return df


def load_spread_bps(sym: str, day: str) -> float:
    f = DATA / f"{sym}_{day}_quotes.csv"
    if not f.exists():
        return 1.0
    q = pd.read_csv(f)
    q = q[(q.ap > 0) & (q.bp > 0) & (q.ap >= q.bp)]
    mid = (q.ap + q.bp) / 2
    return float(((q.ap - q.bp) / mid * 1e4).median())


# ----------------------------------------------------------------------------- signaux
def features(df: pd.DataFrame) -> pd.DataFrame:
    c = df["c"]
    lc = np.log(c)
    out = pd.DataFrame(index=df.index)
    for k in (1, 3, 5, 10, 30, 60):
        out[f"r{k}"] = (lc - lc.shift(k)) * 1e4
    # desequilibre des flux agressifs (regle du tick) sur k secondes, borne [-1, 1]
    for k in (3, 10, 30, 60):
        vb = df["vb"].rolling(k).sum()
        vs = df["vs"].rolling(k).sum()
        out[f"ofi{k}"] = (vb - vs) / (vb + vs).replace(0, np.nan)
    # volatilite (ecart-type des rendements 1 s sur 60 s, en bps) et intensite des echanges
    r1 = (lc - lc.shift(1)) * 1e4
    out["vol60"] = r1.rolling(60).std()
    out["nz60"] = (df["n"] - df["n"].rolling(60).mean()) / df["n"].rolling(60).std().replace(0, np.nan)
    out["vz60"] = (df["v"] - df["v"].rolling(60).mean()) / df["v"].rolling(60).std().replace(0, np.nan)
    # moyennes mobiles et VWAP de seance
    e5 = c.ewm(span=5, adjust=False).mean()
    e20 = c.ewm(span=20, adjust=False).mean()
    e60 = c.ewm(span=60, adjust=False).mean()
    out["ema5_20"] = (e5 / e20 - 1) * 1e4
    out["ema20_60"] = (e20 / e60 - 1) * 1e4
    tp = (df["h"] + df["l"] + df["c"]) / 3
    cum_pv = (tp * df["v"]).cumsum()
    cum_v = df["v"].cumsum().replace(0, np.nan)
    out["vwap_dev"] = (c / (cum_pv / cum_v) - 1) * 1e4
    hi60 = df["h"].rolling(60).max()
    lo60 = df["l"].rolling(60).min()
    out["rng60"] = ((c - lo60) / (hi60 - lo60).replace(0, np.nan) - 0.5) * 2   # -1 bas .. +1 haut
    # rendement normalise par la volatilite (z-score du mouvement recent)
    out["z10"] = out["r10"] / (out["vol60"] * np.sqrt(10)).replace(0, np.nan)
    out["z30"] = out["r30"] / (out["vol60"] * np.sqrt(30)).replace(0, np.nan)
    # interaction : mouvement recent x flux (continuation si le flux pousse dans le sens du mouvement)
    out["mom_flow10"] = np.sign(out["r10"]) * out["ofi10"]
    return out


def targets(df: pd.DataFrame) -> pd.DataFrame:
    lc = np.log(df["c"])
    return pd.DataFrame({f"f{h}": (lc.shift(-h) - lc) * 1e4 for h in HORIZONS}, index=df.index)


def ic_table(feat: pd.DataFrame, tgt: pd.DataFrame) -> pd.DataFrame:
    rows = {}
    for col in feat.columns:
        rows[col] = {h: feat[col].corr(tgt[f"f{h}"], method="spearman") for h in HORIZONS}
    return pd.DataFrame(rows).T


# ----------------------------------------------------------------------------- regle de decision
def score(feat: pd.DataFrame, w: dict) -> pd.Series:
    """Score lineaire (en unites de z) : positif = acheter, negatif = vendre."""
    s = pd.Series(0.0, index=feat.index)
    for k, v in w.items():
        s = s + v * feat[k].fillna(0.0)
    return s


def backtest(df: pd.DataFrame, sc: pd.Series, spread_bps: float, *, thr: float, hold_max: int, tp: float,
             sl: float, latency: int, every: int, min_hold: int = 3, flip: bool = True, allow_short=True,
             fee_bps: float = 0.0) -> dict:
    """Decision a la seconde t (toutes les `every` s), execution a t+latency a l'open de la bougie suivante +/- demi-ecart.
    Sortie par prise de profit / stop / duree max / signal inverse fort. Rendements en bps du notionnel."""
    c = df["c"].to_numpy()
    o = df["o"].to_numpy()
    n = len(c)
    half = spread_bps / 2 + fee_bps
    s = sc.to_numpy()
    pos = 0
    entry_px = 0.0
    entry_i = 0
    trades = []
    i = 0
    next_decision = 0
    while i < n - latency - 1:
        if pos != 0:
            # gestion de la position a chaque seconde (sorties mecaniques : stop, objectif, duree)
            px = c[i]
            pnl = (np.log(px / entry_px) * 1e4) * pos
            held = i - entry_i
            exit_reason = None
            if pnl >= tp:
                exit_reason = "tp"
            elif pnl <= -sl:
                exit_reason = "sl"
            elif held >= hold_max:
                exit_reason = "time"
            elif flip and held >= min_hold and i >= next_decision and s[i] * pos < -thr:
                exit_reason = "flip"
            if exit_reason:
                j = i + latency
                fill = o[j + 1] if j + 1 < n else c[j]
                gross = (np.log(fill / entry_px) * 1e4) * pos
                net = gross - 2 * half
                trades.append({"side": pos, "entry": entry_i, "exit": j, "gross": gross, "net": net,
                               "reason": exit_reason, "held": j - entry_i})
                pos = 0
                next_decision = i + every
                i += 1
                continue
            i += 1
            continue
        if i >= next_decision:
            next_decision = i + every
            want = 1 if s[i] > thr else -1 if (s[i] < -thr and allow_short) else 0
            if want != 0:
                j = i + latency
                fill = o[j + 1] if j + 1 < n else c[j]
                pos = want
                entry_px = fill
                entry_i = j
                i = j + 1
                continue
        i += 1
    if not trades:
        return {"trades": 0}
    t = pd.DataFrame(trades)
    net = t["net"]
    return {
        "trades": int(len(t)), "win_rate": float((net > 0).mean()), "avg_net_bps": float(net.mean()),
        "avg_gross_bps": float(t["gross"].mean()), "sum_net_bps": float(net.sum()),
        "median_held_s": float(t["held"].median()),
        "pnl_10k_usd": float(net.sum() * 10_000 / 1e4),
        "by_reason": t.groupby("reason")["net"].agg(["count", "mean"]).round(2).to_dict(),
        "sharpe_like": float(net.mean() / net.std() * np.sqrt(len(net))) if net.std() > 0 else 0.0,
    }


# ----------------------------------------------------------------------------- pilotage
def zscore_by_day(feat: pd.DataFrame, cols) -> pd.DataFrame:
    z = feat.copy()
    for col in cols:
        m, sd = feat[col].mean(), feat[col].std()
        z[col] = (feat[col] - m) / (sd if sd and sd > 0 else 1.0)
    return z


if __name__ == "__main__":
    syms = (sys.argv[1] if len(sys.argv) > 1 else "SPY,TSLA,NVDA").split(",")
    days = (sys.argv[2] if len(sys.argv) > 2 else "2026-10-07,2026-10-08,2026-10-09").split(",")
    report = {"ic": {}, "spread_bps": {}, "backtests": {}}
    ic_all = []
    datasets = {}
    for sym in syms:
        for day in days:
            df = load_day(sym, day)
            if df is None:
                continue
            feat = features(df)
            tgt = targets(df)
            ic = ic_table(feat, tgt)
            ic_all.append(ic.assign(sym=sym, day=day))
            report["ic"][f"{sym}_{day}"] = ic.round(4).to_dict()
            report["spread_bps"][f"{sym}_{day}"] = load_spread_bps(sym, day)
            datasets[(sym, day)] = (df, feat)
            print(f"\n=== {sym} {day} : {len(df)} s, ecart NBBO median {report['spread_bps'][f'{sym}_{day}']:.2f} bps")
            print(ic.round(3).to_string())
    if ic_all:
        big = pd.concat(ic_all)
        mean_ic = big.drop(columns=["sym", "day"]).groupby(level=0).mean()
        print("\n=== IC moyen (toutes journees)")
        print(mean_ic.round(3).to_string())
        report["ic_mean"] = mean_ic.round(4).to_dict()
    (ROOT / "report_ic.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("\nrapport : research/report_ic.json")
