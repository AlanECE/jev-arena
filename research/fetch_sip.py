"""Telecharge les trades SIP (toutes bourses) d'Alpaca pour des symboles et des journees, et les agrege en
bougies d'1 seconde avec volume signe (regle du tick) ; echantillonne aussi les cotations NBBO pour estimer
l'ecart achat-vente. Sortie : research/data/<SYM>_<date>_1s.csv et <SYM>_<date>_quotes.csv

Usage : python fetch_sip.py SPY,TSLA,NVDA 2026-10-07,2026-10-08,2026-10-09
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
ENV = {}
for line in (ROOT.parent / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        ENV[k.strip()] = v.strip()
H = {"APCA-API-KEY-ID": ENV["ALPACA_KEY"], "APCA-API-SECRET-KEY": ENV["ALPACA_SECRET"]}
BASE = "https://data.alpaca.markets/v2/stocks"
OUT = ROOT / "data"
OUT.mkdir(exist_ok=True)


def iso_ts(s: str) -> float:
    s = s.rstrip("Z")
    if "." in s:
        a, b = s.split(".", 1)
        s = a + "." + b[:6]
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


def fetch_pages(c, url, params, key):
    token = None
    while True:
        p = dict(params)
        if token:
            p["page_token"] = token
        for attempt in range(6):
            try:
                r = c.get(url, params=p, headers=H, timeout=60)
                if r.status_code == 429:
                    time.sleep(2 + attempt)
                    continue
                r.raise_for_status()
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if attempt == 5:
                    raise
                time.sleep(1.5 * (attempt + 1))
        d = r.json()
        rows = d.get(key) or []
        if isinstance(rows, dict):
            rows = next(iter(rows.values()), [])
        yield from rows
        token = d.get("next_page_token")
        if not token:
            return


def day_bars(sym: str, day: str):
    out = OUT / f"{sym}_{day}_1s.csv"
    if out.exists():
        print("deja la :", out.name)
        return
    start, end = f"{day}T13:30:00Z", f"{day}T20:00:00Z"   # 9:30-16:00 New York (heure d'ete)
    bars = {}
    last_price = None
    n = 0
    t0 = time.time()
    with httpx.Client() as c:
        for t in fetch_pages(c, f"{BASE}/{sym}/trades", {"start": start, "end": end, "limit": 10000, "feed": "sip"}, "trades"):
            conds = t.get("c") or []
            # on ecarte les trades hors marche continu (ouverture/fermeture, corrections, odd lots marques)
            if any(x in conds for x in ("O", "6", "P", "Q", "M", "U", "Z", "4", "9", "X", "W")):
                continue
            ts = iso_ts(t["t"])
            p, s = float(t["p"]), float(t["s"])
            sec = int(ts)
            b = bars.get(sec)
            if b is None:
                b = bars[sec] = {"t": sec, "o": p, "h": p, "l": p, "c": p, "v": 0.0, "vb": 0.0, "vs": 0.0, "n": 0}
            b["h"] = max(b["h"], p)
            b["l"] = min(b["l"], p)
            b["c"] = p
            b["v"] += s
            b["n"] += 1
            if last_price is not None:
                if p > last_price:
                    b["vb"] += s
                elif p < last_price:
                    b["vs"] += s
                else:
                    pass  # tick nul : non signe
            last_price = p
            n += 1
            if n % 200000 == 0:
                print(f"  {sym} {day} : {n} trades, {len(bars)} secondes, {time.time()-t0:.0f} s", flush=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["t", "o", "h", "l", "c", "v", "vb", "vs", "n"])
        w.writeheader()
        for sec in sorted(bars):
            w.writerow(bars[sec])
    print(f"{sym} {day} : {n} trades -> {len(bars)} bougies 1 s en {time.time()-t0:.0f} s", flush=True)


def day_quotes(sym: str, day: str):
    """Echantillon des cotations NBBO : 2 000 cotations toutes les 15 minutes (pour l'ecart et la taille affichee)."""
    out = OUT / f"{sym}_{day}_quotes.csv"
    if out.exists():
        return
    rows = []
    with httpx.Client() as c:
        for h in range(13, 20):
            for m in (30, 45, 0, 15) if h == 13 else (0, 15, 30, 45):
                if h == 13 and m < 30:
                    continue
                if h == 19 and m > 45:
                    continue
                start = f"{day}T{h:02d}:{m:02d}:00Z"
                try:
                    r = c.get(f"{BASE}/{sym}/quotes", params={"start": start, "limit": 2000, "feed": "sip"}, headers=H, timeout=60)
                    for q in (r.json().get("quotes") or []):
                        rows.append({"t": iso_ts(q["t"]), "bp": q["bp"], "ap": q["ap"], "bs": q["bs"], "as": q["as"]})
                except Exception as e:
                    print("quotes", sym, day, start, e)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["t", "bp", "ap", "bs", "as"])
        w.writeheader()
        w.writerows(rows)
    print(f"{sym} {day} : {len(rows)} cotations", flush=True)


if __name__ == "__main__":
    syms = sys.argv[1].split(",")
    days = sys.argv[2].split(",")
    for day in days:
        for sym in syms:
            day_quotes(sym, day)
            day_bars(sym, day)
