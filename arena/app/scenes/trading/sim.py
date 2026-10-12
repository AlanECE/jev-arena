"""Courtier SIMULE : utilise quand aucune cle Alpaca n'est fournie (version hebergee pour le public).

Meme interface que AlpacaAccount (submit / cancel_all / close_position / clock / asset / account / register / start / stop),
mais les ordres au marche sont executes sur place au prix du flux (offre de vente pour un achat, offre d'achat pour une
vente ; sinon dernier prix +/- un demi-ecart par defaut), et l'execution est renvoyee au fonds par le meme
mecanisme `trade_updates` (handler enregistre par prefixe de client_order_id).
La vente a decouvert est autorisee (c'est une simulation), les frais sont a 0 : seul l'ecart achat-vente est paye.
"""
import asyncio
import time
import uuid

DEFAULT_HALF_SPREAD_BPS = 0.5


class SimAccount:
    name = "simulé"

    def __init__(self, scene):
        self.scene = scene
        self.handlers = {}
        self.waiters: dict[str, asyncio.Future] = {}
        self.connected = True
        self.last_error = None
        self.orders_sent = 0
        self.req_times: list[float] = []
        self.pos: dict[str, float] = {}
        self.cash = 100_000.0

    # ---------- informations ----------
    async def account(self):
        return {"status": "SIMULÉ", "equity": self.cash, "cash": self.cash, "currency": "USD"}

    async def clock(self):
        return {"is_open": True, "next_open": None}

    async def asset(self, symbol: str):
        return {"symbol": symbol, "shortable": True, "easy_to_borrow": True, "tradable": True}

    async def positions(self):
        return [{"symbol": s, "qty": str(q)} for s, q in self.pos.items() if abs(q) > 1e-12]

    async def position_qty(self, symbol: str) -> float:
        return self.pos.get(symbol.replace("/", ""), 0.0)

    def set_position(self, symbol: str, qty: float):
        self.pos[symbol.replace("/", "")] = qty

    async def cancel_all(self):
        return {}

    async def close_position(self, symbol: str):
        sym = symbol.replace("/", "")
        q = self.pos.get(sym, 0.0)
        if abs(q) < 1e-12:
            return None
        # fermeture au prix du flux, rendue a tous les fonds au prorata de leur position
        for prefix, h in list(self.handlers.items()):
            f = self.scene.funds.get(prefix)
            if f and abs(f.qty) > 1e-12:
                await self.submit(prefix, symbol, "sell" if f.qty > 0 else "buy", abs(f.qty), self.scene.crypto)
        return {}

    # ---------- ordres ----------
    def _px(self, side: str) -> float:
        feed = self.scene.feed
        b = feed.builder if feed else None
        last = b.last_price if b else None
        bid = (b.bid if b and b.bid else (feed.xbid if feed else None))
        ask = (b.ask if b and b.ask else (feed.xask if feed else None))
        if side == "buy":
            return ask if ask else last * (1 + DEFAULT_HALF_SPREAD_BPS / 1e4)
        return bid if bid else last * (1 - DEFAULT_HALF_SPREAD_BPS / 1e4)

    async def submit(self, prefix: str, symbol: str, side: str, qty: float, crypto: bool) -> dict:
        cid = f"{prefix}-{uuid.uuid4().hex[:18]}"
        self.req_times = [t for t in self.req_times if t > time.time() - 60] + [time.time()]
        self.orders_sent += 1
        fut = asyncio.get_running_loop().create_future()
        price = self._px(side)
        if not price:
            raise RuntimeError("pas de prix disponible (flux non connecté)")
        sym = symbol.replace("/", "")
        signed = qty if side == "buy" else -qty
        self.pos[sym] = self.pos.get(sym, 0.0) + signed
        self.cash -= signed * price
        order = {"client_order_id": cid, "symbol": sym, "side": side, "qty": str(qty), "filled_qty": str(qty),
                 "filled_avg_price": str(price), "status": "filled"}
        event = {"event": "fill", "qty": str(qty), "price": str(price), "position_qty": str(self.pos[sym]),
                 "order": order}
        fut.set_result(event)
        # execution rendue de facon asynchrone, comme le flux trade_updates d'Alpaca
        asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self._dispatch(prefix, event)))
        return {**order, "_future": fut}

    async def _dispatch(self, prefix: str, event: dict):
        h = self.handlers.get(prefix)
        if h:
            try:
                await h(event)
            except Exception as e:
                self.last_error = f"handler : {e}"

    # ---------- flux ----------
    def register(self, prefix: str, handler):
        self.handlers[prefix] = handler

    def start(self):
        pass

    async def stop(self):
        pass
