// Jev Arena : utilitaires communs aux scenes (connexion WebSocket, formats, compteurs animes).
(function () {
  const Arena = {};

  // Connexion a /ws/<scene> avec reconnexion automatique. onEvent(evt) recoit chaque evenement.
  Arena.connect = function (scene, onEvent, onStatus) {
    let ws = null, closed = false, retry = 0;
    const api = {
      send(cmd) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(cmd)); },
      close() { closed = true; ws && ws.close(); },
    };
    function open() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/${scene}`);
      ws.onopen = () => { retry = 0; onStatus && onStatus(true); };
      ws.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (e) { console.error(e); } };
      ws.onclose = () => {
        onStatus && onStatus(false);
        if (!closed) setTimeout(open, Math.min(4000, 400 * ++retry));
      };
    }
    open();
    return api;
  };

  // Montants au format francais : « 0,0123 $ », « +1 234,56 $ ».
  const fr = (x, d) => Number(x).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }).replace(/ /g, " ");
  Arena.usd = (x, d) => {   // couts d'API : precision adaptee aux tout petits montants
    if (x == null || isNaN(x)) return "–";
    const a = Math.abs(x);
    const dec = d != null ? d : a >= 100 ? 2 : a >= 1 ? 3 : a >= 0.01 ? 4 : 5;
    return (x < 0 ? "−" : "") + fr(a, dec) + " $";
  };
  Arena.money = (x) => {   // gains et pertes en dollars, signe toujours affiche
    if (x == null || isNaN(x)) return "–";
    const s = x > 0.004 ? "+" : x < -0.004 ? "−" : "";
    return s + fr(Math.abs(x), 2) + " $";
  };
  Arena.price = (x) => (x == null || isNaN(x) ? "–" : fr(x, Math.abs(x) >= 1000 ? 2 : 4) + " $");
  Arena.ms = (x) => (x == null || isNaN(x) ? "–" : x >= 1000 ? (x / 1000).toFixed(2).replace(".", ",") + " s" : Math.round(x) + " ms");
  Arena.pct = (x, d = 0) => (x == null || isNaN(x) ? "–" : (x * 100).toFixed(d).replace(".", ",") + " %");
  Arena.num = (x, d = 0) => (x == null || isNaN(x) ? "–" : Number(x).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  Arena.clock = (s) => {
    s = Math.max(0, Math.floor(s));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };

  // Met a jour un texte et fait "pulser" l'element si la valeur change.
  Arena.set = function (el, text, cls) {
    if (typeof el === "string") el = document.querySelector(el);
    if (!el) return;
    if (el.textContent !== String(text)) {
      el.textContent = text;
      el.animate([{ transform: "scale(1.06)" }, { transform: "scale(1)" }], { duration: 220, easing: "ease-out" });
    }
    if (cls !== undefined) el.className = cls;
  };

  let toastTimer = null;
  Arena.toast = function (msg) {
    let t = document.querySelector(".toast");
    if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 4500);
  };

  Arena.LOGOS = { jev: "/static/common/logos/typesafe-icon.png", laya: "/static/common/logos/laya.png", opus: "/static/common/logos/claude.png" };
  Arena.NAMES = { jev: "Jev", laya: "Laya", opus: "Claude Opus 5.5" };
  Arena.MAKERS = { jev: "TypeSafe AI · via OpenRouter", laya: "open source · sur ton GPU", opus: "Anthropic · via OpenRouter" };
  Arena.LAYA_WHERE = { local: "open source · sur ton GPU", hosted: "open source · Hugging Face (gratuit)" };
  // Laya : local (conteneur laya, GPU) ou heberge (demo gratuite Hugging Face). Met a jour les libelles de la page.
  Arena.setLaya = function (info) {
    if (!info) return;
    Arena.LAYA = info;
    Arena.MAKERS.laya = Arena.LAYA_WHERE[info.backend] || Arena.MAKERS.laya;
    document.querySelectorAll("[data-laya-maker]").forEach((el) => (el.textContent = Arena.MAKERS.laya));
    document.querySelectorAll("select[data-laya-backend]").forEach((el) => (el.value = info.backend));
  };
  Arena.switchLaya = async function (backend) {
    const r = await fetch("/api/laya/backend", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ backend }) });
    const d = await r.json();
    if (!r.ok) { Arena.toast(d.error || "impossible de changer Laya"); Arena.setLaya(Arena.LAYA); return; }
    Arena.setLaya(d);
    Arena.toast(backend === "hosted" ? "Laya passe sur Hugging Face (hébergé, gratuit)" : "Laya passe en local, sur ton GPU");
  };

  window.Arena = Arena;
})();
