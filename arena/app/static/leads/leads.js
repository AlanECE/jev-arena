// La course aux leads : Jev (gauche) contre Opus (droite) sur le meme flux de vrais leads.
// Tout vient du serveur (WebSocket /ws/leads) : rien n'est simule ici, la page ne fait qu'afficher.
// La page s'ouvre toujours vierge : seule une course EN COURS est renvoyee par le snapshot.
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const MODELS = ["jev", "opus"];
  const VISIBLE = 13;      // lignes de leads gardees dans le DOM
  const EMAILS_KEPT = 18;  // emails gardes dans la liste (les plus recents en haut)
  const STAGE_FR = {
    prelaunch: "Pré-lancement", just_launched: "Vient de sortir", small_business: "PME · produit",
    big_company: "Grande entreprise", agency: "Agence / service", no_product: "Pas de produit", unclear: "Flou",
  };
  const TOOL_FR = { lightpanda: "Lightpanda", crawl4ai: "Crawl4AI" };
  const SRC_FR = { github: "profil GitHub", accueil: "page d'accueil", lien: "page choisie" };

  const S = {
    phase: "idle", config: { target: 100, conc: 8 }, stats: null, statsAt: 0, mask: true,
    flags: {}, summary: null, showFinal: false,
  };
  const side = {};
  for (const m of MODELS) {
    const root = $(`#side-${m}`);
    const k = {};
    root.querySelectorAll("[data-k]").forEach((el) => (k[el.dataset.k] = el));
    side[m] = { root, k };
  }

  // ------------------------------------------------------------------ masquage (par defaut : M pour demasquer)
  function maskHost(h) {
    if (!h) return "";
    if (!S.mask) return h;
    const parts = h.split(".");
    const tld = parts.length > 1 ? parts[parts.length - 1] : "";
    const body = parts.slice(0, -1).join(".");
    return (body.slice(0, 2) || "?") + "***" + (tld ? "." + tld : "");
  }
  function maskEmail(e) {
    if (!e) return "";
    if (!S.mask) return e;
    const [loc, dom = ""] = e.split("@");
    const parts = dom.split(".");
    const tld = parts.length > 1 ? parts[parts.length - 1] : "";
    return `${loc[0] || "?"}***@${(parts[0] || "?")[0]}***${tld ? "." + tld : ""}`;
  }
  function maskPath(p) {
    if (!p) return "";
    const clean = p.split("?")[0];
    if (!S.mask) return p.length > 30 ? p.slice(0, 29) + "…" : p;
    const seg = clean.split("/").filter(Boolean);
    if (!seg.length) return "/";
    const first = seg[0].length > 16 ? seg[0].slice(0, 15) + "…" : seg[0];
    return "/" + first + (seg.length > 1 ? "/…" : "");
  }
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ------------------------------------------------------------------ formats
  const pct = (x) => (x == null || isNaN(x) ? "–" : Math.round(x * 100) + " %");
  function tclock(s, dec = true) {
    if (s == null || isNaN(s)) return dec ? "0:00,0" : "0:00";
    s = Math.max(0, s);
    const m = Math.floor(s / 60), r = s - m * 60;
    if (!dec) return `${m}:${String(Math.floor(r)).padStart(2, "0")}`;
    const whole = Math.floor(r), d = Math.floor((r - whole) * 10);
    return `${m}:${String(whole).padStart(2, "0")},${d}`;
  }
  function secs(ms) {
    if (ms == null || isNaN(ms)) return "–";
    return ms >= 1000 ? (ms / 1000).toFixed(1).replace(".", ",") + " s" : Math.round(ms) + " ms";
  }
  function setText(el, txt) { if (el && el.textContent !== String(txt)) el.textContent = txt; }
  const plural = (n, w) => `${n} ${w}${n > 1 ? "s" : ""}`;

  // ------------------------------------------------------------------ la liste d'emails qui s'incremente
  function emailHTML(e) {
    const src = SRC_FR[e.src] || e.src || "";
    return `<div class="in"><div class="ad">${esc(maskEmail(e.email))}</div><div class="eh"><b>${esc(maskHost(e.host))}</b>${src ? " · " + src : ""}</div></div>`;
  }
  function addEmail(m, e, animate) {
    const box = side[m].k.elist;
    const empty = $(".empty", box);
    if (empty) empty.remove();
    const el = document.createElement("div");
    el.className = "em";
    el.innerHTML = emailHTML(e);
    el._e = e;
    if (animate) {
      el.classList.add("enter", "fresh");
      box.prepend(el);
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("enter")));
      setTimeout(() => el.classList.remove("fresh"), 900);
    } else {
      box.prepend(el);
    }
    const all = box.querySelectorAll(".em");
    for (let i = EMAILS_KEPT; i < all.length; i++) all[i].remove();
  }
  function clearEmails() {
    for (const m of MODELS) side[m].k.elist.innerHTML = `<div class="empty"></div>`;
  }
  function rerenderEmails() {
    for (const m of MODELS) side[m].k.elist.querySelectorAll(".em").forEach((el) => { el.innerHTML = emailHTML(el._e); });
  }

  // ------------------------------------------------------------------ lignes de leads
  function rowHTML(r) {
    const err = !!r.error && r.verdict == null;
    const stage = r.stage ? `<span class="stage ${r.stage}">${STAGE_FR[r.stage] || r.stage}</span>` : `<span class="stage none">${err ? "erreur" : "–"}</span>`;
    let host = esc(maskHost(r.host));
    if (!S.mask && r.username) host += ` <span class="who">@${esc(r.username)}${r.name ? " · " + esc(r.name) : ""}</span>`;
    const tag = S.flags[r.lead] === false ? `<span class="tag">désaccord</span>` : "";
    // 2e ligne : categorie, le lien que le modele a choisi d'ouvrir, et s'il y a un email (la liste a cote le montre)
    const bits = [stage];
    const L = r.link || {};
    if (L.key === "none") bits.push("aucun lien");
    else if (L.path) {
      let s = `<b>${esc(maskPath(L.path))}</b>`;
      if (L.fetched === false) s += ` <span class="bad">illisible</span>`;
      else if (L.emails) s += ` <span class="em-txt">+${L.emails}</span>`;
      bits.push(s);
    }
    if (r.email) { if (!L.emails) bits.push(`<span class="em-txt">${(r.emails || []).length > 1 ? r.emails.length + " emails" : "email"}</span>`); }
    else if (!err) bits.push("pas d'email");
    if (r.has_video != null && r.has_video >= 0.5) bits.push("vidéo déjà");
    if (r.error) bits.push(`<span class="bad">${esc(r.error.slice(0, 60))}</span>`);
    const prio = r.priority == null ? "" : [0, 1, 2].map((i) => `<i class="${i < r.priority ? "on" : ""}"></i>`).join("");
    let verdict;
    if (r.verdict == null) verdict = `<span class="verdict err">erreur</span>`;
    else verdict = `<span class="verdict ${r.verdict ? "yes" : "no"}"><span class="v">${r.verdict ? "OUI" : "NON"}</span><span class="p">${pct(r.fit)}</span></span>`;
    const lat = `${secs(r.ms)}<small>${plural(r.decisions || 0, "décision")}</small>`;
    return `<div class="in"><div class="ident"><div class="h">${host}${tag}</div><div class="s">${bits.join(" · ")}</div></div>`
      + `<div class="prio">${prio}</div><div>${verdict}</div><div class="lat">${lat}</div></div>`;
  }

  function addRow(m, r, animate) {
    const box = side[m].k.rows;
    const empty = $(".empty", box);
    if (empty) empty.remove();
    const el = document.createElement("div");
    el.className = "row" + (r.verdict == null && r.error ? " err" : "") + (S.flags[r.lead] === false ? " dis" : "");
    el.dataset.lead = r.lead;
    el.innerHTML = rowHTML(r);
    el._row = r;
    if (animate) {
      el.classList.add("enter", "fresh");
      box.prepend(el);
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("enter")));
      setTimeout(() => el.classList.remove("fresh"), 650);
    } else {
      box.prepend(el);
    }
    const all = box.querySelectorAll(".row");
    for (let i = VISIBLE; i < all.length; i++) all[i].remove();
  }

  function clearRows(msg) {
    for (const m of MODELS) side[m].k.rows.innerHTML = `<div class="empty">${msg || "Prêt · « Lancer la course » ou Espace"}</div>`;
  }

  function rerenderRows() {
    for (const m of MODELS) {
      side[m].k.rows.querySelectorAll(".row").forEach((el) => {
        el.innerHTML = rowHTML(el._row);
        el.classList.toggle("dis", S.flags[el._row.lead] === false);
      });
    }
  }

  function markAgree(lead, agree) {
    S.flags[lead] = agree;
    if (agree) return;
    for (const m of MODELS) {
      const el = side[m].k.rows.querySelector(`.row[data-lead="${lead}"]`);
      if (el) { el.classList.add("dis"); el.innerHTML = rowHTML(el._row); }
    }
  }

  // ------------------------------------------------------------------ fil du web
  function feedLine(e) {
    const ok = e.ok;
    let who = `<span class="dot web"></span>`;
    if (e.model) who = `<span class="dot ${e.model}"></span>`;
    let what = esc(maskHost(e.host));
    if (e.kind === "contact") what += " " + esc(maskPath(e.path));
    let res;
    if (e.cached) res = "déjà lue";
    else if (ok) res = `${TOOL_FR[e.tool] || e.tool} · ${secs(e.ms)}`;
    else res = "échec · " + secs(e.ms);
    const title = e.error ? ` title="${esc(e.error)}"` : "";
    return `<div class="fl ${ok ? "ok" : "fail"}"${title}><span class="tm">${tclock(e.t, false)}</span><span class="what">${who}${what}</span><span class="res">${res}</span></div>`;
  }
  function addFeed(e, animate = true) {
    const box = $("#feed");
    const d = document.createElement("div");
    d.innerHTML = feedLine(e);
    const el = d.firstChild;
    el._e = e;
    if (!animate) el.style.animation = "none";
    box.prepend(el);
    const all = box.children;
    for (let i = all.length - 1; i >= 12; i--) all[i].remove();
  }
  function rerenderFeed() {
    $("#feed").querySelectorAll(".fl").forEach((el) => {
      const d = document.createElement("div");
      d.innerHTML = feedLine(el._e);
      const n = d.firstChild; n._e = el._e; n.style.animation = "none";
      el.replaceWith(n);
    });
  }

  // ------------------------------------------------------------------ compteurs
  function elapsedNow() {
    if (!S.stats) return 0;
    if (S.phase !== "running") return S.stats.elapsed || 0;
    return (S.stats.elapsed || 0) + (performance.now() - S.statsAt) / 1000;
  }

  function renderStats() {
    const st = S.stats;
    if (!st) return;
    const target = st.target || S.config.target;
    setText($("#t-target"), target);
    setText($("#n-target"), target);
    for (const m of MODELS) {
      const s = st.models[m], k = side[m].k;
      setText(k.done, s.done);
      setText(k.target, target);
      k.bar.style.width = Math.min(100, (100 * s.done) / Math.max(1, target)) + "%";
      setText(k.decisions, Arena.num(s.decisions));
      setText(k.lat, s.lat_med_ms != null ? secs(s.lat_med_ms) : "–");
      setText(k.cost, Arena.usd(s.cost, s.cost >= 1 ? 3 : 4));
      setText(k.oui, s.oui);
      setText(k.waiting, s.waiting);
      k.waiting.classList.toggle("warn", s.waiting >= 10);
      // la liste d'emails
      const ec = s.email_count || 0;
      setText(k.ecount, ec);
      let esub;
      if (S.phase === "idle") esub = "la liste se remplit pendant la course";
      else if (!ec) esub = s.done ? `aucune adresse sur ${plural(s.done, "lead")}` : "en attente du premier lead…";
      else esub = `${s.emails}/${s.done} leads avec email${s.emails_link ? ` · ${s.emails_link} via page choisie` : ""}`;
      setText(k.esub, esub);
      let sub;
      if (S.phase === "idle") sub = "En attente du départ";
      else if (s.t_done != null) sub = `${plural(s.emails, "lead")} avec un email · ${plural(s.links_opened, "page")} ouverte${s.links_opened > 1 ? "s" : ""}`;
      else if (s.inflight === 0 && s.waiting === 0 && S.phase === "running") sub = "attend le web…";
      else sub = `${plural(s.inflight, "lead")} en cours${s.lat_med_ms != null ? "" : " · première décision…"}`;
      if (s.failed_calls) sub += ` · ${plural(s.failed_calls, "erreur")}`;
      setText(k.sub, sub);
      const dc = k.donechip;
      if (s.t_done != null) {
        setText(dc, `${s.done} leads en ${tclock(s.t_done)}`);
        dc.classList.add("on");
      } else dc.classList.remove("on");
    }
    const p = st.pipe || {}, f = p.fetch || {};
    setText($("#n-profiles"), Arena.num(p.profiles || 0));
    setText($("#n-pages"), Arena.num(f.pages || 0));
    setText($("#n-ready"), p.dispatched || 0);
    setText($("#n-contact"), Arena.num((st.models.jev.links_opened || 0) + (st.models.opus.links_opened || 0)));
    setText($("#n-cache"), f.cache_hits || 0);
    let q = "recherche de créateurs d'applis";
    if (p.github_pause) q = `GitHub : limite atteinte, pause ${p.github_pause} s`;
    else if (p.query) q = `« ${p.query.replace(/ in:bio/g, "").replace(/ location:/g, " · ").replace(/"/g, "")} » · ${p.with_site || 0} avec un site`;
    setText($("#q-txt"), q);
    setText($("#n-lp"), Arena.num(f.lightpanda || 0));
    setText($("#n-c4"), Arena.num(f.crawl4ai || 0));
    setText($("#n-fail"), Arena.num(f.fail || 0));
    const srcOn = S.phase === "running" && p.source_done == null;
    $("#st-scout").classList.toggle("active", srcOn);
    $("#st-web").classList.toggle("active", S.phase === "running" && (srcOn || (f.inflight || 0) > 0));
    $("#flow1").classList.toggle("on", srcOn);
    $("#flow2").classList.toggle("on", srcOn);
    const a = st.agree || {};
    setText($("#agree"), a.n ? pct(a.verdict) : "–");
    setText($("#agree-sub"), a.n ? `verdict OUI / NON · ${plural(a.n, "lead")}${a.link != null ? " · même lien " + pct(a.link) : ""}` : "sur le verdict OUI / NON");
  }

  function tick() {
    const el = elapsedNow();
    setText($("#clock"), tclock(el, false));
    if (S.stats) {
      for (const m of MODELS) {
        const s = S.stats.models[m];
        const t = s.t_done != null ? s.t_done : S.phase === "running" ? el : S.stats.elapsed || 0;
        setText(side[m].k.time, tclock(S.phase === "idle" ? 0 : t));
      }
    }
    requestAnimationFrame(tick);
  }

  // ------------------------------------------------------------------ phase, reglages
  function setPhase(ph) {
    S.phase = ph;
    const chip = $("#phase");
    chip.className = "chip";
    const map = { idle: "prêt", running: "en direct", done: "terminé", error: "arrêté", stopped: "arrêté" };
    chip.textContent = map[ph] || ph;
    if (ph === "running") chip.classList.add("live");
    if (ph === "done") chip.classList.add("done");
    if (ph === "error") chip.classList.add("error");
    document.body.classList.toggle("running", ph === "running");
    document.body.classList.toggle("finished", ph === "done");
    $("#btn-start").hidden = ph !== "idle";
    $("#btn-new").hidden = ph === "idle";
  }

  function renderConfig(c) {
    if (!c) return;
    S.config = { ...S.config, ...c };
    const st = $("#sel-target"), sc = $("#sel-conc");
    st.innerHTML = (c.targets || [5, 10, 25, 50, 100]).map((t) => `<option ${t === c.target ? "selected" : ""}>${t}</option>`).join("");
    sc.innerHTML = (c.concs || [4, 8, 12, 16]).map((t) => `<option ${t === c.conc ? "selected" : ""}>${t}</option>`).join("");
    if (S.phase === "idle") {
      setText($("#t-target"), c.target);
      setText($("#n-target"), c.target);
      for (const m of MODELS) setText(side[m].k.target, c.target);
    }
    setText($("#conc-txt"), c.conc);
  }

  function idleStats() {
    const z = { done: 0, decisions: 0, cost: 0, emails: 0, emails_link: 0, email_count: 0, oui: 0, waiting: 0, inflight: 0,
      t_done: null, lat_med_ms: null, failed_calls: 0, links_opened: 0 };
    return { elapsed: 0, target: S.config.target, pipe: { fetch: {} }, models: { jev: { ...z }, opus: { ...z } }, agree: {} };
  }

  function blank(msg) {  // remet toute la page a zero (la plateforme est toujours vierge entre deux courses)
    S.summary = null; S.showFinal = false; renderFinal(); banner(null);
    clearRows(msg); clearEmails(); $("#feed").innerHTML = ""; S.flags = {};
  }

  // ------------------------------------------------------------------ bilan
  function renderFinal() {
    const sm = S.summary;
    const box = $("#final");
    if (!sm || !S.showFinal) {
      box.classList.remove("show");
      setTimeout(() => { if (!S.showFinal) box.hidden = true; }, 600);
      return;
    }
    const J = sm.models.jev, O = sm.models.opus, A = sm.agreement || {}, R = sm.ratio || {};
    const col = (m, s) => `
      <div class="fcol ${m}">
        <div class="model-head"><img src="${Arena.LOGOS[m]}" alt=""><div><div class="name accent">${Arena.NAMES[m]}</div></div></div>
        <div class="frows">
          <div class="frow hero"><span class="k">Temps pour ${s.done} leads</span><span class="v">${tclock(s.time_s)}</span></div>
          <div class="frow"><span class="k">Emails trouvés</span><span class="v">${s.email_count || 0} <small>sur ${plural(s.emails, "lead")}${s.emails_link ? `, ${s.emails_link} via sa page choisie` : ""}</small></span></div>
          <div class="frow"><span class="k">Décisions</span><span class="v">${Arena.num(s.decisions)}</span></div>
          <div class="frow"><span class="k">Coût réel</span><span class="v">${Arena.usd(s.cost, s.cost >= 1 ? 3 : 4)}</span></div>
          <div class="frow"><span class="k">À contacter</span><span class="v">${s.oui} <small>(${pct(s.oui_pct)})</small></span></div>
          <div class="frow"><span class="k">Latence médiane d'une décision</span><span class="v">${secs(s.lat_med_ms)}</span></div>
        </div>
      </div>`;
    const fx = (x) => (x == null ? "–" : "×" + (x >= 10 ? Math.round(x) : x.toFixed(1).replace(".", ",")));
    const dis = sm.disagreements || [];
    const vd = (x) => `${x.fit >= 0.5 ? "OUI" : "NON"} ${pct(x.fit)} · ${STAGE_FR[x.stage] || x.stage || "?"}`;
    const disHTML = dis.length
      ? `<div class="items">${dis.slice(0, 8).map((d) => `<div class="it"><span class="h">${esc(maskHost(d.host))}</span><span class="j">Jev ${vd(d.jev)}</span><span class="o">Opus ${vd(d.opus)}</span></div>`).join("")}</div>`
        + (dis.length > 8 ? `<div class="ffoot">et ${dis.length - 8} autre${dis.length - 8 > 1 ? "s" : ""}</div>` : "")
      : `<div class="none">Aucun désaccord sur le verdict.</div>`;
    box.innerHTML = `
      <div class="fc card">
        <h2>Objectif atteint : ${sm.dispatched} leads qualifiés${sm.exhausted ? " (source épuisée)" : ""}</h2>
        <div class="lead">Mêmes leads, même lecteur web, mêmes questions, ${S.stats ? S.stats.conc : ""} en parallèle de chaque côté. Coûts réels renvoyés par OpenRouter.</div>
        <div class="fgrid">
          ${col("jev", J)}
          <div class="fmid">
            <div class="x">${fx(R.time)}<small>plus rapide de bout en bout (web compris)</small></div>
            <div class="x">${fx(R.cost)}<small>moins cher</small></div>
            <div class="x agree-big">${pct(A.verdict)}<small>d'accord sur OUI / NON (${A.n} leads)${A.stage != null ? `<br>catégorie ${pct(A.stage)} · même lien ${pct(A.link)}` : ""}</small></div>
          </div>
          ${col("opus", O)}
        </div>
        <div class="fdis"><div class="label">Désaccords sur le verdict (${dis.length})</div>${disHTML}</div>
        <div class="factions"><button class="btn ghost" data-act="close" type="button">Voir les leads</button><button class="btn violet" data-act="new" type="button">Nouvelle course (R)</button></div>
        <div class="ffoot">Source : ${Arena.num(sm.pipe.profiles)} profils GitHub lus par Scout, ${Arena.num(sm.pipe.fetch.pages)} pages lues (Lightpanda ${sm.pipe.fetch.lightpanda}, Crawl4AI ${sm.pipe.fetch.crawl4ai}, échecs ${sm.pipe.fetch.fail}). Emails ${S.mask ? "masqués" : "visibles"}, personne n'a été contacté.</div>
      </div>`;
    box.querySelector('[data-act="new"]').onclick = () => newRace();
    box.querySelector('[data-act="close"]').onclick = () => { S.showFinal = false; renderFinal(); };
    box.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => box.classList.add("show")));
  }

  // ------------------------------------------------------------------ evenements du serveur
  function onSnapshot(d) {
    renderConfig(d.config);
    setPhase(d.phase || "idle");
    blank();
    for (const [k, v] of Object.entries(d.agree_flags || {})) S.flags[k] = v;
    S.stats = d.stats || idleStats();
    S.statsAt = performance.now();
    for (const m of MODELS) {
      for (const r of (d.rows || {})[m] || []) addRow(m, r, false);
      for (const e of (d.emails || {})[m] || []) addEmail(m, e, false);
    }
    for (const e of d.feed || []) addFeed(e, false);
    renderStats();
  }

  function banner(msg) {
    const b = $("#banner");
    if (!msg) { b.hidden = true; return; }
    b.textContent = msg;
    b.hidden = false;
  }

  function onEvent(e) {
    switch (e.type) {
      case "snapshot": onSnapshot(e.data); break;
      case "config": renderConfig(e.config); break;
      case "reset":
        blank();
        setPhase("idle");
        renderConfig(e.config); S.stats = idleStats(); renderStats();
        break;
      case "race_start":
        blank("Scout cherche les premiers leads…");
        setPhase("running");
        S.stats = { ...idleStats(), target: e.target, conc: e.conc }; S.statsAt = performance.now();
        setText($("#conc-txt"), e.conc);
        renderStats();
        break;
      case "stats":
        S.stats = e; S.statsAt = performance.now();
        if (e.phase && e.phase !== S.phase && e.phase !== "idle") setPhase(e.phase);
        renderStats();
        break;
      case "lead_done":
        addRow(e.row.model, e.row, true);
        for (const ne of e.row.new_emails || []) addEmail(e.row.model, { ...ne, host: e.row.host, lead: e.row.lead }, true);
        break;
      case "agree": markAgree(e.lead, e.agree); break;
      case "web": addFeed(e, true); break;
      case "race_end":
        setPhase("done"); S.summary = e.summary;
        setTimeout(() => { S.showFinal = true; renderFinal(); }, 1400);
        break;
      case "race_error":
        setPhase("error"); banner("Course arrêtée : " + (e.error || "erreur"));
        break;
      case "error": Arena.toast(e.error || "erreur"); break;
    }
  }

  const ws = Arena.connect("leads", onEvent, (on) => {
    const c = $("#conn");
    c.textContent = on ? "connecté" : "déconnecté";
    c.classList.toggle("off", !on);
    if (on) {
      const qs = new URLSearchParams(location.search);
      const t = +qs.get("target"), cc = +qs.get("conc");
      if (t || cc) setTimeout(() => ws.send({ cmd: "config", target: t || undefined, conc: cc || undefined }), 200);
    }
  });

  // ------------------------------------------------------------------ clavier et reglages
  function start() {
    if (S.phase === "running") return;
    if (S.phase !== "idle") { newRace(); return; }  // apres une course : Espace remet d'abord la page a blanc
    ws.send({ cmd: "start", target: S.config.target, conc: S.config.conc });
  }
  function newRace() { ws.send({ cmd: "reset" }); }
  $("#btn-start").addEventListener("click", (e) => { e.currentTarget.blur(); start(); });
  $("#btn-new").addEventListener("click", (e) => { e.currentTarget.blur(); newRace(); });
  document.addEventListener("keydown", (ev) => {
    if (ev.target.tagName === "SELECT" || ev.repeat) return;
    if (ev.code === "Space") { ev.preventDefault(); start(); }
    else if (ev.key === "r" || ev.key === "R") newRace();
    else if (ev.key === "m" || ev.key === "M") toggleMask();
    else if (ev.key === "f" || ev.key === "F") { if (S.summary) { S.showFinal = !S.showFinal; renderFinal(); } }
    else if (ev.key === "Escape") { S.showFinal = false; renderFinal(); }
  });
  function toggleMask() {
    S.mask = !S.mask;
    const c = $("#mask-chip");
    c.textContent = S.mask ? "masqué" : "DÉMASQUÉ";
    c.classList.toggle("off", !S.mask);
    rerenderRows(); rerenderEmails(); rerenderFeed();
    if (S.showFinal) renderFinal();
  }
  $("#mask-chip").addEventListener("click", toggleMask);
  $("#sel-target").addEventListener("change", (e) => { ws.send({ cmd: "config", target: +e.target.value }); e.target.blur(); });
  $("#sel-conc").addEventListener("change", (e) => { ws.send({ cmd: "config", conc: +e.target.value }); e.target.blur(); });

  S.stats = idleStats();
  renderStats();
  requestAnimationFrame(tick);
})();
