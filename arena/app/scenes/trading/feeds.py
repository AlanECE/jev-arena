"""Flux de prix en direct -> bougies de N secondes.

Sources :
- coinbase      : BTC-USD (ou autre) sur le flux public de Coinbase Exchange. Des trades presque chaque seconde,
                  jour et nuit. Le flux crypto d'Alpaca est trop clairseme la nuit pour des bougies d'1 s
                  (mesure du 12/10 : 32 cotations en 25 s, 0 trade).
- alpaca-crypto : flux crypto d'Alpaca (v1beta3), trades + cotations.
- alpaca-iex    : actions americaines via le flux IEX gratuit d'Alpaca (seance 15h30-22h, heure de Paris).

Les bougies sont fermees par une horloge alignee sur des multiples de bar_sec (heure locale de reception) :
une seconde sans trade donne une bougie plate au dernier prix (volume 0).
"""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import websockets

from ...core.config import ALPACA_KEY, ALPACA_SECRET

SOURCES = {
    "coinbase": {"label": "Coinbase (flux public)", "market": "crypto"},
    "alpaca-crypto": {"label": "Alpaca crypto", "market": "crypto"},
    "alpaca-iex": {"label": "Alpaca IEX (actions)", "market": "stocks"},
}


class BarBuilder:
    def __init__(self, bar_sec: int):
        self.bar_sec = bar_sec
        self.cur = None          # bougie en cours
        self.last_price = None
        self.bid = None
        self.ask = None
        self.history: list[dict] = []

    def bucket(self, ts: float) -> int:
        return int(ts // self.bar_sec * self.bar_sec)

    def add_trade(self, ts: float, price: float, size: float, side: str | None = None):
        # Un trade va toujours dans la bougie ouverte : c'est l'horloge qui decide de la fermer.
        if self.cur is None:
            self.cur = self._new(self.bucket(ts), price)
        c = self.cur
        c["h"] = max(c["h"], price)
        c["l"] = min(c["l"], price)
        c["c"] = price
        c["v"] += size
        c["n"] += 1
        if side == "buy":
            c["vb"] += size
        self.last_price = price

    def add_quote(self, bid: float, ask: float):
        if bid and ask and ask >= bid:
            self.bid, self.ask = bid, ask
            if self.last_price is None:
                self.last_price = (bid + ask) / 2

    def _new(self, t: int, open_price: float) -> dict:
        return {"t": t, "o": open_price, "h": open_price, "l": open_price, "c": open_price,
                "v": 0.0, "vb": 0.0, "n": 0}

    def close(self, t: int) -> dict | None:
        """Ferme la bougie [t, t+bar_sec) et prepare la suivante."""
        if self.last_price is None:
            return None
        if self.cur is None or self.cur["t"] < t:
            bar = self._new(t, self.last_price)
        else:
            bar = self.cur
        bar["t"] = t
        bar["bid"], bar["ask"] = self.bid, self.ask
        self.history.append(bar)
        if len(self.history) > 4000:
            self.history = self.history[-3000:]
        self.cur = self._new(t + self.bar_sec, self.last_price)
        return bar

    def seed(self, trades: list[tuple[float, float, float, str | None]], until: float):
        """Reconstruit l'historique recent (trades REST) en bougies, jusqu'a `until` exclu."""
        trades = sorted(t for t in trades if t[0] < until)
        if not trades:
            return
        start = self.bucket(trades[0][0])
        end = self.bucket(until)
        i = 0
        price = trades[0][1]
        for t in range(start, end, self.bar_sec):
            bar = self._new(t, price)
            while i < len(trades) and trades[i][0] < t + self.bar_sec:
                ts, p, s, side = trades[i]
                bar["h"] = max(bar["h"], p)
                bar["l"] = min(bar["l"], p)
                bar["c"] = p
                bar["v"] += s
                bar["n"] += 1
                if side == "buy":
                    bar["vb"] += s
                price = p
                i += 1
            self.history.append(bar)
        self.last_price = price
        self.cur = self._new(end, price)


def _iso(ts: str) -> float:
    # '2026-10-11T22:10:41.504745Z' (eventuellement nanosecondes)
    ts = ts.rstrip("Z")
    if "." in ts:
        head, frac = ts.split(".", 1)
        ts = head + "." + frac[:6]
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).timestamp()


