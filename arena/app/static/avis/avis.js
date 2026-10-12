// Scene 04 · Le tri en direct. Tout vient du serveur (/ws/avis) : vrais avis, vraies reponses, vrais couts.
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const MODELS = ["jev", "opus"];
  const COLOR = { jev: "#d63fae", opus: "#d97757" };
  const STAR = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2.6l2.85 6.05 6.6.8-4.87 4.55 1.27 6.55L12 17.3l-5.85 3.25 1.27-6.55L2.55 9.45l6.6-.8z"/></svg>';
  const SLOT = 162;                 // hauteur d'une carte d'avis + espace
  const stage = $("#stage");

  // ------------------------------------------------------------ mise a l'echelle 1920x1080
  function fit() {
    const s = Math.min(innerWidth / 1920, innerHeight / 1080);
    stage.style.transform = `scale(${s})`;
    stage.style.left = Math.max(0, (innerWidth - 1920 * s) / 2) + "px";
    stage.style.top = Math.max(0, (innerHeight - 1080 * s) / 2) + "px";
  }
  addEventListener("resize", fit); fit();

  // ------------------------------------------------------------ formats
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const dec = (x) => (x == null ? 2 : Math.abs(x) < 0.001 ? 5 : Math.abs(x) < 0.1 ? 4 : Math.abs(x) < 10 ? 3 : 2);
  const usd = (x) => (x == null || isNaN(x) ? "–" : x === 0 ? Arena.usd(0, 2) : Arena.usd(x, dec(x)));
  const pct = (x) => (x == null || isNaN(x) ? "–" : Arena.pct(x, 0));
  const fr1 = (x) => Number(x).toFixed(1).replace(".", ",");
  const secs = (x) => {
    if (x == null || isNaN(x)) return "–";
    if (x < 60) return fr1(x) + " s";
    const m = Math.floor(x / 60), s = Math.round(x % 60);
    return `${m} min ${String(s).padStart(2, "0")} s`;
  };
  const longDur = (x) => {
    if (x == null || isNaN(x)) return "–";
    if (x < 90) return Math.round(x) + " s";
    if (x < 3600) return Math.round(x / 60) + " min";
    const h = Math.floor(x / 3600), m = Math.round((x % 3600) / 60);
    return `${h} h ${String(m).padStart(2, "0")}`;
  };
  const ratio = (r) => (r >= 10 ? Math.round(r) : r.toFixed(1).replace(".", ","));
  const dateFr = (d) => (d && d.length >= 10 ? `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}` : "");
  function starsHtml(n, color, off = "#ddd5ea") {
    let h = "";
    for (let k = 1; k <= 5; k++) h += `<span style="color:${k <= n ? color : off};display:flex">${STAR}</span>`;
    return h;
  }

  // ------------------------------------------------------------ compteurs lisses (jamais de saut brutal)
  const tweens = new Set();
  class Tw {
    constructor(el, fmt, tau = 0.18) { this.el = el; this.fmt = fmt; this.tau = tau; this.v = null; this.t = null; tweens.add(this); }
    set(t, instant) {
      this.t = t;
      if (t == null || this.v == null || instant) { this.v = t; this.render(); }
    }
    render() { const s = this.fmt(this.v); if (this.el.textContent !== s) this.el.textContent = s; }
    step(dt) {
      if (this.t == null || this.v == null || this.v === this.t) return;
      this.v += (this.t - this.v) * (1 - Math.exp(-dt / this.tau));
      if (Math.abs(this.t - this.v) <= Math.abs(this.t) * 2e-4 + 1e-7) this.v = this.t;
      this.render();
    }
  }

  // ------------------------------------------------------------ etat
  const S = {
    phase: "idle", config: {}, preset: null, presets: [], targets: [], concs: [], topics: {},
    reviews: [], fetchLog: [], fetchInfo: {}, answers: { jev: {}, opus: {} }, inflight: { jev: new Set(), opus: new Set() },
    stats: { jev: null, opus: null }, summary: null, budget: null, clientStart: null, frontier: 0, revealed: [],
    modelIds: {}, finalShown: false,
  };
  const side = {};
  const cards = [];
  let scrollPos = 0, scrollTarget = 0;

  // ------------------------------------------------------------ panneaux modeles
  function buildSide(m) {
    const root = $(`#side-${m}`);
    root.innerHTML = "";
    root.appendChild($("#tplSide").content.cloneNode(true));
    $(".logo", root).src = Arena.LOGOS[m];
    $(".name", root).textContent = Arena.NAMES[m];
    $(".name", root).classList.add("accent");
    $(".maker", root).textContent = Arena.MAKERS[m];
    const k = (key) => root.querySelector(`[data-k="${key}"]`);
    const o = {
      root, status: $(".status", root), bar: $(".progress > i", root), time: k("time"), of: k("of"), inflight: k("inflight"),
      tw: {
        done: new Tw(k("done"), (v) => (v == null ? "0" : String(Math.round(v)))),
        exact: new Tw(k("exact"), pct, 0.3), w1: new Tw(k("w1"), pct, 0.3),
        rps: new Tw(k("rps"), (v) => (v == null ? "–" : fr1(v) + " avis/s"), 0.3),
        lat: new Tw(k("lat"), (v) => Arena.ms(v), 0.3), cost: new Tw(k("cost"), usd, 0.25),
        err: new Tw(k("err"), (v) => (v == null ? "0" : String(Math.round(v)))),
        churn: new Tw(k("churn"), (v) => (v == null ? "0" : String(Math.round(v)))),
        urgent: new Tw(k("urgent"), (v) => (v == null ? "0" : String(Math.round(v)))),
      },
      errEl: k("err"), last: k("last"), cols: [], topics: {}, L: null,
    };
    const colsEl = $(".cols", root);
    for (let s = 1; s <= 5; s++) {
      const col = document.createElement("div");
      col.className = "col";
      col.innerHTML = `<div class="well"><div class="marker" style="display:none"><span></span></div></div>
        <div class="foot"><div class="s">${s}<span style="color:var(--t${s})">${STAR}</span></div><div class="c">0</div></div>`;
      colsEl.appendChild(col);
      o.cols.push({ well: $(".well", col), marker: $(".marker", col), cnt: new Tw($(".c", col), (v) => (v == null ? "0" : String(Math.round(v)))), chips: [] });
    }
    const tEl = $(".topics", root);
    for (const [key, label] of Object.entries(S.topics)) {
      const d = document.createElement("div");
      d.className = "topic";
      d.innerHTML = `<div class="row"><span>${esc(label)}</span><b>0</b></div><div class="bar"><i></i></div>`;
      tEl.appendChild(d);
      o.topics[key] = { bar: $(".bar i", d), cnt: new Tw($("b", d), (v) => (v == null ? "0" : String(Math.round(v)))) };
    }
    side[m] = o;
  }

  function chooseLayout(W, H, need) {
    for (let sz = 30; sz >= 6; sz--) {
      const g = sz >= 18 ? 4 : sz >= 10 ? 3 : 2;
      const per = Math.floor((W - 10 + g) / (sz + g));
      const rows = Math.floor((H - 10 + g) / (sz + g));
      if (per * rows >= need) return { S: sz, g, per, rows, W, H };
    }
    return { S: 6, g: 2, per: Math.floor(W / 8), rows: Math.floor(H / 8), W, H };
  }
  function chipPos(L, k) {
    const r = Math.floor(k / L.per), c = k % L.per;
    const gridW = L.per * L.S + (L.per - 1) * L.g;
    return { x: Math.round((L.W - gridW) / 2) + c * (L.S + L.g), y: L.H - 5 - (r + 1) * L.S - r * L.g };
  }
  function layoutSide(m, need) {
    const o = side[m];
    const w = o.cols[0].well;
    o.L = chooseLayout(w.clientWidth, w.clientHeight, need);
    for (const col of o.cols) col.chips.forEach((ch, k) => placeChip(o.L, ch, k));
    placeMarkers(m);
  }
  function placeChip(L, ch, k) {
    const p = chipPos(L, k);
    ch.style.left = p.x + "px"; ch.style.top = p.y + "px";
    ch.style.width = ch.style.height = L.S + "px";
    ch.style.fontSize = L.S >= 15 ? Math.round(L.S * 0.56) + "px" : "0px";
    ch.style.borderRadius = Math.max(3, Math.round(L.S * 0.24)) + "px";
  }
  function placeMarkers(m) {
    const o = side[m], L = o.L, by = (S.fetchInfo && S.fetchInfo.by_star) || null;
    o.cols.forEach((col, idx) => {
      const tc = by ? by[String(idx + 1)] || 0 : 0;
      if (!by || !L) { col.marker.style.display = "none"; return; }
      const rows = Math.ceil(tc / L.per);
      const y = L.H - 5 - rows * (L.S + L.g) + L.g - 3;
      col.marker.style.display = "";
      col.marker.style.top = Math.max(2, y) + "px";
      col.marker.querySelector("span").textContent = `réel ${tc}`;
    });
  }
  function addChip(m, a, animate) {
    const o = side[m];
    if (!o.L) layoutSide(m, Math.max(8, Math.ceil(S.reviews.length * 0.45)));
    const col = o.cols[a.pred - 1];
    const ch = document.createElement("div");
    ch.className = "tok " + a.verdict;
    ch.textContent = a.truth;
    const k = col.chips.length;
    col.chips.push(ch);
    if (col.chips.length > o.L.per * o.L.rows) {
      const most = Math.max(...o.cols.map((c) => c.chips.length));
      layoutSide(m, Math.ceil(most * 1.35));
    }
    placeChip(o.L, ch, k);
    col.well.appendChild(ch);
    if (animate) {
      const y = parseFloat(ch.style.top) + o.L.S + 24;
      ch.animate([{ transform: `translateY(${-y}px)`, opacity: 0 }, { transform: "translateY(0)", opacity: 1 }],
        { duration: 620, easing: "cubic-bezier(.2,.85,.25,1)" });
    }
  }

  function renderStats(m, instant) {
    const o = side[m], st = S.stats[m], n = S.reviews.length;
    if (!o) return;
    o.of.textContent = `/ ${n || S.config.target || 0}`;
    if (!st) {
      for (const t of Object.values(o.tw)) t.set(null, true);
      o.tw.done.set(0, true); o.tw.err.set(0, true); o.tw.churn.set(0, true); o.tw.urgent.set(0, true); o.tw.cost.set(0, true);
      o.bar.style.width = "0%";
      o.inflight.textContent = "4 décisions par avis";
      o.last.textContent = "";
      o.cols.forEach((c) => c.cnt.set(0, true));
      for (const t of Object.values(o.topics)) { t.cnt.set(0, true); t.bar.style.width = "0%"; }
      o.errEl.classList.remove("has");
      return;
    }
    o.tw.done.set(st.done, instant);
    o.tw.exact.set(st.exact_pct, instant);
    o.tw.w1.set(st.within1_pct, instant);
    o.tw.rps.set(st.rps, instant);
    o.tw.lat.set(st.lat_med, instant);
    o.tw.cost.set(st.cost, instant);
    o.tw.err.set(st.err, instant);
    o.tw.churn.set(st.churn, instant);
    o.tw.urgent.set(st.urgent, instant);
    o.errEl.classList.toggle("has", st.err > 0);
    o.bar.style.width = n ? (100 * st.done / n).toFixed(2) + "%" : "0%";
    const dec4 = `${Arena.num(st.ok * 4)} décisions`;
    o.inflight.textContent = st.finished ? `terminé · ${dec4}` : `${S.inflight[m].size} en cours · ${dec4}`;
    o.last.textContent = st.lat_last != null ? `dern. ${Arena.ms(st.lat_last)}` : "";
    o.cols.forEach((c, idx) => c.cnt.set(st.pred[String(idx + 1)] || 0, instant));
    for (const [key, t] of Object.entries(o.topics)) {
      const v = (st.topics && st.topics[key]) || 0;
      t.cnt.set(v, instant);
      t.bar.style.width = st.ok ? (100 * v / st.ok).toFixed(1) + "%" : "0%";
    }
    renderStatus(m);
  }
  function renderStatus(m) {
    const o = side[m], st = S.stats[m];
    if (!o) return;
    let txt = "en attente", cls = "status";
    if (S.phase === "fetch") txt = "attend les avis";
    else if (S.phase === "ready") txt = "prêt";
    if (!st && S.phase === "race") { txt = "lit les avis"; cls += " run"; }
    if (st && (S.phase === "race" || S.phase === "done" || S.phase === "error")) {
      if (st.finished) { txt = `terminé en ${secs(st.elapsed)}`; cls += " fin"; }
      else if (S.phase === "race") { txt = "lit les avis"; cls += " run"; }
      else txt = "arrêté";
    }
    o.status.className = cls;
    o.status.textContent = txt;
    if (S.modelIds[m]) $(".maker", o.root).textContent = `${m === "jev" ? "TypeSafe AI" : "Anthropic"} · ${S.modelIds[m]} · via OpenRouter`;
  }

  // ------------------------------------------------------------ colonne centrale
  const track = $("#track"), intro = $("#intro"), flog = $("#fetchlog"), stream = $("#stream");

  // glyphe « Play » (triangle quadricolore) pour la source Google Play, dessine en SVG : aucun fichier a charger
  const PLAY = (sz) => `<svg viewBox="0 0 24 24" width="${sz}" height="${sz}" style="flex:none"><path fill="#00c3ff" d="M3.6 2.3 13.9 12 3.6 21.7c-.4-.2-.6-.7-.6-1.2V3.5c0-.5.2-1 .6-1.2z"/><path fill="#00e676" d="M3.6 2.3c.4-.3 1-.3 1.5 0L17.3 9l-3.4 3z"/><path fill="#ff3d47" d="M3.6 21.7 13.9 12l3.4 3L5.1 21.7c-.5.3-1.1.3-1.5 0z"/><path fill="#ffd400" d="M17.3 9l3.4 1.9c1 .6 1 1.6 0 2.2L17.3 15 13.9 12z"/></svg>`;
  const SRC_NAME = { trustpilot: "Trustpilot FR", appstore: "App Store FR", googleplay: "Google Play FR" };
  function srcLine(p) {
    if (!p) return "";
    if (p.source === "trustpilot")
      return `<img src="/static/common/logos/lightpanda.png" alt=""><span>Trustpilot FR · <b>${esc(p.label)}</b> · lu en direct par Lightpanda</span>`;
    if (p.source === "googleplay")
      return `<span style="padding-left:6px;display:flex;align-items:center">${PLAY(22)}</span><span>Google Play FR · <b>${esc(p.label)}</b> · lu en direct</span>`;
    return `<span style="padding-left:6px">App Store FR · <b>${esc(p.label)}</b> · flux RSS public d'Apple</span>`;
  }
  function renderConfig() {
    const p = S.preset, c = S.config;
    $("#src").innerHTML = srcLine(p);
    const sp = $("#selPreset");
    if (!sp.options.length) {
      const groups = { trustpilot: "Trustpilot FR · Lightpanda", appstore: "App Store FR · RSS Apple", googleplay: "Google Play FR · lu en direct" };
      for (const [src, lab] of Object.entries(groups)) {
        const g = document.createElement("optgroup"); g.label = lab;
        for (const x of S.presets.filter((q) => q.source === src)) {
          const op = document.createElement("option"); op.value = x.id; op.textContent = x.label; g.appendChild(op);
        }
        sp.appendChild(g);
      }
      for (const t of S.targets) { const op = document.createElement("option"); op.value = t; op.textContent = `${t} avis`; $("#selTarget").appendChild(op); }
      for (const t of S.concs) { const op = document.createElement("option"); op.value = t; op.textContent = `${t} en parallèle`; $("#selConc").appendChild(op); }
    }
    sp.value = c.preset; $("#selTarget").value = c.target; $("#selConc").value = c.conc;
    renderIntro();
  }
  function renderIntro() {
    const p = S.preset, c = S.config;
    if (!p) return;
    const per = Math.ceil(c.target / 5);
    const step1 = p.source === "trustpilot"
      ? `<b>Lightpanda</b> ouvre en direct les pages Trustpilot de <b>${esc(p.label)}</b> et récupère <b>${c.target} vrais avis</b> (${per} par note).`
      : p.source === "googleplay"
      ? `Le Play Store est interrogé en direct : les derniers avis Android de <b>${esc(p.label)}</b>, filtrés par note, soit <b>${c.target} vrais avis</b> (${per} par note).`
      : `Le flux public de l'App Store donne les 500 derniers avis de l'appli <b>${esc(p.label)}</b> ; on en garde <b>${c.target}</b>, le plus équilibré possible par note.`;
    intro.innerHTML = `<div class="cta">
        <button class="btn violet" data-act="start">Lancer la course <kbd>Espace</kbd></button>
        <button class="btn ghost" data-act="load">Charger les avis d'abord <kbd>C</kbd></button></div><ol>
      <li>${step1}</li>
      <li><b style="color:var(--jev)">Jev</b> et <b style="color:var(--opus)">Claude Opus 5.5</b> lisent les mêmes avis, dans le même ordre, ${c.conc} à la fois chacun. Un appel par avis.</li>
      <li>Chacun devine la note, le sujet, le risque de départ et l'urgence. <b>La vraie note du client tranche.</b></li></ol>
      <div class="keys">Raccourcis : Espace lancer · C charger · R nouvelle course · B bilan · H masquer les réglages</div>`;
    $("#cTitle").textContent = "Les avis";
    $("#cSub").innerHTML = p.source === "trustpilot" ? `Source : fr.trustpilot.com/review/${esc(p.domain)}`
      : p.source === "googleplay" ? `Source : play.google.com/store/apps/details?id=${esc(p.package)}`
      : `Source : App Store FR, appli n° ${p.app_id}`;
  }
  function renderCenterHead() {
    const fi = S.fetchInfo || {}, p = S.preset || {};
    if (!S.reviews.length) { $("#dist").innerHTML = ""; return; }
    const tool = (fi.tools || []).map((t) => ({ lightpanda: "Lightpanda", crawl4ai: "Crawl4AI", rss: "le flux RSS d'Apple", googleplay: "le Play Store" }[t] || t)).join(" + ");
    const unit = p.source === "googleplay" ? "lots lus" : "pages lues";
    $("#cTitle").innerHTML = `${S.reviews.length} vrais avis <span style="color:var(--ink-3);font-weight:500;font-size:18px">${esc(p.label || "")}${p.source ? ` · ${SRC_NAME[p.source] || ""}` : ""}</span>`;
    $("#cSub").innerHTML = `${fi.pages_ok}/${fi.pages} ${unit} en ${secs((fi.ms || 0) / 1000)} par ${esc(tool)} · vraie note cachée jusqu'aux deux réponses`
      + (S.phase === "ready" ? ` · <b style="color:var(--violet)">Espace : lancer la course</b>` : "");
    let h = "";
    for (let s = 1; s <= 5; s++) h += `<div><b>${(fi.by_star || {})[String(s)] || 0}</b>${s} ★</div>`;
    $("#dist").innerHTML = h;
  }
  const fetchRows = {};
  function fetchRowHtml(ev, pending) {
    const box = (inner) => `<div style="width:30px;height:30px;border-radius:8px;background:var(--paper-2);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:var(--ink-2)">${inner}</div>`;
    const logo = ev.tool === "lightpanda" ? `<img src="/static/common/logos/lightpanda.png" alt="">`
      : ev.tool === "googleplay" ? box(PLAY(20)) : box("RSS");
    const toolName = { lightpanda: "Lightpanda", crawl4ai: "Crawl4AI", rss: "RSS Apple", googleplay: "Google Play" }[ev.tool] || ev.tool;
    const n = pending ? "" : ev.kind === "cookie" ? (ev.ok ? "ok" : "échec") : `${ev.n} avis`;
    return `${logo}<div class="m"><b>${esc(ev.label)}</b>${ev.note ? ` <span style="color:var(--near);font-size:13px">${esc(ev.note)}</span>` : ""}
      <div>${esc(toolName)} · ${esc((ev.url || "").replace("https://", ""))}</div></div>
      <div class="n">${n}</div><div class="ms">${pending ? "" : Arena.ms(ev.ms)}</div>`;
  }
  function showFetchPlan(plan) {
    intro.style.display = "none";
    flog.style.display = "";
    flog.innerHTML = "";
    for (const k in fetchRows) delete fetchRows[k];
    for (const p of plan || []) {
      const d = document.createElement("div");
      d.className = "frow card pending";
      d.innerHTML = fetchRowHtml(p, true);
      flog.appendChild(d);
      fetchRows[p.label] = d;
    }
  }
  function addFetchRow(ev) {
    intro.style.display = "none";
    flog.style.display = "";
    let d = fetchRows[ev.label];
    if (!d) {
      d = document.createElement("div");
      if (ev.kind === "cookie") flog.insertBefore(d, flog.firstChild); else flog.appendChild(d);
      fetchRows[ev.label] = d;
    }
    d.className = "frow card landed" + (ev.ok ? "" : " bad");
    d.innerHTML = fetchRowHtml(ev, false);
    while (flog.children.length > 12) flog.removeChild(flog.lastChild);
  }

  function pillHtml(m, i) {
    const a = S.answers[m][i];
    const logo = `<img src="${Arena.LOGOS[m]}" alt="">`;
    if (a) {
      if (!a.ok) return { cls: "fail", html: `${logo}<span class="tp">erreur · ${esc((a.error || "").slice(0, 40))}</span>` };
      const topic = S.topics[a.topic] || a.topic || "";
      return { cls: "ans", html: `${logo}<span class="st">${starsHtml(a.pred, COLOR[m])}</span><span class="tp">${esc(topic)}</span>` };
    }
    if (S.inflight[m].has(i)) return { cls: "read", html: `${logo}<span class="tp">lit l'avis<span class="dots"></span></span>` };
    return { cls: "wait", html: `${logo}<span class="tp">en attente</span>` };
  }
  function updatePill(m, i) {
    const c = cards[i];
    if (!c) return;
    const { cls, html } = pillHtml(m, i);
    const el = c.pills[m];
    const rev = S.revealed[i] && S.answers[m][i] && S.answers[m][i].ok ? " " + S.answers[m][i].verdict : "";
    el.className = `pill ${m}p ${cls}${rev}`;
    if (el._h !== html) { el.innerHTML = html; el._h = html; }
  }
  function buildCards() {
    track.innerHTML = "";
    cards.length = 0;
    S.revealed = new Array(S.reviews.length).fill(false);
    S.frontier = 0;
    for (const r of S.reviews) {
      const el = document.createElement("div");
      el.className = "rcard card" + (r.title ? "" : " notitle");      // Google Play : pas de titre, le texte prend la place
      el.style.top = r.i * SLOT + "px";
      el.innerHTML = `<div class="top"><span class="idx">#${r.i + 1}</span><span>${esc(r.author)} · ${dateFr(r.date)}${r.version ? ` · v${esc(r.version)}` : ""}</span></div>
        <div class="ti">${esc(r.title || "(sans titre)")}</div><div class="tx">${esc(r.text)}</div>
        <div class="truth"><div class="lab">vraie note</div><div class="flip"><div class="face front">?</div>
          <div class="face back" style="background:var(--t${r.stars})">${r.stars}${STAR}</div></div></div>
        <div class="pills"><div class="pill jevp"></div><div class="pill opusp"></div></div>`;
      track.appendChild(el);
      cards[r.i] = { el, truth: $(".truth", el), pills: { jev: $(".jevp", el), opus: $(".opusp", el) } };
      MODELS.forEach((m) => updatePill(m, r.i));
    }
    intro.style.display = "none";
    flog.style.display = "none";
    scrollPos = scrollTarget = 0;
  }
  function settle(i) {
    if (S.revealed[i]) return;
    if (!S.answers.jev[i] || !S.answers.opus[i]) return;
    S.revealed[i] = true;
    const c = cards[i];
    if (c) { c.truth.classList.add("on"); MODELS.forEach((m) => updatePill(m, i)); }
    while (S.frontier < S.reviews.length && S.revealed[S.frontier]) S.frontier++;
    updateScrollTarget();
  }
  function updateScrollTarget() {
    const n = S.reviews.length, h = stream.clientHeight;
    const maxScroll = Math.max(0, n * SLOT - h + 72);
    scrollTarget = Math.min(maxScroll, Math.max(0, (S.frontier - 1) * SLOT));
  }

  function onAnswer(a, animate) {
    S.answers[a.model][a.i] = a;
    S.inflight[a.model].delete(a.i);
    if (a.model_id) S.modelIds[a.model] = a.model_id;
    if (a.stats) S.stats[a.model] = a.stats;
    if (a.ok && a.pred) addChip(a.model, a, animate);
    updatePill(a.model, a.i);
    settle(a.i);
    if (a.stats) renderStats(a.model, !animate);
    renderCost(!animate);
    if (!a.ok && animate) Arena.toast(`${Arena.NAMES[a.model]} · avis #${a.i + 1} : ${a.error}`);
  }

  // ------------------------------------------------------------ bilan
  function renderFinal() {
    const sm = S.summary;
    if (!sm) return;
    const J = sm.models.jev, O = sm.models.opus, p = sm.preset || {};
    const row = (label, k, fmt, better, small) => {
      const a = J[k], b = O[k];
      let wj = "", wo = "";
      if (a != null && b != null && a !== b) {
        const jevBetter = better === "low" ? a < b : a > b;
        if (jevBetter) wj = " win"; else wo = " win";
      }
      return `<div class="l">${label}${small ? `<small>${small}</small>` : ""}</div><div class="v${wj}">${fmt(a)}</div><div class="v${wo}">${fmt(b)}</div>`;
    };
    const srcTxt = p.source === "trustpilot" ? "Trustpilot FR · lus en direct par Lightpanda"
      : p.source === "googleplay" ? "Google Play FR · lus en direct sur le Play Store" : "App Store FR · flux RSS public d'Apple";
    const sr = sm.speed_ratio, cr = sm.cost_ratio;
    const speedCard = sr ? (sr >= 1 ? `<b>×${ratio(sr)} plus rapide</b><span>Jev a fini ${ratio(sr)} fois plus tôt qu'Opus</span>`
      : `<b>×${ratio(1 / sr)} plus rapide</b><span>Opus a fini ${ratio(1 / sr)} fois plus tôt que Jev</span>`) : "";
    const costCard = cr ? (cr >= 1 ? `<b>×${ratio(cr)} moins cher</b><span>coût réel par avis, Jev contre Opus</span>`
      : `<b>×${ratio(1 / cr)} moins cher</b><span>coût réel par avis, Opus contre Jev</span>`) : "";
    const ag = sm.agree || {};
    $("#finalBox").innerHTML = `<h2>Bilan · ${sm.n} vrais avis ${esc(p.label || "")}</h2>
      <div class="sub">${srcTxt} · ${sm.conc} avis en parallèle pour chacun · mêmes avis, même ordre, mêmes 4 questions</div>
      <div class="ftable">
        <div></div><div class="h"><img src="${Arena.LOGOS.jev}" alt=""><span style="color:var(--jev)">Jev</span></div>
        <div class="h"><img src="${Arena.LOGOS.opus}" alt=""><span style="color:var(--opus)">Claude Opus 5.5</span></div>
        ${row("Temps total", "elapsed", secs, "low")}
        ${row("Note exacte", "exact_pct", pct, "high")}
        ${row("À une étoile près", "within1_pct", pct, "high")}
        ${row("Latence médiane", "lat_med", Arena.ms, "low")}
        ${row("Coût réel", "cost", usd, "low")}
        ${row("Coût pour 10 000 avis", "cost_10k_extrapolated", usd, "low", "extrapolé")}
        ${row("Temps pour 10 000 avis", "time_10k_extrapolated_s", longDur, "low", "extrapolé")}
        ${row("Erreurs", "err", (v) => String(v ?? 0), "low")}
      </div>
      <div class="bottom">
        ${speedCard ? `<div class="card">${speedCard}</div>` : ""}
        ${costCard ? `<div class="card">${costCard}</div>` : ""}
        ${ag.n ? `<div class="card"><b>${pct(ag.topic)}</b><span>même sujet choisi par les deux (${ag.n} avis)</span></div>
        <div class="card"><b>${pct(ag.stars)}</b><span>même note devinée par les deux</span></div>` : ""}
      </div>
      <div class="note">Vraie note = étoiles laissées par le client. Extrapolé = coût moyen réel par avis × 10 000, et débit mesuré ici à la même concurrence (${sm.conc}). </div>
      <div class="actions"><button class="btn ghost" data-act="bilan">Masquer le bilan <kbd>B</kbd></button>
        <button class="btn violet" data-act="reset">Nouvelle course <kbd>R</kbd></button></div>`;
  }
  function showFinal(on) {
    S.finalShown = on && !!S.summary;
    if (S.finalShown) renderFinal();
    $("#final").classList.toggle("show", S.finalShown);
  }

  // ------------------------------------------------------------ phases, budget, erreurs
  const PH = { idle: "prêt", fetch: "récupération des avis", ready: "avis chargés", race: "en direct", done: "terminé", error: "erreur" };
  function setPhase(ph) {
    S.phase = ph;
    const el = $("#phase");
    el.textContent = PH[ph] || ph;
    el.className = "chip phase-chip" + (ph === "race" || ph === "fetch" ? " live" : "");
    const busy = ph === "race" || ph === "fetch";
    $("#btnStart").disabled = busy;
    $("#btnLoad").disabled = busy;
    $("#btnStart").classList.toggle("glow", ph === "ready");
    MODELS.forEach(renderStatus);
  }
  const costTw = new Tw($("#raceCostV"), usd, 0.25);
  function renderCost(instant) {
    const c = MODELS.reduce((a, m) => a + ((S.stats[m] && S.stats[m].cost) || 0), 0);
    costTw.set(c, instant);
  }
  let errTimer = null;
  function showErr(msg) {
    const e = $("#errbar");
    e.textContent = msg;
    e.classList.add("show");
    clearTimeout(errTimer);
    errTimer = setTimeout(() => e.classList.remove("show"), 12000);
  }

  // ------------------------------------------------------------ chargement complet (connexion, reset)
  function clearRun() {
    S.reviews = []; S.fetchLog = []; S.fetchInfo = {}; S.summary = null; S.clientStart = null;
    S.answers = { jev: {}, opus: {} }; S.inflight = { jev: new Set(), opus: new Set() }; S.stats = { jev: null, opus: null };
    track.innerHTML = ""; cards.length = 0; flog.innerHTML = ""; flog.style.display = "none"; intro.style.display = "";
    scrollPos = scrollTarget = 0;
    showFinal(false);
    $("#errbar").classList.remove("show");
    MODELS.forEach((m) => { buildSide(m); renderStats(m, true); });
    renderCost(true);
    renderIntro(); renderCenterHead();
  }
  function loadSnapshot(d) {
    S.presets = d.presets; S.targets = d.targets; S.concs = d.concs; S.topics = d.topics;
    S.config = d.config; S.preset = d.preset; S.budget = d.budget;
    renderConfig();
    clearRun();
    S.fetchLog = d.fetch_log || [];
    S.fetchInfo = d.fetch_info || {};
    setPhase(d.phase);
    if (d.phase === "fetch") { showFetchPlan(d.fetch_plan); S.fetchLog.forEach(addFetchRow); }
    if (d.reviews && d.reviews.length) {
      S.reviews = d.reviews;
      buildCards(); renderCenterHead();
      MODELS.forEach((m) => { side[m].L = null; layoutSide(m, Math.max(8, Math.ceil(S.reviews.length * 0.45))); });
      MODELS.forEach((m) => (d.inflight[m] || []).forEach((i) => S.inflight[m].add(i)));
      for (const m of MODELS) for (const a of d.answers[m] || []) onAnswer(a, false);
      MODELS.forEach((m) => { S.stats[m] = d.stats && d.stats[m] && d.stats[m].done ? d.stats[m] : null; renderStats(m, true); });
      renderCost(true);
      cards.forEach((c, i) => MODELS.forEach((m) => updatePill(m, i)));
      scrollPos = scrollTarget;
    }
    if (d.phase === "race" && d.elapsed != null) S.clientStart = performance.now() - d.elapsed * 1000;
    S.summary = d.summary;
    if (d.phase === "done" && d.summary) showFinal(true);
    if (d.error) showErr(d.error);
  }

  // ------------------------------------------------------------ evenements du serveur
  function onEvent(ev) {
    switch (ev.type) {
      case "snapshot":
        if (firstSnapshot) {
          firstSnapshot = false;
          const d = ev.data;
          if (d.phase !== "fetch" && d.phase !== "race") {
            const blank = { ...d, phase: "idle", reviews: [], answers: { jev: [], opus: [] }, inflight: { jev: [], opus: [] },
              stats: null, summary: null, error: null, fetch_log: [], fetch_plan: [], fetch_info: {}, elapsed: null };
            loadSnapshot(blank);
            if (d.phase !== "idle" || (d.reviews && d.reviews.length)) conn.send({ cmd: "reset" });
            break;
          }
        }
        loadSnapshot(ev.data);
        break;
      case "reset": loadSnapshot(ev.data); break;
      case "config":
        S.config = ev.config; S.preset = ev.preset; if (ev.budget) S.budget = ev.budget;
        renderConfig(); renderCost(); break;
      case "phase":
        if (ev.phase === "fetch") { if (ev.config) S.config = ev.config; if (ev.preset) S.preset = ev.preset; renderConfig(); clearRun(); }
        setPhase(ev.phase);
        if (ev.phase === "fetch") {
          showFetchPlan(ev.plan);
          $("#cTitle").textContent = "Récupération en direct";
          const src = S.preset && S.preset.source;
          $("#cSub").innerHTML = src === "trustpilot"
            ? `Lightpanda ouvre ${ev.plan ? ev.plan.length : ""} pages Trustpilot filtrées par note, en parallèle`
            : src === "googleplay" ? `Le Play Store est interrogé en direct : un lot d'avis par note, les 5 en parallèle`
            : `Le flux RSS public de l'App Store : 10 pages de 50 avis, en parallèle`;
        }
        if (ev.error) showErr(ev.error);
        break;
      case "fetch_page": S.fetchLog.push(ev); addFetchRow(ev); break;
      case "reviews":
        S.reviews = ev.items; S.fetchInfo = ev.fetch_info || {};
        setPhase("ready");
        buildCards(); renderCenterHead();
        MODELS.forEach((m) => { side[m].L = null; layoutSide(m, Math.max(8, Math.ceil(S.reviews.length * 0.45))); renderStats(m, true); });
        break;
      case "race_start":
        S.clientStart = performance.now(); S.summary = null;
        if (ev.budget) { S.budget = ev.budget; renderCost(); }
        setPhase("race");
        renderCenterHead();
        break;
      case "item_start": {
        S.inflight[ev.model].add(ev.i); updatePill(ev.model, ev.i);
        const o = side[ev.model], st = S.stats[ev.model];
        if (o && !(st && st.finished)) o.inflight.textContent = `${S.inflight[ev.model].size} en cours` + (st ? ` · ${Arena.num(st.ok * 4)} décisions` : "");
        if (!st) renderStatus(ev.model);
        break;
      }
      case "answer":
        onAnswer(ev, true);
        if (ev.budget) { S.budget = ev.budget; renderCost(); }
        break;
      case "side_done": S.stats[ev.model] = ev.stats; renderStats(ev.model); break;
      case "done":
        S.summary = ev.summary; setPhase("done");
        if (ev.budget) { S.budget = ev.budget; renderCost(); }
        MODELS.forEach((m) => { if (ev.summary && S.stats[m]) renderStatus(m); });
        if (ev.error) showErr(ev.error);
        setTimeout(() => { if (S.phase === "done") showFinal(true); }, 1400);
        break;
      case "budget": showErr("Plafond de budget atteint : " + ev.error); break;
      case "error": showErr(ev.error || "erreur"); break;
    }
  }

  let firstSnapshot = true;
  const conn = Arena.connect("avis", onEvent, (ok) => {
    const c = $("#conn"); c.textContent = ok ? "" : "connexion perdue, reconnexion…"; c.className = "conn" + (ok ? "" : " off");
  });

  // ------------------------------------------------------------ commandes
  const cfgFromUi = () => ({ preset: $("#selPreset").value, target: +$("#selTarget").value, conc: +$("#selConc").value });
  function sendConfig() {
    const c = cfgFromUi();
    if (S.phase === "ready") conn.send({ cmd: "reset" });
    conn.send({ cmd: "config", ...c });
  }
  ["#selPreset", "#selTarget", "#selConc"].forEach((s) => $(s).addEventListener("change", (e) => { sendConfig(); e.target.blur(); }));
  function act(a) {
    const busy = S.phase === "fetch" || S.phase === "race";
    if (a === "start") { if (!busy) conn.send({ cmd: "start", ...cfgFromUi() }); }
    else if (a === "load") { if (!busy) conn.send({ cmd: "load", ...cfgFromUi() }); }
    else if (a === "reset") conn.send({ cmd: "reset" });
    else if (a === "bilan") showFinal(!S.finalShown);
  }
  // les boutons ne prennent jamais le focus (sinon Espace les re-cliquerait)
  stage.addEventListener("mousedown", (e) => { if (e.target.closest("button")) e.preventDefault(); });
  stage.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (b && !b.disabled) act(b.dataset.act);
  });
  addEventListener("keydown", (e) => {
    if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (e.code === "Space") {
      e.preventDefault();
      if (document.activeElement) document.activeElement.blur();
      act("start");
    } else if (k === "c") act("load");
    else if (k === "r") act("reset");
    else if (k === "b") act("bilan");
    else if (k === "h") $("#ctrls").classList.toggle("hidden");
  });

  // ------------------------------------------------------------ boucle d'animation
  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    tweens.forEach((t) => t.step(dt));
    scrollPos += (scrollTarget - scrollPos) * (1 - Math.exp(-dt / 0.38));
    if (Math.abs(scrollTarget - scrollPos) < 0.3) scrollPos = scrollTarget;
    track.style.transform = `translate3d(0, ${-scrollPos.toFixed(2)}px, 0)`;
    const live = S.phase === "race" && S.clientStart != null ? (now - S.clientStart) / 1000 : null;
    let clock = 0;
    for (const m of MODELS) {
      const o = side[m], st = S.stats[m];
      if (!o) continue;
      let t = 0;
      if (st && st.finished) t = st.elapsed;
      else if (live != null) t = live;
      else if (st) t = st.elapsed || 0;
      clock = Math.max(clock, t);
      const s = fr1(t) + " s";
      if (o.time.textContent !== s) o.time.textContent = s;
    }
    const cs = `${Math.floor(clock / 60)}:${String(Math.floor(clock % 60)).padStart(2, "0")}`;
    const ck = $("#clock");
    if (ck.textContent !== cs) ck.textContent = cs;
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  addEventListener("resize", updateScrollTarget);
})();
