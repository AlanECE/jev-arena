"""Compte Alpaca (paper) : ordres REST + flux trade_updates (executions en direct).

Un compte peut porter un ou deux fonds (Jev, Opus). Les ordres sont etiquetes par client_order_id
(`jev-...`, `opus-...`) : chaque execution est rendue au bon fonds.
"""
import asyncio
import json
import time
import uuid

import httpx
import websockets


class AlpacaError(RuntimeError):
    pass


class AlpacaAccount:
    def __init__(self, name: str, key: str, secret: str, base: str):
        self.name = name
        self.key = key
        self.secret = secret
        self.base = base.rstrip("/")
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.http = httpx.AsyncClient(base_url=self.base, headers=self.headers, timeout=15)
        self.handlers = {}          # prefixe client_order_id -> coroutine(event)
        self.waiters: dict[str, asyncio.Future] = {}
        self.task = None
        self.connected = False
        self.last_error = None
        self.orders_sent = 0
        self.req_times: list[float] = []
        self.pos: dict[str, float] = {}   # position du compte par symbole, suivie via les executions

    # ---------- REST ----------
    async def _req(self, method, path, **kw):
        self.req_times = [t for t in self.req_times if t > time.time() - 60] + [time.time()]
        r = await self.http.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message") or r.text
            except Exception:
                msg = r.text
            raise AlpacaError(f"{r.status_code} : {str(msg)[:200]}")
        return r.json() if r.content else {}

    async def account(self):
        return await self._req("GET", "/v2/account")

    async def clock(self):
        return await self._req("GET", "/v2/clock")

    async def positions(self):
        return await self._req("GET", "/v2/positions")

    async def asset(self, symbol: str):
        return await self._req("GET", f"/v2/assets/{symbol.replace('/', '%2F')}")

    async def close_position(self, symbol: str):
        sym = symbol.replace("/", "")
        try:
            return await self._req("DELETE", f"/v2/positions/{sym}")
        except AlpacaError as e:
            if "404" in str(e) or "position does not exist" in str(e).lower():
                return None
            raise

    async def cancel_all(self):
        return await self._req("DELETE", "/v2/orders")

    async def submit(self, prefix: str, symbol: str, side: str, qty: float, crypto: bool) -> dict:
        cid = f"{prefix}-{uuid.uuid4().hex[:18]}"
        body = {"symbol": symbol, "side": side, "type": "market",
                "time_in_force": "gtc" if crypto else "day", "client_order_id": cid}
        if crypto:
            q = int(qty * 1e9) / 1e9          # arrondi vers le bas : jamais plus que le solde
            body["qty"] = f"{q:.9f}".rstrip("0").rstrip(".")
        else:
            body["qty"] = str(int(round(qty)))
        fut = asyncio.get_running_loop().create_future()
        self.waiters[cid] = fut
        try:
            order = await self._req("POST", "/v2/orders", json=body)
        except Exception:
            self.waiters.pop(cid, None)
            raise
        self.orders_sent += 1
        order["_future"] = fut
        return order

    # ---------- flux des executions ----------
    def register(self, prefix: str, handler):
        self.handlers[prefix] = handler

    def start(self):
        if not self.task:
            self.task = asyncio.create_task(self._updates_loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None

    async def _updates_loop(self):
        url = self.base.replace("https://", "wss://") + "/stream"
        while True:
            try:
                async with websockets.connect(url, max_size=None, ping_interval=20) as ws:
                    await ws.send(json.dumps({"action": "auth", "key": self.key, "secret": self.secret}))
                    await ws.recv()
                    await ws.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
                    self.connected = True
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "replace")
                        m = json.loads(raw)
                        if m.get("stream") != "trade_updates":
                            continue
                        await self._dispatch(m.get("data") or {})
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected = False
                self.last_error = str(e)[:200]
                await asyncio.sleep(2)

    def set_position(self, symbol: str, qty: float):
        self.pos[symbol] = qty

    async def position_qty(self, symbol: str) -> float:
        sym = symbol.replace("/", "")
        try:
            p = await self._req("GET", f"/v2/positions/{sym}")
            return float(p.get("qty") or 0)
        except AlpacaError:
            return 0.0

    async def _dispatch(self, d: dict):
        order = d.get("order") or {}
        cid = order.get("client_order_id") or ""
        # Quantite reellement creditee/debitee sur le compte : en crypto, Alpaca preleve ses frais dans l'actif
        # recu (un achat de 0,1197 BTC credite ~0,1194 BTC). position_qty = position du compte apres l'execution.
        if d.get("event") in ("fill", "partial_fill") and d.get("position_qty") is not None:
            sym = order.get("symbol") or ""
            new_pos = float(d["position_qty"])
            if sym in self.pos:
                d["_delta"] = new_pos - self.pos[sym]
            self.pos[sym] = new_pos
        prefix = cid.split("-", 1)[0]
        h = self.handlers.get(prefix)
        if h:
            try:
                await h(d)
            except Exception as e:
                self.last_error = f"handler : {e}"
        ev = d.get("event")
        if ev in ("fill", "canceled", "rejected", "expired", "done_for_day") and cid in self.waiters:
            fut = self.waiters.pop(cid)
            if not fut.done():
                fut.set_result(d)