class Feed:
    """Lance le flux, alimente un BarBuilder et appelle on_bar(bar) a chaque fermeture."""

    def __init__(self, source: str, symbol: str, bar_sec: int, on_bar, on_status):
        self.source = source
        self.symbol = symbol
        self.bar_sec = bar_sec
        self.builder = BarBuilder(bar_sec)
        self.on_bar = on_bar
        self.on_status = on_status
        self.tasks: list[asyncio.Task] = []
        self.msgs = 0
        self.connected = False
        self.xbid = None          # meilleure offre d'achat chez Alpaca (prix auquel on revend)
        self.xask = None          # meilleure offre de vente chez Alpaca (prix auquel on achete)

    async def start(self):
        await self._seed()
        self.tasks = [asyncio.create_task(self._stream_loop()), asyncio.create_task(self._clock())]
        if self.source == "coinbase":
            self.tasks.append(asyncio.create_task(self._alpaca_quote_loop()))

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.tasks = []

    async def _clock(self):
        bs = self.bar_sec
        while True:
            now = time.time()
            nxt = (now // bs + 1) * bs
            await asyncio.sleep(max(0.0, nxt - now) + 0.03)   # 30 ms de marge pour les trades en vol
            bar = self.builder.close(int(nxt - bs))
            if bar is not None:
                if self.source == "coinbase":
                    bar["xbid"], bar["xask"] = self.xbid, self.xask
                elif self.builder.bid and self.builder.ask:
                    self.xbid, self.xask = self.builder.bid, self.builder.ask
                try:
                    await self.on_bar(bar)
                except Exception as e:  # une erreur d'affichage ne doit pas tuer l'horloge
                    await self.on_status(f"erreur on_bar : {e}")

    async def _stream_loop(self):
        while True:
            try:
                if self.source == "coinbase":
                    await self._coinbase()
                else:
                    await self._alpaca()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected = False
                await self.on_status(f"flux {self.source} coupé ({str(e)[:120]}), reconnexion…")
                await asyncio.sleep(2)

    # ---- Coinbase ----
    async def _coinbase(self):
        product = self.symbol.replace("/", "-")
        async with websockets.connect("wss://ws-feed.exchange.coinbase.com", max_size=None, ping_interval=20) as ws:
            await ws.send(json.dumps({"type": "subscribe", "product_ids": [product], "channels": ["matches", "ticker"]}))
            self.connected = True
            await self.on_status(f"flux Coinbase {product} connecté")
            async for raw in ws:
                m = json.loads(raw)
                t = m.get("type")
                self.msgs += 1
                if t in ("match", "last_match"):
                    self.builder.add_trade(time.time(), float(m["price"]), float(m["size"]),
                                           "buy" if m.get("side") == "sell" else "sell")
                    # Coinbase : side = cote du maker ; maker vendeur => taker acheteur
                elif t == "ticker" and m.get("best_bid"):
                    self.builder.add_quote(float(m["best_bid"]), float(m["best_ask"]))

    # ---- Alpaca ----
    async def _alpaca(self):
        if self.source == "alpaca-iex":
            url = "wss://stream.data.alpaca.markets/v2/iex"
        else:
            url = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"
        async with websockets.connect(url, max_size=None, ping_interval=20) as ws:
            await ws.recv()
            await ws.send(json.dumps({"action": "auth", "key": ALPACA_KEY, "secret": ALPACA_SECRET}))
            auth = json.loads(await ws.recv())
            if not any(x.get("msg") == "authenticated" for x in auth):
                raise RuntimeError(f"auth Alpaca refusée : {auth}")
            await ws.send(json.dumps({"action": "subscribe", "trades": [self.symbol], "quotes": [self.symbol]}))
            self.connected = True
            await self.on_status(f"flux {SOURCES[self.source]['label']} {self.symbol} connecté")
            async for raw in ws:
                for m in json.loads(raw):
                    self.msgs += 1
                    if m.get("T") == "t":
                        side = {"B": "buy", "S": "sell"}.get(m.get("tks"))
                        self.builder.add_trade(time.time(), float(m["p"]), float(m["s"]), side)
                    elif m.get("T") == "q":
                        self.builder.add_quote(float(m["bp"]), float(m["ap"]))
                    elif m.get("T") == "error":
                        raise RuntimeError(f"Alpaca : {m}")

    # ---- prix d'execution reel chez Alpaca (le graphique Coinbase sert de reference du marche) ----
    async def _alpaca_quote_loop(self):
        h = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
        url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes"
        async with httpx.AsyncClient(timeout=5, headers=h) as c:
            while True:
                try:
                    r = await c.get(url, params={"symbols": self.symbol})
                    q = (r.json().get("quotes") or {}).get(self.symbol) or {}
                    if q.get("bp") and q.get("ap"):
                        self.xbid, self.xask = float(q["bp"]), float(q["ap"])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                await asyncio.sleep(1.0)

    # ---- historique recent pour ne pas demarrer sur un graphique vide ----
    async def _seed(self):
        now = time.time()
        trades = []
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                if self.source == "coinbase":
                    product = self.symbol.replace("/", "-")
                    r = await c.get(f"https://api.exchange.coinbase.com/products/{product}/trades", params={"limit": 1000})
                    for x in r.json():
                        trades.append((_iso(x["time"]), float(x["price"]), float(x["size"]),
                                       "buy" if x.get("side") == "sell" else "sell"))
                else:
                    h = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
                    start = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
                    if self.source == "alpaca-iex":
                        r = await c.get(f"https://data.alpaca.markets/v2/stocks/{self.symbol}/trades",
                                        params={"start": start, "limit": 10000, "feed": "iex"}, headers=h)
                        rows = r.json().get("trades") or []
                    else:
                        r = await c.get("https://data.alpaca.markets/v1beta3/crypto/us/trades",
                                        params={"symbols": self.symbol, "start": start, "limit": 10000}, headers=h)
                        rows = (r.json().get("trades") or {}).get(self.symbol) or []
                    for x in rows:
                        trades.append((_iso(x["t"]), float(x["p"]), float(x["s"]),
                                       {"B": "buy", "S": "sell"}.get(x.get("tks"))))
        except Exception as e:
            await self.on_status(f"historique indisponible ({str(e)[:100]})")
        # on ne garde que les 10 dernieres minutes
        trades = [t for t in trades if t[0] > now - 600]
        self.builder.seed(trades, until=self.builder.bucket(now))
