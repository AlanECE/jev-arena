"""Detail jour par jour des reglages candidats sur TSLA (les deux profils de latence)."""
import sys

import numpy as np
import pandas as pd

from grid2 import extra
from grid3 import NONE, PROFILES, run
from study import features, load_day, load_spread_bps

DAYS = sys.argv[1].split(",") if len(sys.argv) > 1 else [
    "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-07", "2026-10-08", "2026-10-09"]
CANDIDATES = [
    dict(sig="z30", thr=2.5, hold=90, tp=8.0, sl=16.0),
    dict(sig="z30", thr=2.5, hold=60, tp=8.0, sl=16.0),
    dict(sig="z30", thr=2.0, hold=90, tp=8.0, sl=16.0),
    dict(sig="z30", thr=2.0, hold=60, tp=8.0, sl=16.0),
    dict(sig="z60", thr=2.0, hold=90, tp=8.0, sl=16.0),
    dict(sig="z30", thr=2.0, hold=90, tp=6.0, sl=12.0),
]
data = {}
for d in DAYS:
    df = load_day("TSLA", d)
    if df is not None:
        data[d] = (df, extra(df, features(df)), load_spread_bps("TSLA", d))
pd.set_option("display.width", 220)
for p in CANDIDATES:
    print(f"\n=== TSLA {p}")
    rows = []
    for prof, pp in PROFILES.items():
        per = {}
        all_trades = []
        for d, (df, ex, sp) in data.items():
            r = run(df, ex, sp, **p, **pp)
            per[d[5:]] = f"{r['sum']:+.0f} ({r['n']})"
            if r["n"]:
                all_trades.append(r["trades"]["net"])
        net = pd.concat(all_trades) if all_trades else pd.Series(dtype=float)
        rows.append({"profil": prof, **per, "total bps": round(float(net.sum()), 0), "trades": int(len(net)),
                     "avg": round(float(net.mean()), 2) if len(net) else None,
                     "win": round(float((net > 0).mean()), 2) if len(net) else None,
                     "t": round(float(net.mean() / net.std() * np.sqrt(len(net))), 2) if len(net) > 1 else None})
    print(pd.DataFrame(rows).to_string(index=False))
