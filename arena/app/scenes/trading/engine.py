"""Scene trading : deux fonds (Jev a gauche, Opus a droite) qui scalpent en direct sur Alpaca paper.

A chaque fermeture de bougie (1 s par defaut), chaque fonds demande une decision a SON modele s'il est libre.
S'il attend encore la reponse precedente, la bougie est comptee comme "ratee" : c'est ce qui montre qu'Opus
ne peut pas prendre autant de decisions que Jev. Les decisions deviennent de vrais ordres au marche sur
Alpaca paper ; les positions, prix d'entree et gains viennent des executions reelles renvoyees par Alpaca.
"""
import asyncio
import statistics
import time

from fastapi import APIRouter

from ...core import config as C
from ...core.deciders import BudgetExceeded, decide, top_choice
from ...core.hub import HUB, SceneBase
from ...core.ledger import LEDGER_OBJ
from . import strategies as strategies_pkg
from .alpaca import AlpacaAccount, AlpacaError
from .sim import SimAccount
from .feeds import SOURCES, Feed
from .strategies.base import FundView, MarketView

MODELS = ("jev", "opus")
EPS = 1e-9
CRYPTO_FEE_BPS = 25.0   # Alpaca crypto, palier 1, taker (0,25 % par ordre)

DEFAULTS = {
    "source": "coinbase",
    "symbol": "BTC/USD",
    "broker": "sim",            # "sim" = courtier simule (24 h/24, long et short) ; "alpaca" = ordres Alpaca paper
    "bar_sec": 1,
    "strategy": "rupture",
    "position_usd": 100_000,
    "duration_min": 10,     # arret automatique (0 = illimite)
    "fee_bps": None,        # None = 25 bps en crypto, 0 en actions
}


def _sign(x):
    return 1 if x > EPS else -1 if x < -EPS else 0


