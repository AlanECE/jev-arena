// Scene trading a trois : trois graphiques a la seconde (Jev a gauche, Laya au milieu, Opus a droite), alimentes en direct.
// Page VIERGE a l'ouverture : rien ne tourne tant qu'Alan n'a pas appuye sur « Lancer le test ».
(function () {
  const MODELS = ["jev", "laya", "opus"];
  const TZ = -new Date().getTimezoneOffset() * 60;       // les graphiques affichent l'heure locale
  const TAPE_N = 46;   // trois colonnes : la bande tient en 46 bougies
  const COLORS = { long: "#12a150", short: "#e5484d", longA: "rgba(18,161,80,.55)", shortA: "rgba(229,72,77,.55)",
    ema: "#7b3ff2", vwap: "#e8a317" };
  const S = {
    running: false, startedAt: null, stopAt: null, cfg: {}, bars: [], lastT: null, ind: new Map(), thr: 2,
    f: {}, sources: {}, strategies: [], crypto: false, shared: true, feeBps: 0, ws: null, exitRules: {},
  };
  const fr = (x, d) => Number(x).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }).replace(/ /g, " ");

  // ---------- construction des deux panneaux ----------
  function buildFund(model) {
    const el = document.getElementById("fund-" + model);
    el.appendChild(document.getElementById("fundTpl").content.cloneNode(true));
    el.querySelector(".model-head img").src = Arena.LOGOS[model];
    el.querySelector(".name").textContent = Arena.NAMES[model];
    el.querySelector(".maker").textContent = Arena.MAKERS[model];
    if (model === "laya") el.querySelector(".maker").setAttribute("data-laya-maker", "");
    const chart = LightweightCharts.createChart(el.querySelector(".chart"), {
      autoSize: true,
      layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#5b5368", fontFamily: "SF Pro Display", fontSize: 13 },
      grid: { vertLines: { color: "#f2eef8" }, horzLines: { color: "#f2eef8" } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.06, bottom: 0.28 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: true, rightOffset: 4, barSpacing: 7 },
      crosshair: { mode: 0 }, handleScroll: false, handleScale: false,
      localization: { locale: "fr-FR", priceFormatter: (p) => fr(p, 2) },
    });
    const candles = chart.addCandlestickSeries({
      upColor: COLORS.long, downColor: COLORS.short, borderVisible: false,
      wickUpColor: COLORS.long, wickDownColor: COLORS.short, priceLineColor: "#9a92a8", priceLineStyle: 2,
    });
    const ema = chart.addLineSeries({ color: COLORS.ema, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const vwap = chart.addLineSeries({ color: COLORS.vwap, lineWidth: 2, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const xline = chart.addLineSeries({ color: "rgba(123,63,242,.8)", lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: "Alpaca", crosshairMarkerVisible: false });
    // bandes d'entree : le seuil traduit en prix (au-dessus de la rouge = short, en dessous de la verte = long)
    const bandUp = chart.addLineSeries({ color: "rgba(229,72,77,.55)", lineWidth: 1, lineStyle: 3, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const bandDn = chart.addLineSeries({ color: "rgba(18,161,80,.55)", lineWidth: 1, lineStyle: 3, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    // volume echange par seconde
    const volume = chart.addHistogramSeries({ priceScaleId: "vol", priceLineVisible: false, lastValueVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.62, bottom: 0.26 }, visible: false });
    // jauge d'etirement (en ecarts-types) en bas du graphique, sur sa propre echelle
    const stretch = chart.addHistogramSeries({ priceScaleId: "sig", priceLineVisible: false, lastValueVisible: false, base: 0 });
    chart.priceScale("sig").applyOptions({ scaleMargins: { top: 0.76, bottom: 0.07 }, visible: false });
    const strip = chart.addHistogramSeries({ priceScaleId: "pos", lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("pos").applyOptions({ scaleMargins: { top: 0.95, bottom: 0 }, visible: false });
    const tape = el.querySelector(".tape");
    const cells = [];
    for (let i = 0; i < TAPE_N; i++) { const c = document.createElement("i"); tape.appendChild(c); cells.push(c); }
    S.f[model] = { el, chart, candles, ema, vwap, xline, bandUp, bandDn, volume, stretch, strip, cells, markers: [], tapeMap: new Map(), tapeOrder: [],
      lines: {}, side: "flat", thinkingSince: null, plan: null, posByT: new Map() };
  }

  // ---------- donnees du graphique ----------
  const toCandle = (b) => ({ time: b.t + TZ, open: b.o, high: b.h, low: b.l, close: b.c });
  const volPoint = (b) => ({ time: b.t + TZ, value: b.v || 0, color: b.c >= b.o ? "rgba(18,161,80,.18)" : "rgba(229,72,77,.18)" });
  const stripPoint = (t, side) => ({ time: t + TZ, value: 1, color: side === "long" ? COLORS.longA : side === "short" ? COLORS.shortA : "rgba(0,0,0,0)" });
  function stretchPoint(t, z) {
    const a = Math.abs(z), beyond = a >= S.thr;
    const col = z > 0 ? (beyond ? "rgba(229,72,77,.85)" : "rgba(229,72,77,.3)") : (beyond ? "rgba(18,161,80,.85)" : "rgba(18,161,80,.3)");
    return { time: t + TZ, value: z, color: col };
  }
  function setHistory(bars, ind) {
    S.bars = bars.slice(-900);
    S.lastT = S.bars.length ? S.bars[S.bars.length - 1].t : null;
    S.ind = new Map();
    (ind || []).forEach((x) => { S.ind.set(x.t, x); if (x.thr) S.thr = x.thr; });
    for (const m of MODELS) {
      const F = S.f[m];
      F.candles.setData(S.bars.map(toCandle));
      F.strip.setData(S.bars.map((b) => stripPoint(b.t, F.posByT.get(b.t) || "flat")));
      F.xline.setData(S.simulated ? [] : S.bars.filter((b) => b.xbid && b.xask).map((b) => ({ time: b.t + TZ, value: (b.xbid + b.xask) / 2 })));
      const I = S.bars.map((b) => S.ind.get(b.t)).filter(Boolean);
      F.ema.setData(I.filter((x) => x.ema20).map((x) => ({ time: x.t + TZ, value: x.ema20 })));
      F.vwap.setData(I.filter((x) => x.vwap).map((x) => ({ time: x.t + TZ, value: x.vwap })));
      F.stretch.setData(I.filter((x) => x.stretch != null).map((x) => stretchPoint(x.t, x.stretch)));
      F.bandUp.setData(I.filter((x) => x.band_up).map((x) => ({ time: x.t + TZ, value: x.band_up })));
      F.bandDn.setData(I.filter((x) => x.band_dn).map((x) => ({ time: x.t + TZ, value: x.band_dn })));
      F.volume.setData(S.bars.map(volPoint));
      F.chart.timeScale().scrollToRealTime();
    }
    if (S.bars.length) setPrice(S.bars[S.bars.length - 1].c);
  }
  function onBar(bar, pos, ind) {
    if (S.lastT != null && bar.t <= S.lastT) return;
    S.lastT = bar.t;
    S.bars.push(bar);
    if (S.bars.length > 1200) S.bars = S.bars.slice(-900);
    if (ind) { S.ind.set(bar.t, { t: bar.t, ...ind }); if (ind.thr) S.thr = ind.thr; }
    for (const m of MODELS) {
      const F = S.f[m];
      F.candles.update(toCandle(bar));
      const side = (pos && pos[m]) || "flat";
      F.posByT.set(bar.t, side);
      F.strip.update(stripPoint(bar.t, side));
      if (!S.simulated && bar.xbid && bar.xask) F.xline.update({ time: bar.t + TZ, value: (bar.xbid + bar.xask) / 2 });
      if (ind) {
        if (ind.ema20) F.ema.update({ time: bar.t + TZ, value: ind.ema20 });
        if (ind.vwap) F.vwap.update({ time: bar.t + TZ, value: ind.vwap });
        if (ind.stretch != null) F.stretch.update(stretchPoint(bar.t, ind.stretch));
        if (ind.band_up) F.bandUp.update({ time: bar.t + TZ, value: ind.band_up });
        if (ind.band_dn) F.bandDn.update({ time: bar.t + TZ, value: ind.band_dn });
      }
      F.volume.update(volPoint(bar));
      F.tapeOrder.push(bar.t);
      if (F.tapeOrder.length > TAPE_N) F.tapeOrder.shift();
      renderTape(m);
    }
    setPrice(bar.c);
  }
  function setPrice(p) { Arena.set("#price", Arena.price(p)); }

  // ---------- bande des decisions ----------
  function renderTape(m) {
    const F = S.f[m];
    const start = TAPE_N - F.tapeOrder.length;
    for (let i = 0; i < TAPE_N; i++) {
      const t = F.tapeOrder[i - start];
      F.cells[i].className = t == null ? "" : F.tapeMap.get(t) || "";
    }
  }
  function tapeSet(m, t, state) {
    const F = S.f[m];
    F.tapeMap.set(t, state);
    if (F.tapeMap.size > 600) F.tapeMap.delete(F.tapeMap.keys().next().value);
    renderTape(m);
  }

  // ---------- marqueurs, lignes de niveau, plan de sortie ----------
  function addMarker(m, mk) {
    const F = S.f[m];
    const t = mk.t + TZ;
    if (mk.kind === "open") {
      F.markers.push(mk.side === "long"
        ? { time: t, position: "belowBar", color: COLORS.long, shape: "arrowUp", text: "LONG", size: 2 }
        : { time: t, position: "aboveBar", color: COLORS.short, shape: "arrowDown", text: "SHORT", size: 2 });
      setPlan(m, { side: mk.side, entry: mk.price, hold_until: mk.hold_until, tp_price: mk.tp_price, sl_price: mk.sl_price });
    } else {
      const net = mk.net || 0;
      F.markers.push({ time: t, position: mk.side === "long" ? "aboveBar" : "belowBar", color: net >= 0 ? COLORS.long : COLORS.short,
        shape: "circle", text: Arena.money(net), size: 1 });
      setPlan(m, null);
    }
    F.markers.sort((a, b) => a.time - b.time);
    if (F.markers.length > 300) F.markers = F.markers.slice(-300);
    F.candles.setMarkers(F.markers);
  }
  function setLine(m, key, price, opts) {
    const F = S.f[m];
    if (F.lines[key]) { F.candles.removePriceLine(F.lines[key]); delete F.lines[key]; }
    if (price) F.lines[key] = F.candles.createPriceLine({ price, lineWidth: 1, axisLabelVisible: true, ...opts });
  }
  function setPlan(m, plan) {
    const F = S.f[m];
    F.plan = plan;
    const el = F.el.querySelector(".plan");
    if (!plan) {
      el.classList.add("hidden"); el.className = "plan hidden";
      setLine(m, "entry"); setLine(m, "tp"); setLine(m, "sl");
      return;
    }
    el.className = "plan " + plan.side;
    el.querySelector(".plan-side").textContent = plan.side === "long" ? "LONG ▲" : "SHORT ▼";
    const lv = [];
    if (plan.tp_price) lv.push(`objectif ${Arena.price(plan.tp_price)}`);
    if (plan.sl_price) lv.push(`stop ${Arena.price(plan.sl_price)}`);
    el.querySelector(".plan-levels").textContent = lv.join(" · ");
    setLine(m, "entry", plan.entry, { color: plan.side === "long" ? COLORS.long : COLORS.short, lineWidth: 2, lineStyle: 2, title: "entrée" });
    setLine(m, "tp", plan.tp_price, { color: COLORS.long, lineStyle: 1, title: "objectif" });
    setLine(m, "sl", plan.sl_price, { color: COLORS.short, lineStyle: 1, title: "stop" });
  }

  // ---------- statistiques ----------
  function applyStats(m, s) {
    const F = S.f[m];
    const el = F.el;
    const side = s.side;
    const badge = el.querySelector(".pos-badge");
    if (F.side !== side) {
      badge.className = "badge pos-badge " + side + " pulse";
      badge.textContent = side === "long" ? "LONG ▲" : side === "short" ? "SHORT ▼" : "À PLAT";
      setTimeout(() => badge.classList.remove("pulse"), 260);
      F.side = side;
    }
    if (side !== "flat" && s.exit_plan && !F.plan) setPlan(m, { side, entry: s.entry, ...s.exit_plan });
    if (side === "flat" && F.plan) setPlan(m, null);
    if (F.plan) {
      const u = s.unrealized || 0;
      Arena.set(el.querySelector(".plan-pnl"), Arena.money(u), "plan-pnl num " + (u > 0.004 ? "pos" : u < -0.004 ? "neg" : ""));
    }
    Arena.set(el.querySelector(".pnl .value"), Arena.money(s.net), "value num " + (s.net > 0.004 ? "pos" : s.net < -0.004 ? "neg" : ""));
    Arena.set(el.querySelector(".s-dec"), Arena.num(s.decisions));
    Arena.set(el.querySelector(".s-pm"), Arena.num(s.per_min));
    Arena.set(el.querySelector(".s-missed"), Arena.num(s.missed));
    Arena.set(el.querySelector(".s-lat"), Arena.ms(s.lat_med));
    Arena.set(el.querySelector(".s-cost"), Arena.usd(s.api_cost));
    Arena.set(el.querySelector(".s-trades"), Arena.num(s.trades));
    Arena.set(el.querySelector(".s-wins"), s.trades ? `${s.wins}/${s.trades}` : "–");
    Arena.set(el.querySelector(".s-real"), Arena.money(s.realized), "value num s-real " + (s.realized > 0.004 ? "pos" : s.realized < -0.004 ? "neg" : ""));
    Arena.set(el.querySelector(".s-fees"), Arena.money(-s.fees));
    Arena.set(el.querySelector(".s-orders"), s.rejected ? `${Arena.num(s.orders)} (${s.rejected} refusés)` : Arena.num(s.orders));
  }

  const REASONS = { "durée": "durée", objectif: "objectif ✓", stop: "stop", "fin de session": "fin" };
  function addTrade(m, tr, animate = true) {
    const list = S.f[m].el.querySelector(".tlist");
    const row = document.createElement("div");
    row.className = "trow";
    if (!animate) row.style.animation = "none";
    row.innerHTML = `<span class="side ${tr.side}">${tr.side === "long" ? "LONG" : "SHORT"}</span>
      <span class="px">${fr(tr.entry_price, 2)} → ${fr(tr.exit_price, 2)}</span>
      <span class="why">${REASONS[tr.reason] || "sortie"}</span>
      <span class="net ${tr.net >= 0 ? "pos" : "neg"}">${Arena.money(tr.net)}</span>
      <span class="dur">${Math.round(tr.duration)} s</span>`;
    list.prepend(row);
    while (list.children.length > 3) list.lastChild.remove();
  }

  // ---------- decision en cours / derniere decision ----------
  function setThinking(m, since) {
    const F = S.f[m];
    F.thinkingSince = since ? since * 1000 : null;
    const st = F.el.querySelector(".dstate");
    st.classList.toggle("thinking", !!since);
    if (since) st.querySelector(".dtext").innerHTML = `réfléchit… <span class="timer">0,0 s</span>`;
  }
  function showDecision(m, d) {
    const F = S.f[m];
    const el = F.el.querySelector(".dlast");
    if (!d.ok) {
      el.innerHTML = `<b class="error">erreur</b> · ${(d.error || "").slice(0, 60)}`;
    } else {
      const word = d.choice === "long" ? "LONG" : d.choice === "short" ? "SHORT" : "À PLAT";
      const p = d.prob != null ? ` ${Arena.pct(d.prob)}${d.declared ? " (déclaré)" : ""}` : "";
      el.innerHTML = `dernière décision : <b class="${d.choice}">${word}</b>${p} · ${Arena.ms(d.latency_ms)}`;
    }
    const st = F.el.querySelector(".dstate");
    st.classList.remove("thinking");
    st.querySelector(".dtext").textContent = S.running ? "prochaine bougie…" : "en attente du départ";
  }
  function orderToast(m, text) {
    const t = S.f[m].el.querySelector(".otoast");
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove("show"), 1800);
  }
  function banner(msg) {
    const b = document.getElementById("banner");
    if (!msg) { b.classList.add("hidden"); b.textContent = ""; return; }
    b.textContent = msg;
    b.classList.remove("hidden");
  }

  // ---------- fin de session ----------
  function showFinal(ev) {
    const box = document.getElementById("final");
    const col = (m) => {
      const s = ev.summary[m] || {};
      return `<div class="col"><h3><img src="${Arena.LOGOS[m]}">${Arena.NAMES[m]}</h3>
        <div class="row"><span>Gain net</span><b class="${s.net > 0.004 ? "pos" : s.net < -0.004 ? "neg" : ""}">${Arena.money(s.net)}</b></div>
        <div class="row"><span>Décisions</span><b>${Arena.num(s.decisions)}</b></div>
        <div class="row"><span>Bougies ratées</span><b>${Arena.num(s.missed)}</b></div>
        <div class="row"><span>Latence médiane</span><b>${Arena.ms(s.lat_med)}</b></div>
        <div class="row"><span>Trades (gagnants)</span><b>${s.trades} (${s.wins})</b></div>
        <div class="row"><span>Frais estimés</span><b>${Arena.money(-s.fees)}</b></div>
        <div class="row"><span>Coût API</span><b>${Arena.usd(s.api_cost)}</b></div></div>`;
    };
    box.classList.add("three-final");
    box.innerHTML = `<h2>Fin du test · ${Arena.clock(ev.elapsed)}</h2>
      <div class="why">${ev.reason}. Positions remises à plat. Laya : ${Arena.MAKERS.laya}, gratuit.</div>
      <div class="cols">${MODELS.map(col).join("")}</div>
      <div class="close"><button class="btn ghost" id="closeFinal">Fermer</button><button class="btn violet" id="againFinal">Nouveau test</button></div>`;
    box.classList.remove("hidden");
    document.getElementById("closeFinal").onclick = () => box.classList.add("hidden");
    document.getElementById("againFinal").onclick = () => { box.classList.add("hidden"); location.reload(); };
  }

  // ---------- reglages ----------
  function fillControls(snap) {
    document.getElementById("cSource").innerHTML = Object.entries(snap.sources).map(([k, v]) => `<option value="${k}">${v.label}</option>`).join("");
    document.getElementById("cStrategy").innerHTML = snap.strategies.map((s) => `<option value="${s.id}">${s.short || s.label}</option>`).join("");
    setCfg(snap.cfg);
  }
  function setCfg(cfg) {
    S.cfg = cfg;
    document.getElementById("cSource").value = cfg.source;
    document.getElementById("cSymbol").value = cfg.symbol;
    document.getElementById("cBar").value = String(cfg.bar_sec);
    document.getElementById("cStrategy").value = cfg.strategy;
    document.getElementById("cDuration").value = String(cfg.duration_min);
    document.getElementById("cSize").value = String(cfg.position_usd);
    document.getElementById("cThr").value = String(cfg.thr || (cfg.strategy === "rupture" ? "2.0" : "2.5"));
    document.getElementById("cBroker").value = cfg.broker || "sim";
    document.getElementById("cFee").value = String(cfg.fee_bps == null ? 0 : cfg.fee_bps);
    document.getElementById("cBroker").disabled = !S.hasAlpaca;
    document.getElementById("symbol").textContent = cfg.symbol;
    document.getElementById("barChip").textContent = "bougies " + (cfg.bar_sec >= 60 ? cfg.bar_sec / 60 + " min" : cfg.bar_sec + " s");
    const srcLabel = { coinbase: "prix Coinbase", "alpaca-crypto": "prix Alpaca crypto", "alpaca-iex": "prix Alpaca IEX" }[cfg.source];
    document.getElementById("srcChip").textContent = `${srcLabel} · ${S.simulated ? "ordres simulés" : "ordres Alpaca paper"}`;
    document.querySelectorAll(".l-alpaca-wrap").forEach((x) => x.classList.toggle("hidden", cfg.source !== "coinbase" || S.simulated));
    const s = S.strategies.find((x) => x.id === cfg.strategy);
    document.getElementById("stratChip").textContent = "stratégie " + (s ? s.short || s.label : cfg.strategy);
    S.crypto = (S.sources[cfg.source] || {}).market !== "stocks";
    S.feeBps = cfg.fee_bps != null ? +cfg.fee_bps : S.simulated ? 0 : S.crypto ? 25 : 0;
    const ov = document.getElementById("ovText");
    const where = S.simulated ? "un courtier simulé (exécution au prix réel du flux, long et short)" : "le même compte Alpaca paper";
    if (s && s.description) ov.textContent = `${cfg.symbol} · ${s.description} Les trois fonds reçoivent les mêmes données et ${where} ; seule la décision d'entrée change.`;
  }
  function readCfg() {
    return {
      source: document.getElementById("cSource").value,
      symbol: document.getElementById("cSymbol").value.trim().toUpperCase(),
      bar_sec: +document.getElementById("cBar").value,
      strategy: document.getElementById("cStrategy").value,
      duration_min: +document.getElementById("cDuration").value,
      position_usd: +document.getElementById("cSize").value,
      thr: +document.getElementById("cThr").value,
      broker: document.getElementById("cBroker").value,
      fee_bps: +document.getElementById("cFee").value,
    };
  }
  function setRunning(r) {
    S.running = r;
    document.getElementById("btnStart").textContent = r ? "Arrêter" : "Lancer le test";
    document.getElementById("liveChip").classList.toggle("hidden", !r);
    for (const id of ["cSource", "cSymbol", "cBar", "cStrategy", "cDuration", "cSize", "cThr", "cBroker", "cPreset", "cFee", "cLaya"]) document.getElementById(id).disabled = r;
    document.getElementById("controls").classList.toggle("hidden", r);
    document.getElementById("infoChips").classList.toggle("hidden", !r);
    document.getElementById("overlay").classList.toggle("hidden", r);
  }
  function footer() {
    const parts = [];
    parts.push(S.simulated ? `courtier simulé : exécution au prix réel du flux${S.feeBps ? "" : ", sans frais"} · 3 fonds (Jev / Laya / Opus)`
      : S.shared ? "1 compte Alpaca paper · 3 fonds séparés (Jev / Laya / Opus)" : "comptes Alpaca paper (Jev + Laya / Opus)");
    parts.push(S.simulated ? "long, short ou à plat" : S.crypto ? "crypto : pas de vente à découvert sur Alpaca (long ou à plat)" : "actions : long, short ou à plat");
    const ex = S.exitRules || {};
    const rules = [];
    if (ex.max_hold_s) rules.push(`tenue ${ex.max_hold_s} s`);
    if (ex.take_profit_bps) rules.push(`objectif +${ex.take_profit_bps} pb`);
    if (ex.stop_loss_bps) rules.push(`stop −${ex.stop_loss_bps} pb`);
    if (rules.length) parts.push("sorties mécaniques : " + rules.join(", "));
    parts.push(`frais estimés ${Arena.num(S.feeBps, 0)} pb par ordre`);
    parts.push(`décisions réelles : Jev et Opus via OpenRouter, Laya ${Arena.LAYA && Arena.LAYA.backend === "hosted" ? "sur Hugging Face" : "sur ton GPU"}`);
    document.getElementById("footer").textContent = parts.join("   ·   ");
  }

  // ---------- evenements du serveur ----------
  function onEvent(ev) {
    switch (ev.type) {
      case "laya": Arena.setLaya(ev.laya); footer(); break;
      case "snapshot": {
        Arena.setLaya(ev.laya);
        const s = ev.data;
        S.sources = s.sources; S.strategies = s.strategies; S.shared = s.shared; S.exitRules = s.exit_rules || {};
        S.simulated = !!s.simulated; S.hasAlpaca = !!s.has_alpaca;
        if (!s.openrouter_key) banner("Aucune clé OpenRouter : ajoute OPENROUTER_API_KEY dans le fichier .env puis relance (docker compose up -d).");
        S.startedAt = s.started_at; S.stopAt = s.stop_at;
        S.simulated = !!s.simulated; S.hasAlpaca = !!s.has_alpaca;
        fillControls(s);
        if (s.running) S.feeBps = s.fee_bps;
        for (const m of MODELS) {
          const F = S.f[m], fs = s.funds[m];
          F.markers = []; F.tapeMap = new Map(); F.tapeOrder = [];
          (fs.markers || []).forEach((mk) => addMarker(m, mk));
          (fs.tape || []).forEach((e) => F.tapeMap.set(e.t, e.k));
          (fs.trade_list || []).forEach((tr) => addTrade(m, tr, false));
          applyStats(m, fs);
        }
        setHistory(s.bars, s.ind);
        for (const m of MODELS) { S.f[m].tapeOrder = S.bars.slice(-TAPE_N).map((b) => b.t); renderTape(m); }
        setRunning(s.running);
        document.getElementById("ovStatus").textContent = (s.messages || []).slice(-2).map((x) => x.msg).join(" · ") || "prêt";
        footer();
        break;
      }
      case "history":
        setHistory(ev.bars, ev.ind);
        for (const m of MODELS) { S.f[m].tapeOrder = S.bars.slice(-TAPE_N).map((b) => b.t); renderTape(m); }
        break;
      case "config": if (ev.simulated != null) S.simulated = !!ev.simulated; setCfg(ev.cfg); footer(); setHistory(S.bars, [...S.ind.values()]); break;
      case "bar": onBar(ev.bar, ev.pos, ev.ind); break;
      case "thinking": tapeSet(ev.fund, ev.t, "thinking"); setThinking(ev.fund, ev.since); break;
      case "tape": tapeSet(ev.fund, ev.t, ev.k); break;
      case "decision": tapeSet(ev.fund, ev.t, ev.ok ? ev.choice : "error"); showDecision(ev.fund, ev); break;
      case "marker": addMarker(ev.fund, ev); break;
      case "trade": addTrade(ev.fund, ev); break;
      case "exit": orderToast(ev.fund, `sortie : ${REASONS[ev.reason] || ev.reason} (${ev.bps > 0 ? "+" : ""}${fr(ev.bps, 1)} pb après ${ev.held} s)`); break;
      case "order": {
        const q = Number(ev.qty || 0);
        const what = ev.side === "buy" ? "achat" : "vente";
        if (ev.status === "submitted") orderToast(ev.fund, `ordre envoyé : ${what} ${Arena.num(q, S.crypto ? 4 : 0)}`);
        else if (ev.status === "filled") orderToast(ev.fund, `exécuté à ${Arena.price(ev.price)}`);
        else { orderToast(ev.fund, `ordre ${ev.status} : ${(ev.reason || "").slice(0, 70)}`); if (ev.status === "rejected") banner(`Alpaca a refusé un ordre (${Arena.NAMES[ev.fund]}) : ${ev.reason || ""}`); }
        break;
      }
      case "stats": for (const m of MODELS) applyStats(m, ev.funds[m]); break;
      case "started":
        S.startedAt = ev.started_at; S.stopAt = ev.stop_at; S.shared = ev.shared; S.feeBps = ev.fee_bps;
        banner(null);
        for (const m of MODELS) {
          const F = S.f[m];
          F.markers = []; F.candles.setMarkers([]); F.tapeMap = new Map(); setPlan(m, null);
          F.el.querySelector(".tlist").innerHTML = ""; F.el.querySelector(".dlast").textContent = "";
          F.posByT = new Map(); F.side = "flat";
          applyStats(m, { side: "flat", net: 0, decisions: 0, per_min: 0, missed: 0, lat_med: null, api_cost: 0, trades: 0, wins: 0, realized: 0, fees: 0, orders: 0 });
        }
        setHistory(S.bars, [...S.ind.values()]);
        setRunning(true);
        document.getElementById("final").classList.add("hidden");
        footer();
        break;
      case "stopped":
        setRunning(false);
        document.getElementById("overlay").classList.add("hidden");   // le bilan prend la place ; « Nouveau test » recharge une page vierge
        for (const m of MODELS) { setThinking(m, null); S.f[m].el.querySelector(".dtext").textContent = "test terminé"; }
        showFinal(ev);
        break;
      case "status":
        Arena.toast(ev.msg);
        document.getElementById("ovStatus").textContent = ev.msg;
        if (/plafond|crédit|credit|solde|insufficient|402/i.test(ev.msg)) banner(ev.msg);
        break;
      case "error": Arena.toast("⚠ " + ev.error); document.getElementById("ovStatus").textContent = "⚠ " + ev.error; break;
    }
  }

  // minuteur de reflexion, compte a rebours de sortie, horloge de session (60 i/s)
  function frame() {
    const now = Date.now();
    for (const m of MODELS) {
      const F = S.f[m];
      if (F.thinkingSince) {
        const tm = F.el.querySelector(".dstate .timer");
        if (tm) tm.textContent = ((now - F.thinkingSince) / 1000).toFixed(1).replace(".", ",") + " s";
      }
      if (F.plan) {
        const c = F.el.querySelector(".plan-count");
        if (F.plan.hold_until) {
          const left = Math.max(0, F.plan.hold_until - now / 1000);
          c.textContent = `sortie dans ${left.toFixed(1).replace(".", ",")} s`;
        } else c.textContent = "position tenue";
      }
    }
    if (S.running && S.startedAt) document.getElementById("clock").textContent = Arena.clock(now / 1000 - S.startedAt);
    requestAnimationFrame(frame);
  }

  function toggleRun() {
    if (S.running) S.ws.send({ cmd: "stop" });
    else S.ws.send({ cmd: "start", cfg: readCfg() });
  }

  MODELS.forEach(buildFund);
  S.ws = Arena.connect("trading", onEvent, (ok) => document.title = (ok ? "" : "⚠ ") + "Scalping en direct · Jev Arena");
  document.getElementById("btnStart").onclick = toggleRun;
  document.getElementById("btnStartBig").onclick = toggleRun;
  for (const id of ["cSource", "cSymbol", "cBar", "cStrategy", "cDuration", "cSize", "cThr", "cBroker", "cFee"]) {
    document.getElementById(id).addEventListener("change", () => S.ws.send({ cmd: "config", cfg: readCfg() }));
  }
  document.getElementById("cLaya").addEventListener("change", (e) => { Arena.switchLaya(e.target.value); e.target.blur(); });
  const PRESETS = {
    "btc-sim": { source: "coinbase", symbol: "BTC/USD", broker: "sim", position_usd: 100000, strategy: "rupture" },
    "tsla-alpaca": { source: "alpaca-iex", symbol: "TSLA", broker: "alpaca", position_usd: 100000, strategy: "ressort" },
    "btc-alpaca": { source: "coinbase", symbol: "BTC/USD", broker: "alpaca", position_usd: 10000, strategy: "rupture" },
  };
  document.getElementById("cPreset").addEventListener("change", (e) => {
    const p = PRESETS[e.target.value];
    if (!p) return;
    S.ws.send({ cmd: "config", cfg: { ...readCfg(), ...p } });
    e.target.value = "";
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    if (e.code === "Space") { e.preventDefault(); toggleRun(); }
    if (e.key === "h" || e.key === "H") document.getElementById("controls").classList.toggle("hidden");
  });
  requestAnimationFrame(frame);
})();
