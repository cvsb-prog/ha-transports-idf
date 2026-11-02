/* Carte « Transports IDF » — prochains passages colorés selon le temps de marche,
 * trajets habituels (perturbations, itinéraires détaillés, conseil de départ).
 * Servie par l'intégration transports_idf (aucune ressource à déclarer).
 * États : atteignable (départ ≥ marche + marge), en se pressant (≥ marche), trop tard.
 * Les états sont recalculés toutes les 15 s dans le navigateur entre deux mises à jour.
 */
const VERSION = "0.3.0";

const ETATS = {
  atteignable: { libelle: "atteignable", defaut: "var(--success-color, #2e7d32)" },
  presse: { libelle: "en se pressant", defaut: "var(--warning-color, #ef8f00)" },
  trop_tard: { libelle: "trop tard", defaut: "var(--disabled-text-color, #9e9e9e)" },
};

const DEFAUTS = {
  titre: "",
  regroupement: "ligne", // ou "chronologique"
  nb_passages: 0, // 0 = valeur de l'intégration
  afficher_trop_tard: true,
  afficher_trajets: "miens", // "miens", "tous", "aucun"
  afficher_marche: true,
  afficher_mode: false,
  afficher_plan: true,
  filtre_arrets: "tous", // ou "mes_trajets"
  couleur_atteignable: "",
  couleur_presse: "",
  couleur_trop_tard: "",
};

const echapper = (t) =>
  String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const heureCourte = (h) => (h ? `${Number(h.slice(0, 2))} h ${h.slice(3, 5)}` : "");

function etat(minutes, marche, marge) {
  if (minutes >= marche + marge) return "atteignable";
  if (minutes >= marche) return "presse";
  return "trop_tard";
}

function texteMinutes(p, minutes) {
  if (p.supprime) return "supprimé";
  if (p.a_quai || minutes < 0.5) return "à quai";
  if (minutes >= 60) {
    const d = new Date(p.depart);
    return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }
  return `${Math.floor(minutes)} min`;
}

class TransportsIdfCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._minuteur = null;
    this._ouverts = new Map(); // état des <details> (clé → ouvert), conservé entre deux rendus
    this._plans = new Set(); // arrêts dont le plan est affiché
    this._blocs = new Map(); // entity_id → éléments persistants de l'arrêt
    this._enCours = new Set(); // trajets dont le calcul « Partir maintenant » est en cours
    this._erreurs = new Map(); // trajet → message d'erreur affiché quelques secondes
    this.shadowRoot.addEventListener("toggle", (e) => {
      const cle = e.target?.dataset?.cle;
      if (cle) this._ouverts.set(cle, e.target.open);
    }, true);
  }

  static getStubConfig(hass) {
    const arrets = Object.keys(hass.states).filter((e) => hass.states[e].attributes.type_transports_idf === "arret");
    return { entities: arrets.slice(0, 2) };
  }

  // Éditeur visuel généré par Home Assistant à partir de ce schéma.
  static getConfigForm() {
    return {
      schema: [
        { name: "titre", selector: { text: {} } },
        { name: "entities", selector: { entity: { multiple: true, filter: { integration: "transports_idf", domain: "sensor" } } } },
        {
          type: "grid",
          name: "",
          schema: [
            {
              name: "regroupement",
              selector: { select: { mode: "dropdown", options: [
                { value: "ligne", label: "Par ligne et direction" },
                { value: "chronologique", label: "Chronologique" },
              ] } },
            },
            { name: "nb_passages", selector: { number: { min: 0, max: 30, mode: "box" } } },
            {
              name: "filtre_arrets",
              selector: { select: { mode: "dropdown", options: [
                { value: "tous", label: "Tous les arrêts" },
                { value: "mes_trajets", label: "Arrêts de mes trajets" },
              ] } },
            },
            {
              name: "afficher_trajets",
              selector: { select: { mode: "dropdown", options: [
                { value: "miens", label: "Mes trajets" },
                { value: "tous", label: "Tous les trajets" },
                { value: "aucun", label: "Aucun" },
              ] } },
            },
            { name: "afficher_trop_tard", selector: { boolean: {} } },
            { name: "afficher_marche", selector: { boolean: {} } },
            { name: "afficher_mode", selector: { boolean: {} } },
            { name: "afficher_plan", selector: { boolean: {} } },
          ],
        },
        {
          type: "expandable",
          name: "",
          title: "Couleurs",
          schema: [
            { name: "couleur_atteignable", selector: { text: {} } },
            { name: "couleur_presse", selector: { text: {} } },
            { name: "couleur_trop_tard", selector: { text: {} } },
          ],
        },
      ],
      computeLabel: (s) =>
        ({
          titre: "Titre",
          entities: "Arrêts (vide = selon le filtre)",
          filtre_arrets: "Arrêts affichés (si aucun n'est choisi)",
          regroupement: "Présentation",
          nb_passages: "Passages par arrêt (0 = réglage de l'intégration)",
          afficher_trajets: "Trajets",
          afficher_trop_tard: "Montrer les passages trop tard",
          afficher_marche: "Montrer le temps de marche",
          afficher_mode: "Écrire le mode (Bus, RER…) dans la pastille",
          afficher_plan: "Plan au clic sur un arrêt",
          couleur_atteignable: "Couleur « atteignable » (CSS)",
          couleur_presse: "Couleur « en se pressant » (CSS)",
          couleur_trop_tard: "Couleur « trop tard » (CSS)",
        })[s.name],
    };
  }

  setConfig(config) {
    this._config = { ...DEFAUTS, ...config };
    this._squelette = false;
    this._rendu();
  }

  set hass(hass) {
    this._hass = hass;
    this._rendu();
  }

  connectedCallback() {
    this._minuteur = setInterval(() => this._rendu(), 15000);
  }

  disconnectedCallback() {
    clearInterval(this._minuteur);
  }

  getCardSize() {
    return 2 + this._arrets().length * 3;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  // ---- Données -----------------------------------------------------------
  _arrets() {
    if (!this._hass) return [];
    if (this._config?.entities?.length) return this._config.entities.map((id) => this._hass.states[id]).filter(Boolean);
    const tous = Object.values(this._hass.states).filter((s) => s.attributes.type_transports_idf === "arret");
    if (this._config.filtre_arrets !== "mes_trajets") return tous;
    // Arrêts où l'on monte dans un des trajets de l'utilisateur connecté.
    const zones = new Set(), noms = new Set();
    const norm = (t) => String(t || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]/g, "");
    for (const t of this._mesTrajets(true)) {
      for (const sec of t.attributes.sections || []) {
        if (sec.de_id) zones.add(sec.de_id);
        if (sec.de) noms.add(norm(sec.de));
      }
    }
    const miens = tous.filter((s) => zones.has(s.attributes.zone_id) || noms.has(norm(s.attributes.arret)));
    return miens.length ? miens : tous;
  }

  _mesTrajets(toujoursMiens = false) {
    if (!this._hass) return [];
    const moi = this._hass.user?.id;
    const tous = !toujoursMiens && this._config.afficher_trajets === "tous";
    return Object.values(this._hass.states).filter((s) => {
      if (s.attributes.type_transports_idf !== "trajet") return false;
      if (tous) return true;
      const personne = this._hass.states[s.attributes.personne];
      return !moi || !personne || personne.attributes.user_id === moi;
    });
  }

  _trajets() {
    if (!this._hass || this._config.afficher_trajets === "aucun") return [];
    return this._mesTrajets();
  }

  _maintenant(a) {
    const m = a.maintenant;
    if (!m?.le) return null;
    return Date.now() - new Date(m.le).getTime() < 15 * 60000 ? m : null;
  }

  async _partir(id) {
    if (this._enCours.has(id)) return;
    this._enCours.add(id);
    this._erreurs.delete(id);
    this._rendu();
    try {
      await this._hass.callService("transports_idf", "partir_maintenant", { trajet: id });
      this._ouverts.set(`${id}|maintenant0`, true);
    } catch (err) {
      this._erreurs.set(id, err?.message || "Calcul impossible");
      setTimeout(() => { this._erreurs.delete(id); this._rendu(); }, 10000);
    } finally {
      this._enCours.delete(id);
      this._rendu();
    }
  }

  _couleur(cle) {
    return this._config[`couleur_${cle}`] || ETATS[cle].defaut;
  }

  _passages(arret) {
    const a = arret.attributes;
    const marche = Number(a.temps_marche) || 0;
    const marge = Number(a.marge) || 0;
    const maintenant = Date.now();
    return (a.passages || [])
      .map((p) => {
        const minutes = (new Date(p.depart).getTime() - maintenant) / 60000;
        return { ...p, minutes, etat: etat(minutes, marche, marge) };
      })
      .filter((p) => p.minutes > -0.5)
      .filter((p) => this._config.afficher_trop_tard || p.etat !== "trop_tard");
  }

  // ---- Morceaux de HTML ----------------------------------------------------
  _badge(p) {
    const fond = p.couleur || "var(--primary-color)";
    const texte = p.couleur_texte || "#fff";
    const nom = p.ligne ?? p.nom;
    const mode = this._config.afficher_mode && p.mode ? `<span class="mode">${echapper(p.mode)}</span>` : "";
    return `<span class="badge" style="background:${echapper(fond)};color:${echapper(texte)}">${mode}${echapper(nom)}</span>`;
  }

  _temps(p) {
    const theorique = p.temps_reel ? "" : `<span class="theo" title="Horaire théorique">≈</span>`;
    return `<span class="temps ${p.etat}${p.supprime ? " supprime" : ""}" title="${ETATS[p.etat].libelle}">${theorique}${echapper(texteMinutes(p, p.minutes))}</span>`;
  }

  _chronologique(passages, n) {
    return passages
      .slice(0, n)
      .map((p) => `<div class="ligne-passage ${p.etat}">
          ${this._badge(p)}
          <span class="dest">${echapper(p.destination || "")}${p.quai ? `<span class="quai">voie ${echapper(p.quai)}</span>` : ""}</span>
          ${this._temps(p)}
        </div>`)
      .join("");
  }

  _parLigne(passages, n) {
    const groupes = new Map();
    for (const p of passages) {
      const cle = `${p.ligne}|${p.destination}`;
      if (!groupes.has(cle)) groupes.set(cle, []);
      groupes.get(cle).push(p);
    }
    return [...groupes.values()]
      .map((g) => {
        const tete = g.find((p) => p.etat !== "trop_tard") || g[0];
        return `<div class="ligne-passage ${tete.etat}">
          ${this._badge(g[0])}
          <span class="dest">${echapper(g[0].destination || "")}</span>
          <span class="puces">${g.slice(0, Math.max(1, Math.min(n, 3))).map((p) => this._temps(p)).join("")}</span>
        </div>`;
      })
      .join("");
  }

  _details(cle, ouvertParDefaut, resume, corps, classe = "") {
    const ouvert = this._ouverts.has(cle) ? this._ouverts.get(cle) : ouvertParDefaut;
    return `<details class="${classe}" data-cle="${echapper(cle)}"${ouvert ? " open" : ""}><summary>${resume}</summary>${corps}</details>`;
  }

  _etapes(it) {
    const etapes = it?.etapes || [];
    if (!etapes.length) return "";
    return `<ol class="etapes">${etapes
      .map((e) =>
        e.type === "marche"
          ? `<li class="marche"><span class="h"></span><span>🚶 ${echapper(e.duree)} min${e.vers ? ` jusqu'à ${echapper(e.vers)}` : ""}</span></li>`
          : `<li><span class="h">${echapper(e.depart || "")}</span>${this._badge(e)}<span class="trajet-ligne">${echapper(e.de)} → ${echapper(e.vers)}<span class="sous">${e.direction ? `dir. ${echapper(e.direction)} · ` : ""}arr. ${echapper(e.arrivee || "?")}</span></span></li>`
      )
      .join("")}</ol>`;
  }

  _horaires(it) {
    return it?.depart_h ? `${echapper(it.depart_h)} → ${echapper(it.arrivee_h)}` : "";
  }

  _lignesIt(it) {
    return (it?.etapes || []).filter((e) => e.type === "transport").map((e) => this._badge(e)).join("");
  }

  _blocTrajet(s) {
    const a = s.attributes;
    const id = s.entity_id;
    const arrivee = a.type_heure === "arrivee";
    const c = a.conseil;
    const repere = `${arrivee ? "arrivée" : "départ"} ${heureCourte(a.heure || a.heure_depart)}`;

    let statut = `<span class="statut ok">à l'heure</span>`;
    if (c?.impossible) statut = `<span class="statut mauvais">arrivée à l'heure impossible</span>`;
    else if (arrivee && c?.depart_h) {
      const tot = (c.avance || 0) >= 3;
      statut = `<span class="statut ${tot ? "attention" : "ok"}">pars à ${echapper(c.depart_h)} au plus tard${tot ? ` (${c.avance} min plus tôt)` : ""}</span>`;
    } else if (s.state === "on") statut = `<span class="statut mauvais">perturbé</span>`;

    const morceaux = [];
    const vus = new Set();
    const maintenant = this._maintenant(a);
    if (maintenant) {
      const its = maintenant.itineraires || [];
      if (!its.length) morceaux.push(`<div class="sous">Aucun itinéraire trouvé au départ de maintenant.</div>`);
      its.forEach((it, n) => {
        vus.add(`${it.depart}|${it.resume}`);
        morceaux.push(this._details(`${id}|maintenant${n}`, n === 0,
          `<span class="intitule">${n === 0 ? "Maintenant" : "Ou"}${it.habituel ? " (habituel)" : ""}</span><span class="horaires">${this._horaires(it)}</span><span class="lignes-it">${this._lignesIt(it)}</span>`,
          this._etapes(it), "maintenant"));
      });
    }
    for (const p of a.perturbations || []) {
      morceaux.push(this._details(`${id}|p|${p.id}`, false,
        `<span class="alerte-titre">⚠️ ${echapper((p.lignes_trajet || []).join(", "))} — ${echapper(p.titre)}</span>`,
        `<div class="message">${echapper(p.message || p.titre)}${p.fin ? `<div class="sous">Jusqu'au ${echapper(p.fin.slice(6, 8))}/${echapper(p.fin.slice(4, 6))} à ${Number(p.fin.slice(9, 11))} h ${echapper(p.fin.slice(11, 13))}</div>` : ""}</div>`,
        "perturbation"));
    }
    const ajouter = (it, cle, ouvert, intitule) => {
      if (!it?.etapes?.length) return;
      const sig = `${it.depart}|${it.resume}`;
      if (vus.has(sig)) return;
      vus.add(sig);
      morceaux.push(this._details(`${id}|${cle}`, ouvert,
        `<span class="intitule">${intitule}</span><span class="horaires">${this._horaires(it)}</span><span class="lignes-it">${this._lignesIt(it)}</span>`,
        this._etapes(it)));
    };
    if (arrivee && c && !c.impossible) ajouter(c, "conseil", (c.avance || 0) >= 3 || s.state === "on", c.habituel ? "À prendre (habituel)" : "À prendre");
    if (a.rechange?.etapes?.length) ajouter(a.rechange, "rechange", true, "Rechange");
    else if (a.rechange?.resume) morceaux.push(`<div class="sous">${echapper(a.rechange.resume)}</div>`);
    ajouter(a.reference, "reference", false, "Habituel");
    for (const [n, it] of (a.plus_rapides || []).entries()) {
      ajouter(it, `rapide${n}`, false, arrivee ? `Part ${it.gain} min plus tard` : `Plus rapide (−${it.gain} min)`);
    }
    const actif = s.state === "on" || a.en_veille || maintenant;
    let classe = s.state === "on" || c?.impossible ? "trajet mauvais" : (c?.avance || 0) >= 3 ? "trajet attention" : "trajet";
    if (!actif) {
      classe += " compact";
      statut = "";
    }
    const bouton = `<button class="partir" type="button" data-partir="${echapper(id)}"${this._enCours.has(id) ? " disabled" : ""}>${this._enCours.has(id) ? "Calcul…" : "🚶 Partir maintenant"}</button>`;
    const erreur = this._erreurs.has(id) ? `<div class="erreur">${echapper(this._erreurs.get(id))}</div>` : "";
    return `<div class="${classe}"><div class="trajet-tete"><b>${echapper(a.trajet)}</b><span class="sous">${echapper(repere)}</span>${statut}${bouton}</div>${erreur}${actif ? morceaux.join("") : ""}</div>`;
  }

  _urlPlan(a) {
    const lat = Number(a.lat), lon = Number(a.lon);
    if (!isFinite(lat) || !isFinite(lon) || !a.lat) return null;
    const dlat = Number(a.depart_lat), dlon = Number(a.depart_lon);
    const pts = isFinite(dlat) && isFinite(dlon) && a.depart_lat ? [[lat, lon], [dlat, dlon]] : [[lat, lon]];
    const marge = 0.0025;
    const s = Math.min(...pts.map((p) => p[0])) - marge, n = Math.max(...pts.map((p) => p[0])) + marge;
    const o = Math.min(...pts.map((p) => p[1])) - marge * 1.5, e = Math.max(...pts.map((p) => p[1])) + marge * 1.5;
    const carte = `https://www.openstreetmap.org/export/embed.html?bbox=${o},${s},${e},${n}&layer=mapnik&marker=${lat},${lon}`;
    const pied = pts.length > 1
      ? `https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=${dlat}%2C${dlon}%3B${lat}%2C${lon}`
      : `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=17/${lat}/${lon}`;
    return { carte, pied };
  }

  // ---- Rendu -------------------------------------------------------------
  _creerSquelette() {
    this.shadowRoot.innerHTML = `<style>${this._style()}</style>
      <ha-card><div class="titre"></div><div class="trajets"></div><div class="arrets"></div><div class="aucun"></div></ha-card>`;
    this._el = {
      titre: this.shadowRoot.querySelector(".titre"),
      trajets: this.shadowRoot.querySelector(".trajets"),
      arrets: this.shadowRoot.querySelector(".arrets"),
      aucun: this.shadowRoot.querySelector(".aucun"),
      style: this.shadowRoot.querySelector("style"),
    };
    this._el.trajets.addEventListener("click", (e) => {
      const b = e.target.closest?.("[data-partir]");
      if (b) this._partir(b.dataset.partir);
    });
    this._blocs.clear();
    this._squelette = true;
  }

  _maj(el, html) {
    if (el._html !== html) {
      el._html = html;
      el.innerHTML = html;
    }
  }

  _blocArret(id) {
    let b = this._blocs.get(id);
    if (b) return b;
    const section = document.createElement("section");
    section.className = "arret-bloc";
    section.innerHTML = `<button class="entete" type="button"></button><div class="plan" hidden></div><div class="corps"></div>`;
    b = { section, entete: section.querySelector(".entete"), plan: section.querySelector(".plan"), corps: section.querySelector(".corps") };
    b.entete.addEventListener("click", () => {
      if (!this._config.afficher_plan) return;
      if (this._plans.has(id)) this._plans.delete(id);
      else this._plans.add(id);
      this._rendu();
    });
    this._blocs.set(id, b);
    return b;
  }

  _rendu() {
    if (!this._config || !this._hass) return;
    if (!this._squelette) this._creerSquelette();
    const style = this._style();
    if (this._el.style.textContent !== style) this._el.style.textContent = style;

    this._maj(this._el.titre, this._config.titre ? `<h1>${echapper(this._config.titre)}</h1>` : "");
    this._maj(this._el.trajets, this._trajets().map((s) => this._blocTrajet(s)).join(""));

    const arrets = this._arrets();
    const ids = arrets.map((a) => a.entity_id);
    for (const [id, b] of this._blocs) {
      if (!ids.includes(id)) {
        b.section.remove();
        this._blocs.delete(id);
      }
    }
    arrets.forEach((arret, rang) => {
      const a = arret.attributes;
      const b = this._blocArret(arret.entity_id);
      if (this._el.arrets.children[rang] !== b.section) this._el.arrets.insertBefore(b.section, this._el.arrets.children[rang] || null);
      const plan = this._config.afficher_plan ? this._urlPlan(a) : null;
      const ouvert = plan && this._plans.has(arret.entity_id);
      const marche = this._config.afficher_marche && a.temps_marche != null ? `<span class="marche">🚶 ${echapper(a.temps_marche)} min</span>` : "";
      const indispo = arret.state === "unavailable" ? `<span class="indispo">données indisponibles</span>` : "";
      const chevron = plan ? `<span class="chevron">${ouvert ? "▴" : "🗺️"}</span>` : "";
      this._maj(b.entete, `<span class="arret">${echapper(a.arret || a.friendly_name)}</span>${indispo}${marche}${chevron}`);
      b.entete.disabled = !plan;

      if (ouvert) {
        if (b.plan._url !== plan.carte) {
          b.plan._url = plan.carte;
          b.plan.innerHTML = `<iframe loading="lazy" referrerpolicy="no-referrer" title="Plan" src="${echapper(plan.carte)}"></iframe>
            <a href="${echapper(plan.pied)}" target="_blank" rel="noopener">Itinéraire à pied ↗</a>`;
        }
        b.plan.hidden = false;
      } else b.plan.hidden = true;

      const n = Number(this._config.nb_passages) || Number(a.nb_passages) || 6;
      const passages = this._passages(arret);
      this._maj(b.corps, passages.length
        ? this._config.regroupement === "chronologique" ? this._chronologique(passages, n) : this._parLigne(passages, n)
        : `<div class="vide">Aucun passage annoncé</div>`);
    });
    this._maj(this._el.aucun, arrets.length ? "" : `<div class="vide">Aucun arrêt : ajoute-en un depuis l'intégration Transports IDF.</div>`);
  }

  _style() {
    return `
      :host {
        --ti-atteignable: ${this._couleur("atteignable")};
        --ti-presse: ${this._couleur("presse")};
        --ti-trop-tard: ${this._couleur("trop_tard")};
        --ti-fond: var(--secondary-background-color, rgba(127,127,127,.08));
      }
      ha-card { padding: 12px; display: flex; flex-direction: column; gap: 10px; }
      .titre:empty, .trajets:empty, .aucun:empty { display: none; }
      h1 { font-size: 1.15em; font-weight: 500; margin: 0 4px; }
      .trajets, .arrets { display: flex; flex-direction: column; gap: 10px; }
      .arret-bloc { background: var(--ti-fond); border-radius: 10px; padding: 4px 12px 6px; }
      .entete {
        all: unset; box-sizing: border-box; width: 100%; display: flex; align-items: baseline; gap: 8px; cursor: pointer;
        font-size: .85em; color: var(--secondary-text-color); padding: 6px 0 4px;
      }
      .entete:disabled { cursor: default; }
      .entete:focus-visible { outline: 2px solid var(--primary-color); border-radius: 4px; }
      .arret { flex: 1; min-width: 0; overflow-wrap: anywhere; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--primary-text-color); }
      .marche, .chevron { white-space: nowrap; }
      .indispo { color: var(--error-color); }
      .plan { margin: 2px 0 8px; display: flex; flex-direction: column; gap: 4px; }
      .plan iframe { width: 100%; height: 200px; border: 0; border-radius: 8px; }
      .plan a { font-size: .85em; color: var(--primary-color); align-self: flex-end; }
      .ligne-passage { display: flex; align-items: center; gap: 10px; padding: 5px 0; border-top: 1px solid var(--divider-color, rgba(127,127,127,.15)); }
      .corps > .ligne-passage:first-child { border-top: 0; }
      .badge {
        display: inline-flex; align-items: baseline; gap: 4px; min-width: 2.4em; justify-content: center; box-sizing: border-box;
        font-weight: 700; font-size: .85em; padding: 1px 6px; border-radius: 4px; white-space: nowrap;
      }
      .mode { font-weight: 400; font-size: .8em; opacity: .85; }
      .dest { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .ligne-passage.trop_tard .dest, .ligne-passage.trop_tard .badge { opacity: .45; }
      .quai { margin-left: 6px; font-size: .8em; color: var(--secondary-text-color); }
      .puces { display: flex; gap: 10px; }
      .temps { font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .temps.atteignable { color: var(--ti-atteignable); }
      .temps.presse { color: var(--ti-presse); }
      .temps.trop_tard { color: var(--ti-trop-tard); font-weight: 400; }
      .temps.supprime { color: var(--error-color); text-decoration: line-through; font-weight: 400; }
      .theo { font-weight: 400; opacity: .7; }
      .vide { color: var(--secondary-text-color); font-size: .9em; padding: 4px 0; }

      .trajet { border-radius: 10px; padding: 8px 12px; background: var(--ti-fond); border-left: 4px solid var(--ti-atteignable); display: flex; flex-direction: column; gap: 4px; }
      .trajet.attention { border-left-color: var(--ti-presse); }
      .trajet.mauvais { border-left-color: var(--error-color); }
      .trajet-tete { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; }
      .statut { margin-left: auto; font-size: .85em; font-weight: 600; }
      .trajet.compact { border-left-color: var(--divider-color, rgba(127,127,127,.3)); padding: 6px 12px; }
      .trajet.compact .partir { margin-left: auto; }
      .partir {
        font: inherit; font-size: .82em; font-weight: 600; color: var(--primary-color); cursor: pointer; white-space: nowrap;
        background: transparent; border: 1px solid var(--primary-color); border-radius: 14px; padding: 2px 10px;
      }
      .partir:disabled { opacity: .6; cursor: progress; }
      .erreur { color: var(--error-color); font-size: .85em; }
      .maintenant summary .intitule { color: var(--primary-color); }
      .statut.ok { color: var(--ti-atteignable); }
      .statut.attention { color: var(--ti-presse); }
      .statut.mauvais { color: var(--error-color); }
      .sous { color: var(--secondary-text-color); font-size: .85em; }
      details { border-top: 1px solid var(--divider-color, rgba(127,127,127,.15)); padding: 4px 0 2px; }
      summary { cursor: pointer; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; list-style: none; font-size: .92em; }
      summary::-webkit-details-marker { display: none; }
      summary::before { content: "▸"; color: var(--secondary-text-color); font-size: .8em; }
      details[open] > summary::before { content: "▾"; }
      .intitule { font-weight: 600; }
      .horaires { font-variant-numeric: tabular-nums; }
      .lignes-it { display: inline-flex; gap: 4px; }
      .lignes-it .badge { font-size: .75em; min-width: 2em; }
      .perturbation summary { color: var(--error-color); }
      .alerte-titre { flex: 1; min-width: 0; }
      .message { font-size: .9em; padding: 4px 0 4px 14px; white-space: pre-line; overflow-wrap: anywhere; }
      .etapes { list-style: none; margin: 4px 0 2px; padding: 0 0 0 14px; display: flex; flex-direction: column; gap: 4px; font-size: .9em; }
      .etapes li { display: grid; grid-template-columns: 3.6em auto 1fr; align-items: baseline; gap: 8px; }
      .etapes li.marche { grid-template-columns: 3.6em 1fr; color: var(--secondary-text-color); }
      .etapes .h { font-weight: 600; font-variant-numeric: tabular-nums; }
      .trajet-ligne { display: flex; flex-direction: column; min-width: 0; overflow-wrap: anywhere; }
    `;
  }
}

if (!customElements.get("transports-idf-card")) {
  customElements.define("transports-idf-card", TransportsIdfCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "transports-idf-card",
    name: "Transports IDF",
    description: "Prochains passages colorés selon le temps de marche, trajets habituels et alertes.",
    preview: true,
  });
  console.info(`%c TRANSPORTS-IDF-CARD %c ${VERSION} `, "background:#1f6f8b;color:#fff", "");
}