class Fund:
    def __init__(self, scene: "TradingScene", model: str):
        self.scene = scene
        self.model = model
        self.account: AlpacaAccount | None = None
        self.reset()

    def reset(self):
        self.qty = 0.0
        self.avg = None
        self.realized = 0.0
        self.fees = 0.0
        self.open_trade = None
        self.trades = []
        self.markers = []
        self.busy = False
        self.busy_since = None
        self.pending = None
        self.decisions = 0
        self.ok = 0
        self.errors = 0
        self.missed = 0
        self.lat = []
        self.api_cost = 0.0
        self.orders = 0
        self.rejected = 0
        self.tape = []
        self.decision_times = []
        self.order_times = []
        self.entry_time = None
        self.last = None
        self.last_choices = []
        self.blocked = None
        self.exit_reason = None
        self.exit_plan = None       # {hold_until, tp_price, sl_price} de la position en cours

    @property
    def side(self):
        s = _sign(self.qty)
        return "long" if s > 0 else "short" if s < 0 else "flat"

    def unrealized(self, price):
        """Gain latent au prix ou la position peut vraiment etre soldee chez Alpaca
        (offre d'achat pour un long, offre de vente pour un short), sinon au dernier prix."""
        if self.avg is None or abs(self.qty) < EPS:
            return 0.0, 0.0
        feed = self.scene.feed
        if feed is not None:
            mark = feed.xbid if self.qty > 0 else feed.xask
            if mark:
                price = mark
        if price is None:
            return 0.0, 0.0
        usd = (price - self.avg) * self.qty
        bps = (price / self.avg - 1) * 1e4 * _sign(self.qty)
        return usd, bps

    def view(self, price) -> FundView:
        _, bps = self.unrealized(price)
        return FundView(model=self.model, side=self.side, qty=self.qty, entry_price=self.avg, unrealized_bps=bps,
                        seconds_in_position=(time.time() - self.entry_time) if self.entry_time else 0.0,
                        realized_pnl=self.realized, trades=len(self.trades), last_choices=self.last_choices[-5:])

    def stats(self, price):
        u_usd, u_bps = self.unrealized(price)
        now = time.time()
        self.decision_times = [t for t in self.decision_times if t > now - 60]
        lat = self.lat[-300:]
        wins = sum(1 for t in self.trades if t["net"] > 0)
        return {
            "model": self.model, "side": self.side, "qty": self.qty, "entry": self.avg, "exit_plan": self.exit_plan,
            "exit_reason": self.exit_reason,
            "unrealized": u_usd, "unrealized_bps": u_bps, "realized": self.realized, "fees": self.fees,
            "net": self.realized + u_usd - self.fees,
            "decisions": self.decisions, "ok": self.ok, "errors": self.errors, "missed": self.missed,
            "per_min": len(self.decision_times),
            "lat_med": statistics.median(lat) if lat else None, "lat_last": lat[-1] if lat else None,
            "api_cost": self.api_cost, "orders": self.orders, "rejected": self.rejected,
            "trades": len(self.trades), "wins": wins,
            "busy": self.busy, "busy_since": self.busy_since, "pending": self.pending is not None,
            "blocked": self.blocked,
        }

    # ---------- executions renvoyees par Alpaca ----------
    async def on_update(self, d: dict):
        ev = d.get("event")
        order = d.get("order") or {}
        if ev in ("fill", "partial_fill"):
            qty = float(d.get("qty") or order.get("filled_qty") or 0)
            price = float(d.get("price") or order.get("filled_avg_price") or 0)
            signed = qty if order.get("side") == "buy" else -qty
            credited = d.get("_delta")
            fee_in_kind = 0.0
            if self.scene.crypto and signed > 0 and credited is not None and 0 < credited <= signed:
                # frais preleves en BTC a l'achat : la position reelle est la quantite creditee
                fee_in_kind = (signed - credited) * price
                signed = credited
            await self.apply_fill(signed, price, order, fee_in_kind=fee_in_kind)
        elif ev in ("rejected", "canceled", "expired"):
            self.rejected += 1
            await self.scene.emit({"type": "order", "fund": self.model, "status": ev,
                                   "side": order.get("side"), "qty": order.get("qty"),
                                   "reason": (d.get("order") or {}).get("status")})

    async def apply_fill(self, signed: float, price: float, order: dict, fee_in_kind: float | None = None):
        sc = self.scene
        now = time.time()
        # frais : reels quand Alpaca les preleve dans l'actif recu (achat crypto), sinon estimes (fee_bps)
        fee = fee_in_kind if fee_in_kind else abs(signed) * price * sc.fee_bps / 1e4
        self.fees += fee
        q0 = self.qty
        events = []
        if _sign(q0) == 0 or _sign(q0) == _sign(signed):
            new = q0 + signed
            self.avg = ((abs(q0) * (self.avg or 0)) + abs(signed) * price) / abs(new)
            self.qty = new
            if _sign(q0) == 0:
                self.entry_time = now
                self.open_trade = {"side": "long" if signed > 0 else "short", "entry_t": now, "entry_price": price,
                                   "qty": abs(signed), "fees": fee, "pnl": 0.0, "exit_qty": 0.0, "exit_value": 0.0,
                                   "decision": self.last}
                ex = sc.exit_rules()
                sd = 1 if signed > 0 else -1
                self.exit_plan = {
                    "hold_until": (now + ex["max_hold_s"]) if ex.get("max_hold_s") else None,
                    "tp_price": price * (1 + sd * ex["take_profit_bps"] / 1e4) if ex.get("take_profit_bps") else None,
                    "sl_price": price * (1 - sd * ex["stop_loss_bps"] / 1e4) if ex.get("stop_loss_bps") else None,
                }
                m = {"t": sc.bar_t(now), "kind": "open", "side": self.open_trade["side"], "price": price,
                     **self.exit_plan}
                self.markers.append(m)
                events.append({"type": "marker", "fund": self.model, **m})
            else:
                self.open_trade["qty"] += abs(signed)
                self.open_trade["fees"] += fee
                self.open_trade["entry_price"] = self.avg
        else:
            closing = min(abs(signed), abs(q0))
            pnl = (price - self.avg) * closing * _sign(q0)
            self.realized += pnl
            ot = self.open_trade
            if ot:
                ot["pnl"] += pnl
                ot["fees"] += fee
                ot["exit_qty"] += closing
                ot["exit_value"] += closing * price
            self.qty = q0 + _sign(signed) * closing
            if abs(self.qty) < 1e-7:
                self.qty = 0.0
                if ot:
                    ot.update({"exit_t": now, "exit_price": ot["exit_value"] / max(ot["exit_qty"], EPS),
                               "duration": now - ot["entry_t"], "net": ot["pnl"] - ot["fees"]})
                    tr = {k: ot[k] for k in ("side", "entry_t", "entry_price", "exit_t", "exit_price", "qty",
                                             "pnl", "fees", "net", "duration")}
                    tr["decision"] = ot.get("decision")
                    tr["reason"] = self.exit_reason
                    self.trades.append(tr)
                    self.exit_reason = None
                    self.exit_plan = None
                    m = {"t": sc.bar_t(now), "kind": "close", "side": ot["side"], "price": price, "net": tr["net"]}
                    self.markers.append(m)
                    events.append({"type": "marker", "fund": self.model, **m})
                    events.append({"type": "trade", "fund": self.model, **tr})
                self.open_trade = None
                self.avg = None
                self.entry_time = None
            rest = abs(signed) - closing
            if rest > 1e-7:   # ne devrait pas arriver (les ordres qui croisent zero sont coupes en deux)
                await self.apply_fill(_sign(signed) * rest, price, order)
        await sc.emit({"type": "order", "fund": self.model, "status": "filled", "side": order.get("side"),
                       "qty": abs(signed), "price": price, "t": now, "fee": fee})
        for e in events:
            await sc.emit(e)

    # ---------- decision a chaque bougie ----------
    async def on_bar(self, bar: dict, mv: MarketView):
        strat = self.scene.strategy
        if (self.side != "flat" or self.pending is not None) and not getattr(strat, "decide_when_in_position", True):
            await self._manage(bar, mv)
            return
        if self.busy:
            self.missed += 1
            entry = {"t": bar["t"], "k": "missed"}
            self.tape.append(entry)
            self.tape = self.tape[-400:]
            await self.scene.emit({"type": "tape", "fund": self.model, **entry})
            return
        self.busy = True
        self.busy_since = time.time()
        await self.scene.emit({"type": "thinking", "fund": self.model, "t": bar["t"], "since": self.busy_since})
        task = asyncio.create_task(self._decide(bar, mv))
        self.scene.tasks.add(task)
        task.add_done_callback(self.scene.tasks.discard)

    async def _manage(self, bar, mv: MarketView):
        """Position en cours : sorties mecaniques identiques pour les deux fonds (duree, objectif, stop)."""
        sc = self.scene
        _, bps = self.unrealized(mv.price)
        held = time.time() - (self.entry_time or time.time())
        entry = {"t": bar["t"], "k": "hold", "bps": round(bps, 2), "held": round(held)}
        self.tape.append(entry)
        self.tape = self.tape[-400:]
        await sc.emit({"type": "tape", "fund": self.model, **entry})
        if self.pending is not None or self.side == "flat":
            return
        ex = sc.exit_rules()
        reason = None
        if ex.get("take_profit_bps") and bps >= ex["take_profit_bps"]:
            reason = "objectif"
        elif ex.get("stop_loss_bps") and bps <= -ex["stop_loss_bps"]:
            reason = "stop"
        elif ex.get("max_hold_s") and held >= ex["max_hold_s"]:
            reason = "durée"
        if reason:
            self.exit_reason = reason
            await sc.emit({"type": "exit", "fund": self.model, "reason": reason, "bps": round(bps, 2), "t": bar["t"],
                           "held": round(held)})
            await self.apply_target("flat", mv, force=True)

    async def _decide(self, bar, mv: MarketView):
        sc = self.scene
        strat = sc.strategy
        fv = self.view(mv.price)
        try:
            state = strat.state(mv, fv)
            questions = strat.questions(mv, fv)
            r = await decide(self.model, "trading", state, questions, tag=f"trade-{self.model}")
        except BudgetExceeded as e:
            self.busy = False
            await sc.emit({"type": "status", "msg": f"⚠ {e}"})
            asyncio.create_task(sc.stop(reason="plafond de dépense atteint"))
            return
        except Exception as e:
            r = {"ok": False, "error": f"stratégie : {e}", "answers": {}, "latency_ms": 0, "cost": 0}
        finally:
            self.busy = False
            self.busy_since = None
        if not sc.running:
            return
        self.decisions += 1
        self.decision_times.append(time.time())
        self.api_cost += r.get("cost") or 0
        if r.get("latency_ms"):
            self.lat.append(r["latency_ms"])
        if r.get("ok"):
            self.ok += 1
        else:
            self.errors += 1
            err = str(r.get("error") or "")
            if "402" in err or "credit" in err.lower() or "insufficient" in err.lower():
                await sc.emit({"type": "status", "msg": "⚠ crédits OpenRouter épuisés : recharge ta clé sur openrouter.ai/credits"})
                asyncio.create_task(sc.stop(reason="crédits OpenRouter épuisés"))
                return
        target = None
        choice, prob = None, None
        if r.get("ok"):
            first_choice = next((k for k, q in questions.items() if q.get("type") == "choice"), None)
            if first_choice:
                choice, prob = top_choice(r["answers"].get(first_choice))
            try:
                target = strat.target(r["answers"], mv, fv)
            except Exception as e:
                r["error"] = f"stratégie : {e}"
        self.last = {"t": bar["t"], "choice": choice, "prob": prob, "lat": r.get("latency_ms"),
                     "price": mv.price, "ok": r.get("ok")}
        self.last_choices.append(choice)
        self.last_choices = self.last_choices[-20:]
        entry = {"t": bar["t"], "k": choice if r.get("ok") else "error", "lat": r.get("latency_ms"),
                 "prob": prob, "at": time.time()}
        self.tape.append(entry)
        self.tape = self.tape[-400:]
        await sc.emit({"type": "decision", "fund": self.model, "t": bar["t"], "ok": r.get("ok"),
                       "choice": choice, "prob": prob, "declared": self.model == "opus",
                       "latency_ms": r.get("latency_ms"), "cost": r.get("cost"), "error": r.get("error"),
                       "target": target, "price_at_request": mv.price, "answered_at": time.time()})
        await self.apply_target(target, mv)

    async def apply_target(self, target, mv: MarketView, force: bool = False):
        sc = self.scene
        if target is None or not sc.running:
            return
        if target == "short" and not mv.allow_short:
            target = "flat"
        if target == self.side:
            self.blocked = None
            return
        if self.pending is not None:
            self.blocked = "ordre en cours"
            return
        risk = sc.strategy.risk
        now = time.time()
        if not force and self.side != "flat" and self.entry_time and now - self.entry_time < risk.min_hold_s:
            self.blocked = f"tenue minimale {risk.min_hold_s:g} s"
            return
        self.order_times = [t for t in self.order_times if t > now - 60]
        if len(self.order_times) >= risk.max_orders_per_min:
            self.blocked = f"max {risk.max_orders_per_min} ordres/min"
            return
        self.blocked = None
        size = sc.position_usd / max(mv.price, EPS)
        size = round(size, 6) if sc.crypto else float(int(size))
        if size <= 0:
            return
        desired = size if target == "long" else -size if target == "short" else 0.0
        legs = []
        if _sign(self.qty) != 0 and _sign(desired) != _sign(self.qty):
            legs.append(-self.qty)                      # fermer d'abord
            if _sign(desired) != 0:
                legs.append(desired)                    # puis ouvrir dans l'autre sens
        else:
            legs.append(desired - self.qty)
        legs = [x for x in legs if abs(x) > EPS]
        if not legs:
            return
        self.pending = asyncio.create_task(self._execute(legs, target))
        sc.tasks.add(self.pending)
        self.pending.add_done_callback(sc.tasks.discard)

    async def _execute(self, legs, target):
        sc = self.scene
        try:
            for leg in legs:
                for part in sc.split_for_account(self, leg):
                    side = "buy" if part > 0 else "sell"
                    t_sub = time.time()
                    self.orders += 1
                    self.order_times.append(t_sub)
                    try:
                        order = await self.account.submit(self.model, sc.symbol, side, abs(part), sc.crypto)
                    except AlpacaError as e:
                        self.rejected += 1
                        await sc.emit({"type": "order", "fund": self.model, "status": "rejected", "side": side,
                                       "qty": abs(part), "reason": str(e)})
                        return
                    await sc.emit({"type": "order", "fund": self.model, "status": "submitted", "side": side,
                                   "qty": abs(part), "t": t_sub, "target": target})
                    try:
                        res = await asyncio.wait_for(order["_future"], timeout=20)
                    except asyncio.TimeoutError:
                        await sc.emit({"type": "order", "fund": self.model, "status": "timeout", "side": side,
                                       "qty": abs(part), "reason": "pas d'exécution en 20 s"})
                        return
                    if res.get("event") != "fill":
                        return
        finally:
            self.pending = None


