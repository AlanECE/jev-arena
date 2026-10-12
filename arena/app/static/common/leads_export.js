// Export Excel de la course aux leads (bouton « Leads en Excel » de la page /leads).
// Meme classeur que tools/export_leads.py : « Résumé » + une feuille par modèle (OUI en haut, par priorité puis score),
// emails, source de l'email, LinkedIn, GitHub, bio, signaux du site. Les données ne sont PAS masquées : le fichier sert
// à prospecter, il ne se publie pas.
// Données : la page les accumule pendant la course (événements WebSocket). Si le serveur expose /api/leads/export
// (version à trois modèles), on part de lui : l'export marche aussi après un rechargement de la page.
(function () {
  const ORDER = ["jev", "laya", "opus"];
  const TAB = { jev: "FFD63FAE", laya: "FF2A78D6", opus: "FFD97757" };
  const STAGE_FR = {
    prelaunch: "Pré-lancement", just_launched: "Vient de lancer", small_business: "TPE / PME · produit",
    big_company: "Grande entreprise", agency: "Agence / freelance", no_product: "Pas de produit", unclear: "Incertain",
  };
  const PRIO_FR = { 0: "À ne pas contacter", 1: "Faible", 2: "Bon prospect", 3: "Prospect chaud" };
  const SRC_FR = { github: "profil GitHub", accueil: "page d'accueil", home: "page d'accueil", lien: "page choisie" };
  const SIG_FR = {
    app_store_link: "App Store", google_play_link: "Google Play", product_hunt_link: "Product Hunt",
    testflight_link: "TestFlight", chrome_extension_link: "extension Chrome", waitlist_or_early_access: "liste d'attente",
    signup_or_download_button: "bouton d'inscription", email_capture_form: "formulaire email", pricing: "page tarifs",
  };
  const HEADERS = ["#", "Verdict", "Score", "Priorité", "Profil", "Nom", "Entreprise", "Localisation", "Site", "Email(s)",
    "Email trouvé sur", "LinkedIn", "GitHub", "Titre du site", "Bio GitHub", "Signaux sur le site", "Décision"];
  const WIDTHS = [5, 10, 9, 17, 18, 22, 20, 20, 26, 32, 16, 16, 18, 40, 54, 26, 11];
  const CENTER = new Set([1, 2, 3, 4, 5, 11, 12, 17]);
  const C = { violet: "FF7B3FF2", ink: "FF17121F", grey: "FF8C8698", lilac: "FFF6F2FC", line: "FFE4DDEF", link: "FF2F5BD3" };
  const F = {
    head: { name: "Segoe UI", size: 11, bold: true, color: { argb: "FFFFFFFF" } },
    body: { name: "Segoe UI", size: 10.5, color: { argb: C.ink } },
    small: { name: "Segoe UI", size: 9.5, color: { argb: C.grey } },
    link: { name: "Segoe UI", size: 10.5, color: { argb: C.link }, underline: true },
  };
  const fill = (argb) => ({ type: "pattern", pattern: "solid", fgColor: { argb } });
  const THIN = { bottom: { style: "thin", color: { argb: C.line } } };
  const AL = { vertical: "middle", wrapText: true };
  const ALC = { vertical: "middle", horizontal: "center", wrapText: true };
  const PRIO_FILL = { 3: "FFFDE3E4", 2: "FFFFF1D6", 1: "FFF6F2FC", 0: "FFFFFFFF" };

  // ------------------------------------------------------------------ données accumulées pendant la course
  let D = blankData();
  function blankData() { return { t0: null, leads: {}, results: {}, flags: {}, summary: null, stats: null }; }

  function onEvent(e) {
    switch (e.type) {
      case "race_start": D = blankData(); D.t0 = e.t0 || Date.now() / 1000; break;
      case "reset": D = blankData(); break;
      case "lead": D.leads[e.id] = { id: e.id, host: e.host, username: e.username, name: e.name, title: e.title,
        query: e.query, signals: e.signals, state: e.state }; break;
      case "lead_done": { const r = e.row; (D.results[r.model] = D.results[r.model] || {})[r.lead] = r; break; }
      case "agree": D.flags[e.lead] = e.agree; break;
      case "stats": if (e.models) D.stats = e; break;
      case "race_end": D.summary = e.summary; break;
    }
  }
  const count = (d) => Object.values(d.results || {}).reduce((n, x) => n + Object.keys(x).length, 0);
  const hasData = () => count(D) > 0;

  async function serverData() {
    try {
      const r = await fetch("/api/leads/export");
      if (!r.ok) return null;
      const d = await r.json();
      if (!d || !d.results || !count(d)) return null;
      return { t0: d.t0, leads: d.leads, results: d.results, flags: d.agree_flags || {}, summary: d.summary, stats: d.stats };
    } catch (_) { return null; }
  }
  async function serverHasData() { return !!(await serverData()); }

  // ------------------------------------------------------------------ LinkedIn
  async function findLinkedIn(leads, status) {
    const items = Object.values(leads).map((l) => ({ id: l.id, username: l.username, host: l.host }));
    try {   // version à trois modèles : le serveur cherche avec le jeton GitHub (+ liens du site)
      const r = await fetch("/api/leads/linkedin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) });
      if (r.ok) { const d = await r.json(); return { found: d.found || {}, note: "réseaux déclarés sur GitHub, sinon lien trouvé sur le site" }; }
    } catch (_) { /* repli ci-dessous */ }
    // repli navigateur : API GitHub publique sans jeton (60 requêtes par heure), profils GitHub seulement
    const found = {};
    let checked = 0, limited = false;
    const queue = items.filter((x) => x.username);
    async function worker() {
      while (queue.length && !limited) {
        const it = queue.shift();
        try {
          const r = await fetch(`https://api.github.com/users/${encodeURIComponent(it.username)}/social_accounts`);
          if (r.status === 403 || r.status === 429) { limited = true; break; }
          checked++;
          if (status) status(`LinkedIn ${checked}/${items.length}…`);
          if (r.ok) {
            const a = (await r.json()).find((x) => x.provider === "linkedin" || /linkedin\.com/.test(x.url || ""));
            if (a) found[it.id] = { url: a.url, src: "profil GitHub" };
          }
        } catch (_) { /* reseau : on passe */ }
      }
    }
    await Promise.all([1, 2, 3, 4, 5, 6].map(worker));
    return { found, note: limited ? `profils GitHub seulement, limite de l'API GitHub atteinte après ${checked} profils`
      : "profils GitHub seulement (recherche depuis le navigateur)" };
  }

  // ------------------------------------------------------------------ mise en forme
  const pct = (x) => (typeof x === "number" ? Math.round(x * 100) + " %" : "–");
  const fr = (x, d = 1) => (typeof x === "number" ? x.toFixed(d).replace(".", ",") : "–");
  const clip = (t, n) => { t = String(t || "").split(/\n+/).map((x) => x.trim()).filter(Boolean).join(" · "); return t.length <= n ? t : t.slice(0, n - 1).trimEnd() + "…"; };
  const clean = (v) => (v == null || ["null", "none", ""].includes(String(v).toLowerCase()) ? null : v);
  const NAME = (m) => (window.Arena && Arena.NAMES[m]) || m;

  function title(ws, text, sub, width) {
    ws.mergeCells(1, 1, 1, width);
    ws.mergeCells(2, 1, 2, width);
    const a = ws.getCell(1, 1); a.value = text; a.font = { name: "Segoe UI", size: 18, bold: true, color: { argb: C.ink } };
    const b = ws.getCell(2, 1); b.value = sub; b.font = F.small; b.alignment = { vertical: "top", wrapText: true };
    ws.getRow(1).height = 34; ws.getRow(2).height = 26; ws.getRow(3).height = 10;
  }

  function leadRows(d, m, ids, li) {
    return ids.map((id, n) => {
      const L = d.leads[id] || {}, r = (d.results[m] || {})[id] || {};
      const st = (L.state || {}).lead || {}, site = (L.state || {}).site || {};
      const emails = r.emails || [];
      const srcs = {}; (r.new_emails || []).forEach((e) => (srcs[e.email] = SRC_FR[e.src] || e.src));
      let src = [...new Set(emails.map((e) => srcs[e]).filter(Boolean))].sort().join(", ");
      if (!src && emails.length) src = SRC_FR[r.email_src] || "";
      const sigObj = L.signals || {};
      const sig = Object.keys(sigObj).filter((k) => sigObj[k] && SIG_FR[k]).map((k) => SIG_FR[k]);
      if (sigObj.video) sig.push("vidéo déjà présente");
      const prio = typeof r.priority === "number" ? r.priority : null;
      const L_in = li[String(id)];
      return [
        n + 1,
        { kind: "verdict", v: r.verdict == null ? null : !!r.verdict },
        pct(r.fit),
        { kind: "prio", p: prio == null ? 0 : prio, text: prio == null ? "–" : PRIO_FR[prio] },
        STAGE_FR[r.stage] || r.stage || (r.error ? "erreur" : "–"),
        L.name || L.username || r.name || r.username,
        clean(st.company), clean(st.location),
        (L.host || r.host) ? { kind: "link", url: `https://${L.host || r.host}`, text: L.host || r.host } : null,
        emails.length ? { kind: "link", url: `mailto:${emails[0]}`, text: emails.join("\n") } : { kind: "muted", text: "aucun email trouvé" },
        src,
        L_in ? { kind: "link", url: L_in.url, text: "LinkedIn" + (L_in.src === "site" ? " (site)" : "") } : { kind: "muted", text: "–" },
        (L.username || r.username) ? { kind: "link", url: `https://github.com/${L.username || r.username}`, text: L.username || r.username } : null,
        clip(site.title || L.title, 110),
        clip(st.github_bio, 150),
        sig.join(", "),
        r.ms ? `${Math.round(r.ms)} ms` : "–",
      ];
    });
  }

  function table(ws, rows, start = 4) {
    HEADERS.forEach((h, j) => {
      const c = ws.getCell(start, j + 1);
      c.value = h; c.font = F.head; c.fill = fill(C.violet); c.alignment = ALC;
      ws.getColumn(j + 1).width = WIDTHS[j];
    });
    ws.getRow(start).height = 30;
    rows.forEach((rd, i) => {
      const rr = start + 1 + i;
      ws.getRow(rr).height = 54;
      const band = i % 2 ? fill(C.lilac) : null;
      rd.forEach((val, j) => {
        const col = j + 1, c = ws.getCell(rr, col);
        c.border = THIN;
        c.alignment = CENTER.has(col) ? ALC : AL;
        if (val && typeof val === "object") {
          if (val.kind === "link" && val.url) {
            c.value = { text: val.text || val.url, hyperlink: val.url }; c.font = F.link;
          } else if (val.kind === "verdict") {
            if (val.v == null) { c.value = "erreur"; c.font = F.small; }
            else {
              c.value = val.v ? "OUI" : "NON";
              c.fill = fill(val.v ? "FFDCF5E6" : "FFF1EEF5");
              c.font = { name: "Segoe UI", size: 10.5, bold: true, color: { argb: val.v ? "FF12A150" : C.grey } };
            }
            return;
          } else if (val.kind === "prio") {
            c.value = val.text; c.fill = fill(PRIO_FILL[val.p] || "FFFFFFFF");
            c.font = { name: "Segoe UI", size: 10.5, bold: val.p >= 2, color: { argb: C.ink } };
            return;
          } else { c.value = val.text || "–"; c.font = F.small; }
        } else {
          const empty = val == null || val === "";
          c.value = empty ? "–" : val; c.font = empty ? F.small : F.body;
        }
        if (band && !c.fill) c.fill = band;
      });
    });
    ws.views = [{ state: "frozen", xSplit: 2, ySplit: start, showGridLines: false }];
    ws.autoFilter = { from: { row: start, column: 1 }, to: { row: start + rows.length, column: HEADERS.length } };
    ws.pageSetup = { orientation: "landscape", paperSize: 9, fitToPage: true, fitToWidth: 1, fitToHeight: 0, printTitlesRow: `${start}:${start}` };
  }

  // ------------------------------------------------------------------ classeur
  async function build(d, li, liNote) {
    const models = ORDER.filter((m) => d.results[m] && Object.keys(d.results[m]).length);
    const when = d.t0 ? new Date(d.t0 * 1000) : new Date();
    const whenTxt = when.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
    const versus = models.map(NAME).join(models.length > 2 ? ", " : " contre ").replace(/, ([^,]*)$/, " et $1");
    const wb = new ExcelJS.Workbook();
    wb.creator = "Jev Arena";
    wb.created = new Date();

    // ---- Résumé
    const ws = wb.addWorksheet("Résumé", { views: [{ showGridLines: false }] });
    const W = models.length + 1;
    title(ws, `La course aux leads · ${versus}`, `Course du ${whenTxt} · motion design pour les applis des TPE / PME · mêmes leads, mêmes questions`, W);
    const S = (d.summary && d.summary.models) || (d.stats && d.stats.models) || {};
    const done = (m) => Object.keys(d.results[m] || {}).length;
    const oui = (m) => Object.values(d.results[m] || {}).filter((r) => r.verdict).length;
    const time = (m) => { const s = S[m] || {}; const t = s.time_s != null ? s.time_s : s.t_done; return t != null ? `${fr(t)} s` : "–"; };
    const lat = (m) => { const v = (S[m] || {}).lat_med_ms; return v == null ? "–" : v >= 1000 ? `${fr(v / 1000, 2)} s` : `${Math.round(v)} ms`; };
    const liCount = Object.keys(li).length;
    const rows = [
      ["", ...models.map(NAME)],
      ["Leads traités", ...models.map(done)],
      ["Temps pour finir", ...models.map(time)],
      ["Décisions", ...models.map((m) => (S[m] || {}).decisions ?? "–")],
      ["Latence médiane d'une décision", ...models.map(lat)],
      ["Coût réel", ...models.map((m) => `${fr((S[m] || {}).cost || 0, 4)} $`)],
      ["Emails uniques trouvés", ...models.map((m) => (S[m] || {}).email_count ?? "–")],
      ["Leads jugés « à contacter » (OUI)", ...models.map(oui)],
      ["Profils LinkedIn retrouvés (export)", liCount, ...models.slice(1).map(() => "")],
    ];
    rows.forEach((row, i) => {
      const r = 4 + i;
      ws.getRow(r).height = 30;
      row.forEach((v, j) => {
        const c = ws.getCell(r, j + 1);
        c.value = v; c.border = THIN;
        c.alignment = j ? ALC : { vertical: "middle" };
        if (i === 0) { c.font = F.head; c.fill = fill(C.violet); }
        else c.font = { name: "Segoe UI", size: 11, bold: j > 0, color: { argb: C.ink } };
      });
    });
    let r = 4 + rows.length + 1;
    const ag = (d.summary && d.summary.agreement) || (d.stats && d.stats.agree) || null;
    const agLines = [];
    if (ag && typeof ag.verdict === "number") agLines.push(`Accord Jev / Opus sur le verdict : ${pct(ag.verdict)} (${ag.n} leads)`);
    else if (ag) {
      for (const [k, v] of Object.entries(ag)) {
        if (!v || typeof v.verdict !== "number") continue;
        const lbl = k === "all3" ? "Accord des trois modèles" : `Accord ${k.split("-").map(NAME).join(" / ")}`;
        agLines.push(`${lbl} sur le verdict : ${pct(v.verdict)} (${v.n} leads)`);
      }
    }
    agLines.forEach((t) => { ws.getCell(r, 1).value = t; ws.getCell(r, 1).font = F.body; r++; });
    r++;
    const notes = [
      "Verdict : le modèle juge si le lead a besoin d'une vidéo de motion design pour son appli (OUI / NON). Score = probabilité donnée par le modèle (déclarée par Opus).",
      "Priorité : 0 à ne pas contacter · 1 faible · 2 bon prospect · 3 prospect chaud.",
      `Sources : profils GitHub (Scout), sites lus par Lightpanda / Crawl4AI. LinkedIn : ${liNote}.`,
      "Données publiées par les personnes elles-mêmes. Prospection B2B : message lié à leur activité, expéditeur identifié, désinscription possible.",
    ];
    notes.forEach((t) => {
      ws.mergeCells(r, 1, r, W);
      const c = ws.getCell(r, 1); c.value = t; c.font = F.small; c.alignment = { vertical: "top", wrapText: true };
      ws.getRow(r).height = 32; r++;
    });
    ws.getColumn(1).width = 44;
    for (let j = 2; j <= W; j++) ws.getColumn(j).width = 24;
    ws.pageSetup = { orientation: "portrait", paperSize: 9, fitToPage: true, fitToWidth: 1, fitToHeight: 0 };

    // ---- une feuille par modèle : tous ses leads, OUI d'abord, puis priorité et score
    for (const m of models) {
      const res = d.results[m];
      const ids = Object.keys(res).map(Number).sort((a, b) => {
        const A = res[a], B = res[b];
        return (!!B.verdict - !!A.verdict) || ((B.priority || 0) - (A.priority || 0)) || ((B.fit || 0) - (A.fit || 0)) || a - b;
      });
      const sh = wb.addWorksheet(`Leads ${NAME(m)}`.slice(0, 31), { properties: { tabColor: { argb: TAB[m] || C.violet } } });
      const nOui = ids.filter((i) => res[i].verdict).length;
      const nMail = ids.filter((i) => (res[i].emails || []).length).length;
      const nLi = ids.filter((i) => li[String(i)]).length;
      title(sh, `Les ${ids.length} leads vus par ${NAME(m)}`,
        `${nOui} à contacter (en haut, par priorité) · ${nMail} avec au moins un email · ${nLi} avec LinkedIn · course du ${whenTxt}`, HEADERS.length);
      table(sh, leadRows(d, m, ids, li));
    }
    const buf = await wb.xlsx.writeBuffer();
    const p2 = (x) => String(x).padStart(2, "0");
    const name = `leads_${when.getFullYear()}-${p2(when.getMonth() + 1)}-${p2(when.getDate())}_${p2(when.getHours())}h${p2(when.getMinutes())}.xlsx`;
    return { blob: new Blob([buf], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }), name };
  }

  let busy = false;
  async function download(btn) {
    if (busy) return;
    if (typeof ExcelJS === "undefined") { (window.Arena ? Arena.toast : alert)("ExcelJS n'est pas chargé"); return; }
    busy = true;
    const label = btn ? btn.textContent : "";
    const status = (t) => { if (btn) btn.textContent = t; };
    try {
      status("Préparation…");
      const d = (await serverData()) || (hasData() ? D : null);
      if (!d) { if (window.Arena) Arena.toast("Aucun lead à exporter : lance d'abord une course."); return; }
      status("Recherche LinkedIn…");
      const { found, note } = await findLinkedIn(d.leads, status);
      status("Création du fichier…");
      const { blob, name } = await build(d, found, note);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      if (window.Arena) Arena.toast(`${name} téléchargé · ${count(d)} lignes · ${Object.keys(found).length} LinkedIn`);
    } catch (e) {
      console.error(e);
      if (window.Arena) Arena.toast("Export Excel impossible : " + (e.message || e));
    } finally {
      busy = false;
      if (btn) btn.textContent = label;
    }
  }

  window.LeadsExport = { onEvent, hasData, serverHasData, download, _data: () => D, _build: build };
})();