class TradingScene(SceneBase):
    name = "trading"
    title = "Trading"

    def __init__(self):
        self.cfg = dict(DEFAULTS)
        self.funds = {m: Fund(self, m) for m in MODELS}
        self.feed: Feed | None = None
        self.accounts: dict[str, AlpacaAccount] = {}
        self.strategies = {}
        self.strategy = None
        self.running = False
        self.started_at = None
        self.stop_at = None
        self.tasks: set = set()
        self.messages = []
        self.allow_short = False
        self.fee_bps = CRYPTO_FEE_BPS
        self.shared = True
        self.account_info = {}

    # ---------- proprietes ----------
    @property
    def market(self):
        return SOURCES[self.cfg["source"]]["market"]

    @property
    def crypto(self):
        return self.market == "crypto"

    @property
    def symbol(self):
        return self.cfg["symbol"]

    @property
    def bar_sec(self):
        return int(self.cfg["bar_sec"])

    @property
    def position_usd(self):
        return float(self.cfg["position_usd"])

    def bar_t(self, ts):
        return int(ts // self.bar_sec * self.bar_sec)

    # ---------- cycle de vie ----------
    async def startup(self):
        self.strategies = strategies_pkg.load_all()
        self.strategy = self.strategies.get(self.cfg["strategy"]) or next(iter(self.strategies.values()), None)
        await self._make_accounts()
        await self._restart_feed()

    async def shutdown(self):
        await self.stop(reason="arrêt du serveur", flatten=False)
        if self.feed:
            await self.feed.stop()
        for a in self.accounts.values():
            await a.stop()

    @property
    def has_alpaca(self) -> bool:
        return bool(C.ALPACA_KEY and C.ALPACA_SECRET)

    @property
    def simulated(self) -> bool:
        return self.cfg.get("broker") == "sim" or not self.has_alpaca

    async def _make_accounts(self):
        for a in list(self.accounts.values()):
            try:
                await a.stop()
            except Exception:
                pass
        if self.simulated:
            # version hebergee sans cles Alpaca : courtier simule, meme interface
            main = SimAccount(self)
        else:
            main = AlpacaAccount("principal", C.ALPACA_KEY, C.ALPACA_SECRET, C.ALPACA_ENDPOINT)
        self.accounts = {"principal": main}
        self.funds["jev"].account = main
        if not self.simulated and C.ALPACA_OPUS_KEY and C.ALPACA_OPUS_SECRET:
            second = AlpacaAccount("opus", C.ALPACA_OPUS_KEY, C.ALPACA_OPUS_SECRET, C.ALPACA_ENDPOINT)
            self.accounts["opus"] = second
            self.funds["opus"].account = second
            self.shared = False
        else:
            self.funds["opus"].account = main
            self.shared = True
        for f in self.funds.values():
            f.account.register(f.model, f.on_update)
        for a in self.accounts.values():
            a.start()

    async def _restart_feed(self):
        if not self.has_alpaca and self.cfg["source"] != "coinbase":
            self.cfg["source"], self.cfg["symbol"] = "coinbase", "BTC/USD"
            self.cfg["position_usd"] = min(float(self.cfg.get("position_usd") or 10_000), 25_000)
        if self.feed:
            await self.feed.stop()
        self.feed = Feed(self.cfg["source"], self.symbol, self.bar_sec, self.on_bar, self.status)
        await self.feed.start()
        await self.emit({"type": "history", "bars": self.feed.builder.history[-900:], "bar_sec": self.bar_sec,
                         "symbol": self.symbol, "source": self.cfg["source"], "ind": self.indicator_series()},
                        record=False)
        if not self.crypto:
            try:
                clock = await self.accounts["principal"].clock()
                if not clock.get("is_open"):
                    nxt = str(clock.get("next_open", ""))[:16].replace("T", " ")
                    await self.status(f"marché actions fermé · prochaine ouverture {nxt} (heure de New York)")
            except Exception:
                pass

    async def status(self, msg: str):
        self.messages.append({"t": time.time(), "msg": msg})
        self.messages = self.messages[-30:]
        await self.emit({"type": "status", "msg": msg})

    # ---------- chaque bougie ----------
    def market_view(self) -> MarketView:
        b = self.feed.builder
        return MarketView(symbol=self.symbol, market=self.market, bar_sec=self.bar_sec, bars=b.history[-300:],
                          price=b.last_price, bid=b.bid, ask=b.ask, fee_bps=self.fee_bps,
                          allow_short=self.allow_short)

    def exit_rules(self) -> dict:
        s = self.strategy
        if s is None:
            return {}
        if hasattr(s, "exits"):
            try:
                return s.exits(self.market_view())
            except Exception:
                pass
        r = s.risk
        return {"max_hold_s": getattr(r, "max_hold_s", None), "take_profit_bps": r.take_profit_bps,
                "stop_loss_bps": r.stop_loss_bps}

    def indicators_at(self, bars: list) -> dict | None:
        s = self.strategy
        if s is None or not hasattr(s, "indicators") or len(bars) < 5:
            return None
        b = self.feed.builder
        mv = MarketView(symbol=self.symbol, market=self.market, bar_sec=self.bar_sec, bars=bars, price=bars[-1]["c"],
                        bid=b.bid, ask=b.ask, fee_bps=self.fee_bps, allow_short=self.allow_short)
        try:
            return s.indicators(mv)
        except Exception:
            return None

    def indicator_series(self, n: int = 900) -> list:
        hist = self.feed.builder.history if self.feed else []
        start = max(0, len(hist) - n)
        out = []
        for i in range(start, len(hist)):
            ind = self.indicators_at(hist[max(0, i - 299):i + 1])
            if ind:
                out.append({"t": hist[i]["t"], **ind})
        return out

    async def on_bar(self, bar: dict):
        price = bar["c"]
        pos = {m: f.side for m, f in self.funds.items()}
        ind = self.indicators_at(self.feed.builder.history[-300:]) if self.feed else None
        await self.emit({"type": "bar", "bar": bar, "pos": pos, "ind": ind}, record=self.running)
        if not self.running:
            return
        if self.stop_at and time.time() >= self.stop_at:
            asyncio.create_task(self.stop(reason=f"durée de {self.cfg['duration_min']} min atteinte"))
            return
        mv = self.market_view()
        for f in self.funds.values():
            await f.on_bar(bar, mv)
        await self.emit({"type": "stats", "funds": {m: f.stats(price) for m, f in self.funds.items()},
                         "elapsed": time.time() - self.started_at,
                         "ledger": LEDGER_OBJ.summary().get("trading")}, record=True)

    # ---------- comptes ----------
    def split_for_account(self, fund: Fund, leg: float) -> list[float]:
        """Sur un compte partage, un ordre ne doit pas faire passer la position NETTE du compte
        de longue a courte (ou l'inverse) : Alpaca le refuse. On le coupe en deux."""
        if not self.shared:
            return [leg]
        net = sum(f.qty for f in self.funds.values() if f.account is fund.account)
        after = net + leg
        if _sign(net) != 0 and _sign(after) != 0 and _sign(net) != _sign(after):
            return [-net, after]
        return [leg]

    async def refresh_accounts(self):
        info = {}
        for name, a in self.accounts.items():
            try:
                acc = await a.account()
                info[name] = {"equity": float(acc.get("equity") or 0), "cash": float(acc.get("cash") or 0),
                              "status": acc.get("status"), "updates": a.connected,
                              "orders_last_min": len([t for t in a.req_times if t > time.time() - 60])}
            except Exception as e:
                info[name] = {"error": str(e)[:160]}
        self.account_info = info
        return info

    async def flatten(self):
        """Annule les ordres et ferme la position du symbole sur chaque compte utilise."""
        out = []
        for a in self.accounts.values():
            try:
                await a.cancel_all()
                await a.close_position(self.symbol)
                out.append(f"{a.name} : à plat")
            except Exception as e:
                out.append(f"{a.name} : {e}")
        return out

    # ---------- commandes ----------
    async def start(self, cfg: dict | None = None):
        if self.running:
            return {"type": "status", "msg": "déjà lancé"}
        if cfg:
            await self.configure(cfg)
        if self.strategy is None:
            return {"type": "error", "error": "aucune stratégie chargée"}
        acc = self.accounts["principal"]
        if not self.crypto:
            clock = await acc.clock()
            if not clock.get("is_open"):
                return {"type": "error", "error": f"marché actions fermé (ouverture : {clock.get('next_open')})"}
        # vente a decouvert : impossible en crypto sur Alpaca ; en actions si le titre est empruntable
        self.allow_short = False
        if not self.crypto or self.simulated:
            try:
                a = await acc.asset(self.symbol)
                self.allow_short = bool(a.get("shortable") and a.get("easy_to_borrow"))
            except Exception:
                self.allow_short = False
        self.fee_bps = float(self.cfg["fee_bps"]) if self.cfg.get("fee_bps") not in (None, "") else (
            0.0 if self.simulated else CRYPTO_FEE_BPS if self.crypto else 0.0)
        msgs = await self.flatten()
        for f in self.funds.values():
            f.reset()
        await asyncio.sleep(1.0)
        for a in self.accounts.values():
            a.set_position(self.symbol, await a.position_qty(self.symbol))
        await self.refresh_accounts()
        self.running = True
        self.started_at = time.time()
        d = float(self.cfg.get("duration_min") or 0)
        self.stop_at = self.started_at + d * 60 if d > 0 else None
        path = HUB.recorder(self.name).start({"cfg": self.cfg, "strategy": self.strategy.id,
                                              "shared_account": self.shared, "allow_short": self.allow_short,
                                              "fee_bps": self.fee_bps})
        await self.emit({"type": "started", "cfg": self.cfg, "allow_short": self.allow_short, "fee_bps": self.fee_bps,
                         "shared": self.shared, "started_at": self.started_at, "stop_at": self.stop_at,
                         "strategy": {"id": self.strategy.id, "label": self.strategy.label},
                         "accounts": self.account_info, "flatten": msgs, "session": str(path)})
        return None

    async def stop(self, reason: str = "arrêt demandé", flatten: bool = True):
        if not self.running:
            return None
        self.running = False
        # attendre la fin des ordres en vol, puis tout remettre a plat
        pend = [f.pending for f in self.funds.values() if f.pending]
        if pend:
            await asyncio.wait(pend, timeout=15)
        msgs = await self.flatten() if flatten else []
        await asyncio.sleep(1.5)   # laisser arriver les executions de fermeture
        price = self.feed.builder.last_price if self.feed else None
        summary = {m: f.stats(price) for m, f in self.funds.items()}
        await self.emit({"type": "stopped", "reason": reason, "flatten": msgs, "summary": summary,
                         "elapsed": time.time() - (self.started_at or time.time())})
        HUB.recorder(self.name).stop()
        return None

    async def configure(self, cfg: dict):
        restart_feed = False
        broker_before = self.cfg.get("broker")
        for k in ("source", "symbol", "bar_sec", "strategy", "position_usd", "duration_min", "fee_bps", "thr", "broker"):
            if k in cfg and cfg[k] is not None and cfg[k] != "" or (k == "fee_bps" and cfg.get(k) == 0):
                if k in ("source", "symbol", "bar_sec") and str(cfg[k]) != str(self.cfg.get(k)):
                    restart_feed = True
                self.cfg[k] = cfg[k]
        if self.cfg["source"] not in SOURCES:
            self.cfg["source"] = "coinbase"
        if self.cfg.get("broker") not in ("sim", "alpaca"):
            self.cfg["broker"] = "sim"
        if self.cfg["broker"] != broker_before and not self.running:
            await self._make_accounts()
        self.cfg["bar_sec"] = int(self.cfg["bar_sec"])
        self.strategy = self.strategies.get(self.cfg["strategy"]) or self.strategy
        for s in self.strategies.values():
            if hasattr(s, "thr_override"):
                s.thr_override = float(self.cfg["thr"]) if self.cfg.get("thr") else None
        if restart_feed and not self.running:
            await self._restart_feed()
        await self.emit({"type": "config", "cfg": self.cfg, "simulated": self.simulated}, record=False)

    async def handle(self, cmd: dict):
        c = cmd.get("cmd")
        if c == "start":
            return await self.start(cmd.get("cfg"))
        if c == "stop":
            return await self.stop()
        if c == "config":
            if self.running:
                return {"type": "error", "error": "arrête la session avant de changer les réglages"}
            await self.configure(cmd.get("cfg") or {})
            return None
        if c == "flatten":
            return {"type": "status", "msg": " · ".join(await self.flatten())}
        if c == "reload_strategies":
            self.strategies = strategies_pkg.load_all()
            return {"type": "strategies", "items": self.strategy_list(), "errors": strategies_pkg.ERRORS}
        if c == "accounts":
            return {"type": "accounts", "accounts": await self.refresh_accounts()}
        return None

    def strategy_list(self):
        return [{"id": s.id, "label": s.label, "short": getattr(s, "short", "") or s.label,
                 "description": s.description} for s in self.strategies.values()]

    def snapshot(self) -> dict:
        price = self.feed.builder.last_price if self.feed else None
        return {
            "cfg": self.cfg, "running": self.running, "started_at": self.started_at, "stop_at": self.stop_at,
            "sources": SOURCES, "strategies": self.strategy_list(), "strategy_errors": strategies_pkg.ERRORS,
            "bars": self.feed.builder.history[-900:] if self.feed else [], "bar_sec": self.bar_sec,
            "ind": self.indicator_series(), "exit_rules": self.exit_rules(),
            # hors session, une page qui s'ouvre est VIERGE : aucun resultat d'une session precedente
            "funds": ({m: {**f.stats(price), "markers": f.markers[-200:], "tape": f.tape[-120:],
                           "trade_list": f.trades[-20:]} for m, f in self.funds.items()} if self.running
                      else {m: {**Fund(self, m).stats(price), "markers": [], "tape": [], "trade_list": []}
                            for m in MODELS}),
            "shared": self.shared, "allow_short": self.allow_short, "fee_bps": self.fee_bps,
            "simulated": self.simulated, "has_alpaca": self.has_alpaca, "openrouter_key": bool(C.OPENROUTER_KEY),
            "accounts": self.account_info, "messages": self.messages[-8:],
            "ledger": LEDGER_OBJ.summary().get("trading"),
        }


scene = TradingScene()
router = APIRouter()


@router.get("/state")
async def state():
    return scene.snapshot()


@router.get("/accounts")
async def accounts():
    return await scene.refresh_accounts()


@router.post("/flatten")
async def flatten():
    return {"result": await scene.flatten()}
