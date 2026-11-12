/* Carte « Transports IDF » (intégration transports_idf, servie sans ressource à déclarer).
 *
 * Sections : recherche d'itinéraire · trajets préférés · info trafic · gares.
 * Tout se replie : les trajets sont repliés (résumé sur une ligne) sauf pendant leur veille ;
 * à l'intérieur, propositions et alertes sont repliées ; les gares se replient d'un clic.
 * Passages : vert = atteignable (départ ≥ marche + marge), orange = en se pressant, gris = trop tard.
 */
const VERSION = "1.1.0b1";

const ETATS = {
  atteignable: { libelle: "atteignable", defaut: "var(--success-color, #2e7d32)" },
  presse: { libelle: "en se pressant", defaut: "var(--warning-color, #ef8f00)" },
  trop_tard: { libelle: "trop tard", defaut: "var(--disabled-text-color, #9e9e9e)" },
};

const DEFAUTS = {
  titre: "",
  afficher_recherche: true,
  afficher_trajets: "miens", // "miens", "tous", "aucun"
  afficher_info_trafic: true,
  afficher_gares: true,
  regroupement: "ligne", // ou "chronologique"
  nb_passages: 0, // 0 = valeur de l'intégration
  filtre_arrets: "tous", // ou "mes_trajets"
  afficher_trop_tard: true,
  afficher_marche: true,
  afficher_plan: true,
  couleur_atteignable: "",
  couleur_presse: "",
  couleur_trop_tard: "",
};

const GROUPES = ["RER", "Train", "Métro", "Tram"];
const ICONES = { marche: "🚶", velo: "🚲", voiture: "🚗", transports: "🚌" };
const NIVEAUX = { interrompu: 3, perturbe: 2, travaux: 1 };
// Le fond de carte reste clair en thème sombre : traits et arrêts non desservis en gris foncé, cerclés de blanc.
const GRIS_CARTE = "#37474f";
const STYLE_NON_DESSERVI = { radius: 6, color: GRIS_CARTE, weight: 2.5, fillColor: "#fff", fillOpacity: 1, dashArray: "3 2" };
const PASTILLE = { interrompu: "✕", perturbe: "!", travaux: "⚒", equipement: "♿" };
const LIBELLE_NIVEAU = { interrompu: "trafic interrompu", perturbe: "trafic perturbé", travaux: "travaux", equipement: "ascenseur ou escalier en panne" };
const GLYPHES = { RER: "RER", Train: "TRAIN", "Métro": "M", Tram: "TRAM", Bus: "BUS" };
// Pictogrammes des lieux sans ligne (suggestions de recherche).
const PICTOS = {
  address: `<svg viewBox="0 0 24 24" aria-label="adresse"><path d="M12 2a7 7 0 0 0-7 7c0 5.2 7 13 7 13s7-7.8 7-13a7 7 0 0 0-7-7zm0 9.6a2.6 2.6 0 1 1 0-5.2 2.6 2.6 0 0 1 0 5.2z"/></svg>`,
  poi: `<svg viewBox="0 0 24 24" aria-label="lieu"><path d="M12 2.6l2.8 5.8 6.4.9-4.6 4.5 1.1 6.3L12 17.1l-5.7 3 1.1-6.3-4.6-4.5 6.4-.9z"/></svg>`,
  administrative_region: `<svg viewBox="0 0 24 24" aria-label="ville"><path d="M3 21V10l5-3v3l5-3v3l3-2v13zm15 0V7h3v14zM6 13h2v2H6zm0 4h2v2H6zm4-4h2v2h-2zm0 4h2v2h-2zm4-1h2v2h-2z"/></svg>`,
  stop_area: `<svg viewBox="0 0 24 24" aria-label="arrêt"><path d="M6 2h12v11h-5v9h-2v-9H6z"/></svg>`,
};
const ATTR_OSM = '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
const ATTR_CARTO = `${ATTR_OSM} © <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a>`;
// Ondes « temps réel » (comme dans les applis de transport).
const ONDES = `<svg class="ondes" viewBox="0 0 14 14" aria-label="temps réel"><circle cx="3" cy="11" r="1.7" /><path d="M3 6.5a4.5 4.5 0 0 1 4.5 4.5" /><path d="M3 2a9 9 0 0 1 9 9" /></svg>`;

const echapper = (t) =>
  String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// « 20261106T080500 » (heure locale Navitia) → horodatage, ou null
const dateNavitia = (x) => {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?/.exec(String(x || ""));
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)).getTime() : null;
};
const heureCourte = (h) => (h ? `${Number(h.slice(0, 2))} h ${h.slice(3, 5)}` : "");
// Portion d'un tracé entre les arrêts touchés les plus éloignés (arrêts à moins de ~200 m du tracé).
const decouper = (trace, touches) => {
  if (!trace?.length || touches.length < 2) return null;
  const proche = (p) => {
    let i = 0, d = Infinity;
    trace.forEach((q, n) => {
      const dd = (q[0] - p[0]) ** 2 + ((q[1] - p[1]) * 0.66) ** 2;
      if (dd < d) { d = dd; i = n; }
    });
    return { i, d };
  };
  const idx = touches.map(proche).filter((x) => x.d < 4e-6).map((x) => x.i);
  if (idx.length < 2) return null;
  const a = Math.min(...idx), b = Math.max(...idx);
  return a === b ? null : { avant: trace.slice(0, a + 1), gris: trace.slice(a, b + 1), apres: trace.slice(b) };
};
// Messages « interrompu » d'une étape ou d'une ligne → noms d'arrêts touchés, et si toute la ligne l'est.
// Messages « interrompu » : toute = toute la ligne coupée (pointillés partout) ; coupe = arrêts entre lesquels
// la ligne est coupée (pointillés sur ce tronçon) ; noms = tous les arrêts cités (non desservis, marqués sur la carte).
// Des arrêts non desservis ne coupent pas la ligne : les trains passent sans s'arrêter.
const interruptions = (messages) => {
  const ms = (messages || []).filter((m) => m.niveau === "interrompu");
  const portee = (m) => m.portee || ((m.arrets_touches || []).length ? "troncon" : "ligne");
  const auTroncon = ms.filter((m) => portee(m) === "troncon");
  return {
    noms: new Set(ms.flatMap((m) => m.arrets_touches || [])),
    coupe: new Set(auTroncon.flatMap((m) => m.troncon_touche || m.arrets_touches || [])),
    zones: new Set(ms.flatMap((m) => m.zones_touchees || [])),
    zonesCoupe: new Set(auTroncon.flatMap((m) => m.zones_troncon || [])),
    toute: ms.some((m) => portee(m) === "ligne"),
    // Gares sautées (trains « passants ») : entre « de » et « vers », quel que soit le niveau du message.
    sauts: (messages || []).filter((m) => m.niveau !== "equipement").flatMap((m) => m.sauts || []),
  };
};
// Arrêts sautés : ceux strictement entre « de » et « vers », d'arrêt voisin en arrêt voisin → Map(indice → sens).
// arrets : [{id?, nom, lat, lon}] ; voisins : liste d'adjacence (indices).
const arretsSautes = (sauts, arrets, voisins) => {
  const sortie = new Map();
  const noms = arrets.map((a) => a.nom);
  const trouver = (b) => {
    const i = arrets.findIndex((a) => a.id && (b.zones || []).includes(a.id));
    return i >= 0 ? i : arrets.findIndex((a) => citeParNom(a.nom, new Set(b.noms || []), noms));
  };
  for (const x of sauts || []) {
    const de = trouver(x.de || {}), vers = trouver(x.vers || {});
    if (de < 0 || vers < 0 || de === vers) continue;
    const avant = new Map([[de, -1]]);
    const file = [de];
    while (file.length && !avant.has(vers)) {
      const i = file.shift();
      for (const j of voisins[i] || []) if (!avant.has(j)) { avant.set(j, i); file.push(j); }
    }
    if (!avant.has(vers)) continue;
    for (let i = avant.get(vers); i !== de && i >= 0; i = avant.get(i)) sortie.set(i, x.direction || "");
  }
  return sortie;
};
// Plan d'une ligne : arrêts voisins d'après les tracés (arrêts rencontrés le long de chaque tracé, dans l'ordre).
const voisinsParTraces = (arrets, traces) => {
  const voisins = arrets.map(() => new Set());
  const proche = (p) => {
    let meilleur = -1, d = 4e-6;
    arrets.forEach((a, i) => { const x = (a.lat - p[0]) ** 2 + (a.lon - p[1]) ** 2; if (x < d) { d = x; meilleur = i; } });
    return meilleur;
  };
  for (const t of traces || []) {
    const suite = [];
    for (const p of t) { const i = proche(p); if (i >= 0 && suite[suite.length - 1] !== i) suite.push(i); }
    for (let k = 1; k < suite.length; k++) { voisins[suite[k - 1]].add(suite[k]); voisins[suite[k]].add(suite[k - 1]); }
  }
  return voisins;
};
// Arrêt cité ? Par sa zone d'arrêt si on la connaît, sinon par son nom : exact, ou l'un prolonge l'autre
// (« Saint-Michel » dans un message, « Saint-Michel Notre-Dame » sur le plan) quand un seul arrêt correspond.
const citeParNom = (nom, noms, candidats) => {
  const n = nomArret(nom);
  if (!n) return false;
  if (noms.has(n)) return true;
  return [...noms].some((x) => {
    if (!(n.startsWith(x + " ") || x.startsWith(n + " "))) return false;
    const memes = candidats.filter((c) => { const k = nomArret(c); return k === x || k.startsWith(x + " ") || x.startsWith(k + " "); });
    return memes.length === 1;
  });
};
// Nom d'arrêt comparable (comme côté intégration) : sans la commune entre parenthèses, sans accents ni ponctuation.
const nomArret = (t) => String(t || "").replace(/\s*\([^)]*\)\s*$/, "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
const norm = (t) => String(t || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]/g, "");

function groupeDe(l) {
  if (l.groupe) return l.groupe;
  const m = norm(l.mode);
  if (m.includes("rer")) return "RER";
  if (m.includes("metro")) return "Métro";
  if (m.includes("tram")) return "Tram";
  if (m.includes("train") || m.includes("transilien")) return "Train";
  return "Bus";
}

function etat(minutes, marche, marge) {
  if (minutes >= marche + marge) return "atteignable";
  if (minutes >= marche) return "presse";
  return "trop_tard";
}

class TransportsIdfCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._ouverts = new Map(); // clé de pli → ouvert (choix de l'utilisateur)
    this._plans = new Set();
    this._blocs = new Map();
    this._enCours = new Set(); // trajets dont le calcul au départ de maintenant est en cours
    this._demandes = new Map(); // trajet → dernière demande de calcul (ms)
    this._erreurs = new Map();
    this._ligneInfo = null; // ligne dont on lit le détail dans l'info trafic
    this._rech = { texte: "", lieux: [], dest: null, depuis: null, depuisTexte: "", lieuxDepuis: [], mode: "maintenant", heure: "", date: "", resultats: null, erreur: "", calcul: false };
    this._cartes = new Map();
    this._planArret = new Map(); // arrêt → ligne dont le tracé est affiché sous l'arrêt
    this._plansLigne = new Map(); // ligne → plan (null : en cours) // clé → { el, carte, tuiles, sombre } (cartes Leaflet gardées entre deux rendus)
    this._donneesCartes = new Map(); // clé → { trace, a, b }
    try {
      this._replies = new Set(JSON.parse(localStorage.getItem("transports_idf_gares_repliees") || "[]"));
    } catch (e) {
      this._replies = new Set();
    }
    this.shadowRoot.addEventListener("click", (e) => this._clic(e));
    this.shadowRoot.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target.matches?.("[data-bascule]")) {
        e.preventDefault();
        this._clic(e);
      }
    });
  }

  static getStubConfig() {
    return {};
  }

  // Éditeur visuel : une section par partie de la carte, chaque réglage expliqué.
  // Les valeurs absentes de la configuration s'affichent avec leur valeur par défaut (celle utilisée par la carte).
  static getConfigForm() {
    const choix = (options) => ({ select: { mode: "dropdown", options } });
    // Interrupteurs « Masquer … » : éteints par défaut, comme les champs absents de la configuration.
    const oui = (name) => ({ name, selector: { boolean: {} } });
    return {
      schema: [
        { name: "titre", selector: { text: {} } },
        {
          type: "expandable", name: "", title: "Parties affichées", expanded: true, icon: "mdi:view-list",
          schema: [
            oui("masquer_recherche"),
            { name: "afficher_trajets", default: DEFAUTS.afficher_trajets, selector: choix([
              { value: "miens", label: "Les trajets de la personne connectée" }, { value: "tous", label: "Les trajets de tout le monde" }, { value: "aucun", label: "Aucun trajet" }]) },
            oui("masquer_gares"),
            oui("masquer_info_trafic"),
          ],
        },
        {
          type: "expandable", name: "", title: "Gares et arrêts", icon: "mdi:bus-stop",
          schema: [
            { name: "filtre_arrets", default: DEFAUTS.filtre_arrets, selector: choix([
              { value: "tous", label: "Tous les arrêts de l'intégration" }, { value: "mes_trajets", label: "Seulement les arrêts de mes trajets" }]) },
            { name: "entities", selector: { entity: { multiple: true, filter: { integration: "transports_idf", domain: "sensor", device_class: "timestamp" } } } },
            { name: "regroupement", default: DEFAUTS.regroupement, selector: choix([
              { value: "ligne", label: "Par ligne et direction" }, { value: "chronologique", label: "Dans l'ordre des départs" }]) },
            { name: "nb_passages", default: DEFAUTS.nb_passages, selector: { number: { min: 0, max: 30, mode: "box" } } },
            oui("masquer_trop_tard"),
            oui("masquer_marche"),
            oui("masquer_plan"),
          ],
        },
        {
          type: "expandable", name: "", title: "Couleurs des départs", icon: "mdi:palette",
          schema: [
            { name: "couleur_atteignable", selector: { text: {} } },
            { name: "couleur_presse", selector: { text: {} } },
            { name: "couleur_trop_tard", selector: { text: {} } },
          ],
        },
      ],
      computeLabel: (s) =>
        ({
          titre: "Titre de la carte",
          masquer_recherche: "Masquer la recherche d'itinéraire",
          afficher_trajets: "Trajets préférés (par défaut : ceux de la personne connectée)",
          masquer_gares: "Masquer les gares et arrêts",
          masquer_info_trafic: "Masquer l'info trafic",
          filtre_arrets: "Arrêts à afficher (par défaut : tous)",
          entities: "Ou choisir les arrêts un par un",
          regroupement: "Présentation des départs (par défaut : par ligne)",
          nb_passages: "Nombre de départs par arrêt",
          masquer_trop_tard: "Masquer les départs trop proches pour y arriver à temps",
          masquer_marche: "Masquer le temps de marche jusqu'à l'arrêt",
          masquer_plan: "Désactiver la carte au clic sur le temps de marche",
          couleur_atteignable: "Départ atteignable",
          couleur_presse: "Départ atteignable en se pressant",
          couleur_trop_tard: "Départ trop proche",
        })[s.name],
      computeHelper: (s) =>
        ({
          titre: "Facultatif.",
          afficher_trajets: "Chaque personne voit ses trajets sur son compte.",
          entities: "Facultatif. Si tu en choisis, seuls ces arrêts sont affichés (le réglage du dessus est alors ignoré).",
          nb_passages: "0 : le nombre réglé dans l'intégration.",
          couleur_atteignable: "Couleur CSS, ex. #2e7d32 ou green. Vide : vert par défaut.",
          couleur_presse: "Vide : orange par défaut.",
          couleur_trop_tard: "Vide : gris par défaut.",
        })[s.name],
    };
  }

  setConfig(config) {
    // « masquer_x » (éditeur visuel) l'emporte ; « afficher_x » reste compris (anciennes configurations).
    const c = { ...config };
    for (const x of ["recherche", "gares", "info_trafic", "trop_tard", "marche", "plan"]) {
      if (c[`masquer_${x}`] !== undefined) c[`afficher_${x}`] = !c[`masquer_${x}`];
    }
    this._config = { ...DEFAUTS, ...c };
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
    return 6;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  // ---- Données -------------------------------------------------------------
  _etats(type) {
    return Object.values(this._hass?.states || {}).filter((s) => s.attributes.type_transports_idf === type);
  }

  _mesTrajets(toujoursMiens = false) {
    const moi = this._hass?.user?.id;
    const tous = !toujoursMiens && this._config.afficher_trajets === "tous";
    return this._etats("trajet").filter((s) => {
      if (tous) return true;
      const personne = this._hass.states[s.attributes.personne];
      return !moi || !personne || personne.attributes.user_id === moi;
    });
  }

  _arrets() {
    if (this._config.entities?.length) return this._config.entities.map((id) => this._hass.states[id]).filter(Boolean);
    const tous = this._etats("arret");
    if (this._config.filtre_arrets !== "mes_trajets") return tous;
    const zones = new Set(), noms = new Set();
    for (const t of this._mesTrajets(true)) {
      for (const sec of t.attributes.sections || []) {
        if (sec.de_id) zones.add(sec.de_id);
        if (sec.de) noms.add(norm(sec.de));
      }
    }
    const miens = tous.filter((s) => zones.has(s.attributes.zone_id) || noms.has(norm(s.attributes.arret)));
    return miens.length ? miens : tous;
  }

  _maintenant(a) {
    const m = a.maintenant;
    return m?.le && Date.now() - new Date(m.le).getTime() < 15 * 60000 ? m : null;
  }

  _couleur(cle) {
    return this._config[`couleur_${cle}`] || ETATS[cle].defaut;
  }

  _ouvert(cle, defaut) {
    return this._ouverts.has(cle) ? this._ouverts.get(cle) : defaut;
  }

  // ---- Interactions --------------------------------------------------------
  _clic(e) {
    const t = e.target;
    const plan = t.closest?.("[data-plan]");
    if (plan) {
      e.stopPropagation();
      const id = plan.dataset.plan;
      this._plans.has(id) ? this._plans.delete(id) : this._plans.add(id);
      return this._rendu();
    }
    const tracer = t.closest?.("[data-ligne-plan]");
    if (tracer) {
      e.stopPropagation();
      const [arret, ligne] = tracer.dataset.lignePlan.split("|");
      this._planArret.get(arret) === ligne ? this._planArret.delete(arret) : this._planArret.set(arret, ligne);
      return this._rendu();
    }
    const partir = t.closest?.("[data-partir]");
    if (partir) return this._partir(partir.dataset.partir);
    const ligne = t.closest?.("[data-ligne-info]");
    if (ligne) {
      this._ligneInfo = this._ligneInfo === ligne.dataset.ligneInfo ? null : ligne.dataset.ligneInfo;
      return this._rendu();
    }
    const msg = t.closest?.("[data-ligne-msg]");
    if (msg) {
      // Le message ne s'affiche qu'à l'endroit du clic (résumé, proposition ou étape).
      const ctx = msg.closest("[data-ctx]")?.dataset.ctx || "";
      const meme = this._ligneMsg === msg.dataset.ligneMsg && this._msgCtx === ctx;
      this._ligneMsg = meme ? null : msg.dataset.ligneMsg;
      this._msgCtx = meme ? null : ctx;
      return this._rendu();
    }
    const lieu = t.closest?.("[data-lieu]");
    if (lieu) return this._choisirLieu(lieu.dataset.champ, Number(lieu.dataset.lieu));
    if (t.closest?.("[data-effacer]")) return this._effacerRecherche();
    const gare = t.closest?.("[data-gare]");
    if (gare) {
      const id = gare.dataset.gare;
      this._replies.has(id) ? this._replies.delete(id) : this._replies.add(id);
      try {
        localStorage.setItem("transports_idf_gares_repliees", JSON.stringify([...this._replies]));
      } catch (err) { /* stockage indisponible : le pliage reste valable jusqu'au rechargement */ }
      return this._rendu();
    }
    const b = t.closest?.("[data-bascule]");
    if (b) {
      const cle = b.dataset.bascule;
      this._ouverts.set(cle, !this._ouvert(cle, b.dataset.defaut === "1"));
      this._rendu();
    }
  }

  async _service(service, donnees) {
    const rep = await this._hass.callWS({ type: "call_service", domain: "transports_idf", service, service_data: donnees, return_response: true });
    return rep?.response || {};
  }

  async _partir(id) {
    if (this._enCours.has(id)) return;
    this._enCours.add(id);
    this._erreurs.delete(id);
    this._rendu();
    try {
      await this._hass.callService("transports_idf", "partir_maintenant", { trajet: id });
    } catch (err) {
      this._erreurs.set(id, err?.message || (err?.code ? `Calcul impossible (${err.code})` : "Calcul impossible : PRIM n'a pas répondu"));
      setTimeout(() => { this._erreurs.delete(id); this._rendu(); }, 10000);
    } finally {
      this._enCours.delete(id);
      this._rendu();
    }
  }

  // ---- Recherche d'itinéraire ---------------------------------------------
  _saisie(champ, texte) {
    const r = this._rech;
    if (champ === "dest") {
      r.texte = texte;
      r.dest = null;
    } else {
      r.depuisTexte = texte;
      r.depuis = null;
    }
    clearTimeout(this._delai);
    if (texte.trim().length < 2) {
      champ === "dest" ? (r.lieux = []) : (r.lieuxDepuis = []);
      return this._rendu();
    }
    this._delai = setTimeout(async () => {
      try {
        const rep = await this._service("rechercher_lieu", { texte });
        if ((champ === "dest" ? r.texte : r.depuisTexte) !== texte) return; // saisie dépassée
        champ === "dest" ? (r.lieux = rep.lieux || []) : (r.lieuxDepuis = rep.lieux || []);
        r.erreur = "";
      } catch (err) {
        r.erreur = err?.message || "Recherche impossible";
      }
      this._rendu();
    }, 350);
  }

  _choisirLieu(champ, n) {
    const r = this._rech;
    if (champ === "dest") {
      r.dest = r.lieux[n];
      r.texte = r.dest?.libelle || r.dest?.nom || "";
      r.lieux = [];
      this._el.dest.value = r.texte;
    } else {
      r.depuis = r.lieuxDepuis[n];
      r.depuisTexte = r.depuis?.libelle || r.depuis?.nom || "";
      r.lieuxDepuis = [];
      this._el.depuis.value = r.depuisTexte;
    }
    this._calculer();
  }

  _effacerRecherche() {
    this._rech = { ...this._rech, texte: "", lieux: [], dest: null, resultats: null, erreur: "" };
    this._el.dest.value = "";
    this._rendu();
  }

  async _calculer() {
    const r = this._rech;
    if (!r.dest) return this._rendu();
    const donnees = { arrivee: r.dest.id };
    if (r.depuis) donnees.depart = r.depuis.id;
    if (r.mode !== "maintenant" && r.heure) {
      donnees.heure = r.heure;
      donnees.type_heure = r.mode;
      if (r.date) donnees.date = r.date;
    }
    r.calcul = true;
    r.erreur = "";
    this._rendu();
    try {
      r.resultats = (await this._service("itineraire", donnees)).itineraires || [];
      r.resultats.forEach((_, n) => this._ouverts.delete(`rech|${n}`));
    } catch (err) {
      r.erreur = err?.message || "Calcul impossible";
      r.resultats = null;
    }
    r.calcul = false;
    this._rendu();
  }

  // ---- Briques visuelles ---------------------------------------------------
  _pli(cle, defaut, tete, corps, classe = "", extra = "") {
    const ouvert = this._ouvert(cle, defaut);
    return `<div class="pli ${classe}${ouvert ? " ouvert" : ""}" data-ctx="${echapper(cle)}">
      <div class="pli-tete" data-bascule="${echapper(cle)}" data-defaut="${defaut ? 1 : 0}" role="button" tabindex="0" aria-expanded="${ouvert}">
        <span class="chevron">${ouvert ? "▾" : "▸"}</span>${tete}</div>${extra}
      ${ouvert ? `<div class="pli-corps">${corps}</div>` : ""}</div>`;
  }

  _logo(l, { glyphe = false, alerte = null, cliquable = false, plan = null } = {}) {
    const groupe = groupeDe(l);
    const code = l.nom ?? l.ligne ?? l.code ?? "?";
    const fond = l.couleur || "#666";
    const texte = l.couleur_texte || "#fff";
    const g = glyphe ? `<span class="glyphe g-${norm(groupe)}">${GLYPHES[groupe] || ""}</span>` : "";
    const equipement = !alerte && (l.messages || []).some((m) => m.niveau === "equipement");
    const niveau = alerte && alerte !== "normal" ? alerte : equipement ? "equipement" : null;
    const pastille = niveau ? `<span class="pastille ${niveau}" title="${LIBELLE_NIVEAU[niveau]}">${PASTILLE[niveau]}</span>` : "";
    const ident = l.id ?? l.ligne_id;
    const attr = cliquable === "info" ? "data-ligne-info" : "data-ligne-msg";
    const choisi = cliquable && (cliquable === "info" ? this._ligneInfo : this._ligneMsg) === ident;
    const attrs = plan && ident
      ? ` data-ligne-plan="${echapper(`${plan}|${ident}`)}" role="button" tabindex="0" title="Voir le tracé de la ligne"`
      : cliquable ? ` ${attr}="${echapper(ident)}" role="button" tabindex="0" title="Voir l'info trafic"` : "";
    const planChoisi = plan && ident && this._planArret.get(plan) === ident;
    const long = String(code).length > 2 ? " long" : "";
    return `${g}<span class="logo f-${norm(groupe)}${long}${niveau ? ` a-${niveau}` : ""}${choisi || planChoisi ? " choisi" : ""}" style="--c:${echapper(fond)};--t:${echapper(texte)}"${attrs}>${echapper(code)}${pastille}</span>`;
  }

  _chaine(etapes, compacte = false) {
    // Version compacte (résumé d'un trajet replié) : seule la marche du début est gardée.
    return (etapes || [])
      .filter((e, n) => e.type === "transport" || e.type === "detour" || e.type === "pause" || (e.duree && (!compacte || n === 0)))
      .map((e) => (e.type === "detour" ? `<span class="pied">${ICONES[e.mode] || "🚶"}<sub>${echapper(e.duree)}</sub></span>`
        : e.type === "pause" ? `<span class="pied" title="${echapper(e.lieu || "")}">📍<sub>${echapper(e.duree)}</sub></span>`
        : e.type === "marche" ? `<span class="pied">🚶<sub>${echapper(e.duree)}</sub></span>` : this._logo(e, { glyphe: true, alerte: e.alerte, cliquable: this._aMessages(e) })))
      .join(`<span class="sep">›</span>`);
  }

  _duree(it) {
    if (!it?.duree) return "";
    const m = Math.round(it.duree / 60);
    return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min`;
  }

  _resumeIt(it, note = "") {
    return `<span class="duree">${this._duree(it)}</span>
      <span class="horaires">${echapper(it.depart_h || "")}${it.arrivee_h ? ` – ${echapper(it.arrivee_h)}` : ""}</span>${note}
      <span class="chaine">${this._chaine(it.etapes)}</span>`;
  }

  _aMessages(e) {
    return !!((e.messages || []).length || (e.alerte && e.alerte !== "normal"));
  }

  // Messages trafic : graves d'abord (titre + texte), puis les ascenseurs / escaliers regroupés et repliés.
  _blocMessages(msgs, cle) {
    const graves = msgs.filter((m) => m.niveau !== "equipement");
    const equip = msgs.filter((m) => m.niveau === "equipement");
    let html = graves.map((m) => `<div class="msg-titre">${echapper(m.titre)}</div>${m.message ? `<div class="message">${echapper(m.message)}</div>` : ""}`).join("");
    if (equip.length) {
      const tete = `<span class="msg-equip">♿ ${equip.length === 1 ? "1 ascenseur ou escalier en panne" : `${equip.length} ascenseurs ou escaliers en panne`}</span>`;
      html += this._pli(`equip|${cle}`, !graves.length && equip.length === 1, tete, equip.map((m) => `<div class="message">${echapper(m.message || m.titre)}</div>`).join(""), "equip");
    }
    return html;
  }

  // Bloc affiché sous un résumé ou une étape quand on a cliqué sur le logo d'une de ses lignes.
  // alt : contexte du résumé replié, pour qu'un clic dans le résumé d'un itinéraire déplié s'affiche sous l'étape.
  _messageSi(etapes, ctx, alt = null) {
    if (ctx !== this._msgCtx && (alt === null || alt !== this._msgCtx)) return "";
    const id = this._ligneMsg;
    const es = (etapes || []).filter((x) => x.type === "transport" && x.ligne_id === id && this._aMessages(x));
    if (!es.length) return "";
    const vus = new Set();
    const msgs = es.flatMap((x) => x.messages || []).filter((m) => {
      const cle = `${m.titre}|${m.message}`;
      return !vus.has(cle) && vus.add(cle);
    });
    const niveau = es.map((x) => x.alerte).find((n) => n && n !== "normal") || (msgs.length ? msgs[0].niveau : null) || "perturbe";
    const corps = msgs.length ? this._blocMessages(msgs, ctx) : `<div class="message">${LIBELLE_NIVEAU[niveau] || ""}</div>`;
    return `<div class="msg-ligne ${niveau}">${corps}</div>`;
  }

  _etapes(it, base = "") {
    const etapes = it?.etapes || [];
    if (!etapes.length) return `<div class="sous">${echapper(it?.resume || "")}</div>`;
    return `<ol class="etapes">${this._carteGlobale(etapes, base)}${etapes
      .map((e, n) => {
        if (e.type === "detour") return `<li class="marche"><span class="h"></span><span>${ICONES[e.mode] || "🚶"} ${echapper(e.duree)} min jusqu'à <b>${echapper(e.vers)}</b></span></li>`;
        if (e.type === "pause") return `<li class="pause"><span class="h">${echapper(e.reprise || "")}</span><span>📍 ${echapper(e.duree)} min à ${echapper(e.lieu)}</span></li>`;
        if (e.type === "marche") return this._marche(e, n === etapes.length - 1);
        const suivants = e.suivants?.length ? `<span class="suivants" title="Départs suivants si tu le rates">+${e.suivants.join(", +")} min</span>` : "";
        const train = [e.voie ? `voie <b>${echapper(e.voie)}</b>` : "", e.mission ? `<b class="mission">${echapper(e.mission)}</b>` : ""].filter(Boolean).join(" · ");
        const ctx = `${base}|e${n}`;
        return `<li data-ctx="${echapper(ctx)}"><span class="h">${echapper(e.depart || "")}${suivants}</span>${this._logo(e, { alerte: e.alerte, cliquable: this._aMessages(e) })}
          <span class="trajet-ligne"><span>${echapper(e.de)} → ${echapper(e.vers)}</span>
          <span class="sous">${e.direction ? `dir. ${echapper(e.direction)} · ` : ""}arr. ${echapper(e.arrivee || "?")}</span>
          ${train ? `<span class="sous train">${train}</span>` : ""}${this._rame(e.rame)}${this._messageSi([e], ctx, base || null)}</span></li>`;
      })
      .join("")}</ol>`;
  }

  // Marche : la destination n'est utile qu'à la fin (sinon c'est l'arrêt de l'étape suivante).
  // Un clic ouvre la carte du trajet à pied quand on en connaît les deux bouts.
  // « Voir le trajet sur la carte » : tout l'itinéraire, chaque ligne dans sa couleur, la marche en pointillés.
  _carteGlobale(etapes, base) {
    const segments = [];
    const correspondances = [];
    for (const e of etapes) {
      const parArrets = e.points_arret?.length > 1 ? e.points_arret.map((x) => [x[1], x[2]]) : null;
      const pts = e.trace?.length > 1 ? e.trace : parArrets || (e.de_coord && e.vers_coord ? [e.de_coord, e.vers_coord] : null);
      if (!pts) continue;
      if (e.type === "transport") {
        const { noms, coupe, toute, sauts } = interruptions(e.messages);
        const tous = (e.points_arret || []).map((x) => x[3] || x[0]);
        // Arrêts parcourus dans l'ordre : chacun voisin du suivant.
        const sautes = arretsSautes(sauts, tous.map((nom) => ({ nom })), tous.map((_, i) => [i - 1, i + 1].filter((j) => j >= 0 && j < tous.length)));
        const touches = (e.points_arret || []).filter((x) => citeParNom(x[3] || x[0], coupe, tous)).map((x) => [x[1], x[2]]);
        const nonDesservis = (e.points_arret || []).filter((x, i) => sautes.has(i) || (citeParNom(x[3] || x[0], noms, tous) && !citeParNom(x[3] || x[0], coupe, tous))).map((x) => ({ nom: x[3] || x[0], pos: [x[1], x[2]] }));
        segments.push({ points: pts, couleur: e.couleur || null, interrompu: toute, touches, nonDesservis });
        correspondances.push(pts[0]);
      } else segments.push({ points: pts, pied: true });
    }
    if (!segments.some((x) => !x.pied)) return "";
    const tous = segments.flatMap((x) => x.points);
    const cle = `globale|${base}|${tous[0].join(",")}|${tous[tous.length - 1].join(",")}`;
    const ouvert = this._ouvert(cle, false);
    let carte = "";
    if (ouvert) {
      this._donneesCartes.set(cle, { segments, a: tous[0], b: tous[tous.length - 1], points: correspondances.slice(1) });
      carte = `<li class="carte-marche carte-globale"><span class="plan"><div class="carte-l" data-carte="${echapper(cle)}"></div></span></li>`;
    }
    return `<li class="marche cliquable voir-carte" data-bascule="${echapper(cle)}" data-defaut="0" role="button" tabindex="0" aria-expanded="${ouvert}"><span class="h"></span><span class="lien-carte">${ouvert ? "Masquer la carte" : "Voir le trajet sur la carte"}</span></li>${carte}`;
  }

  _marche(e, derniere) {
    const texte = `🚶 ${echapper(e.duree)} min${derniere && e.vers ? ` jusqu'à ${echapper(e.vers)}` : ""}`;
    const a = e.de_coord || e.trace?.[0], b = e.vers_coord || e.trace?.[e.trace.length - 1];
    if (!a || !b) return `<li class="marche"><span class="h"></span><span>${texte}</span></li>`;
    const cle = `carte|${a.join(",")}|${b.join(",")}`;
    const ouvert = this._ouvert(cle, false);
    let carte = "";
    if (ouvert) {
      this._donneesCartes.set(cle, { trace: e.trace, a, b });
      carte = `<li class="carte-marche"><span class="plan"><div class="carte-l" data-carte="${echapper(cle)}"></div></span></li>`;
    }
    return `<li class="marche cliquable" data-bascule="${echapper(cle)}" data-defaut="0" role="button" tabindex="0" aria-expanded="${ouvert}"><span class="h"></span><span class="lien-carte">${texte}</span></li>${carte}`;
  }

  // ---- Cartes (Leaflet + fond CARTO, servis avec l'intégration) -------------
  static _leaflet() {
    if (!TransportsIdfCard._promesseL) {
      TransportsIdfCard._promesseL = Promise.all([
        new Promise((ok, ko) => {
          const ancien = window.L;
          const s = document.createElement("script");
          s.src = `/transports_idf/leaflet/leaflet.js?v=${VERSION}`;
          s.onload = () => {
            const L = window.L;
            if (ancien) window.L = ancien; // ne pas remplacer le Leaflet d'une autre carte
            ok(L);
          };
          s.onerror = () => {
            TransportsIdfCard._promesseL = null;
            ko(new Error("Leaflet indisponible"));
          };
          document.head.appendChild(s);
        }),
        fetch(`/transports_idf/leaflet/leaflet.css?v=${VERSION}`).then((r) => r.text()),
      ]).then(([L, css]) => ({ L, css }));
    }
    return TransportsIdfCard._promesseL;
  }

  _sombre() {
    return !!this._hass?.themes?.darkMode;
  }

  // Fond de carte : CARTO Voyager si une clé est donnée dans les réglages de l'intégration, sinon OpenStreetMap.
  // En thème sombre, le fond reste clair mais atténué (plus lisible qu'un fond sombre).
  _fond() {
    const info = Object.values(this._hass?.states || {}).find((e) => e.attributes?.type_transports_idf === "info_trafic");
    const cle = info?.attributes?.cle_carto || "";
    const sombre = this._sombre();
    if (cle) {
      return {
        url: `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=${encodeURIComponent(cle)}`,
        options: { subdomains: "abcd", maxZoom: 20, attribution: ATTR_CARTO, referrerPolicy: "strict-origin-when-cross-origin" },
        attenue: sombre,
        signature: `carto|${sombre}|${cle}`,
      };
    }
    // Home Assistant n'envoie pas de « Referer » : OpenStreetMap refuse alors les tuiles (403 « Access blocked »).
    return {
      url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      options: { maxZoom: 19, attribution: ATTR_OSM, referrerPolicy: "strict-origin-when-cross-origin" },
      attenue: sombre,
      signature: `osm|${sombre}`,
    };
  }

  _poserFond(L, c) {
    const f = this._fond();
    if (c.tuiles) c.tuiles.remove();
    c.tuiles = L.tileLayer(f.url, f.options).addTo(c.carte);
    c.signature = f.signature;
    c.el.classList.toggle("attenue", f.attenue);
  }

  // Les cartes prennent toute la largeur de la carte du tableau de bord, quel que soit le retrait de l'étape.
  _elargirCartes() {
    const hc = this.shadowRoot.querySelector("ha-card");
    if (!hc) return;
    const r = hc.getBoundingClientRect();
    const st = getComputedStyle(hc);
    const gauche = r.left + (parseFloat(st.paddingLeft) || 0);
    const largeur = r.width - (parseFloat(st.paddingLeft) || 0) - (parseFloat(st.paddingRight) || 0);
    for (const place of this.shadowRoot.querySelectorAll("[data-carte]")) {
      place.style.marginLeft = "0px";
      const decalage = place.getBoundingClientRect().left - gauche;
      if (largeur <= 0) continue;
      place.style.marginLeft = `${-decalage}px`;
      place.style.width = `${largeur}px`;
    }
    for (const c of this._cartes.values()) c.carte?.invalidateSize();
  }

  _monterCartes() {
    if (!this._observateur && window.ResizeObserver) {
      this._observateur = new ResizeObserver(() => this._elargirCartes());
      const hc = this.shadowRoot.querySelector("ha-card");
      if (hc) this._observateur.observe(hc);
    }
    const places = [...this.shadowRoot.querySelectorAll("[data-carte]")];
    const vues = new Set();
    for (const place of places) {
      const cle = place.dataset.carte;
      vues.add(cle);
      let c = this._cartes.get(cle);
      if (!c) {
        const el = document.createElement("div");
        el.className = "carte-int";
        c = { el, carte: null };
        this._cartes.set(cle, c);
        this._creerCarte(cle, c);
      }
      if (c.el.parentNode !== place) {
        place.appendChild(c.el);
        c.carte?.invalidateSize();
      }
      if (c.carte && c.L && c.signature !== this._fond().signature) this._poserFond(c.L, c);
    }
    for (const [cle, c] of this._cartes) {
      if (!vues.has(cle)) {
        c.carte?.remove();
        this._cartes.delete(cle);
      }
    }
    if (places.length) this._elargirCartes();
  }

  // Pointillés lisibles sur le fond de carte (toujours clair, même en thème sombre) : liseré blanc, traits foncés.
  _pointilles(L, c, pts, epaisseur, motif) {
    L.polyline(pts, { color: "#fff", weight: epaisseur + 4, opacity: 0.85, lineCap: "round", lineJoin: "round" }).addTo(c.carte);
    L.polyline(pts, { color: GRIS_CARTE, weight: epaisseur, opacity: 1, dashArray: motif, lineCap: "round", lineJoin: "round" }).addTo(c.carte);
  }

  // Une ligne sur la carte ; la portion interrompue (ou toute la ligne) en pointillés gris foncé.
  _tracer(L, c, points, couleur, toute, touches, epaisseur, sansLisere = false) {
    const lisere = (pts) => !sansLisere && L.polyline(pts, { color: this._sombre() ? "#000" : "#fff", weight: epaisseur + 3, opacity: 0.6 }).addTo(c.carte);
    const plein = (pts) => { lisere(pts); L.polyline(pts, { color: couleur, weight: epaisseur, opacity: 1 }).addTo(c.carte); };
    const coupe = (pts) => this._pointilles(L, c, pts, Math.max(4, epaisseur - 1), "7 7");
    if (toute) return coupe(points);
    const p = decouper(points, touches || []);
    if (!p) return plein(points);
    plein(p.avant);
    plein(p.apres);
    coupe(p.gris);
  }

  // Arrêt non desservi : rond gris en pointillés, le nom au survol.
  _arretNonDesservi(L, c, pos, nom) {
    L.circleMarker(pos, STYLE_NON_DESSERVI)
      .bindTooltip(`${echapper(nom)} · non desservi`, { direction: "top" })
      .addTo(c.carte);
  }

  async _creerCarte(cle, c) {
    let L, css;
    try {
      ({ L, css } = await TransportsIdfCard._leaflet());
    } catch (err) {
      c.el.textContent = "Carte indisponible.";
      return;
    }
    if (!this.shadowRoot.querySelector("style.leaflet")) {
      const st = document.createElement("style");
      st.className = "leaflet";
      st.textContent = css;
      this.shadowRoot.prepend(st);
    }
    if (this._cartes.get(cle) !== c) return; // carte refermée entre-temps
    const d = this._donneesCartes.get(cle) || {};
    const couleur = getComputedStyle(this).getPropertyValue("--primary-color").trim() || "#03a9f4";
    c.L = L;
    c.carte = L.map(c.el, { zoomControl: true, scrollWheelZoom: false, attributionControl: true });
    c.carte.attributionControl.setPrefix(false);
    this._poserFond(L, c);
    let points = d.trace?.length > 1 ? d.trace : [d.a, d.b].filter(Boolean);
    if (d.segments) {
      // Trajet entier : la marche en pointillés, chaque ligne dans sa couleur (liseré pour les couleurs claires).
      points = d.segments.flatMap((x) => x.points);
      for (const x of d.segments) {
        if (x.pied) this._pointilles(L, c, x.points, 5, "1 9");
        else this._tracer(L, c, x.points, x.couleur || couleur, x.interrompu, x.touches, 6);
        for (const a of x.nonDesservis || []) this._arretNonDesservi(L, c, a.pos, a.nom);
      }
      for (const p of d.points || []) L.circleMarker(p, { radius: 5, color: GRIS_CARTE, weight: 2, fillColor: "#fff", fillOpacity: 1 }).addTo(c.carte);
    } else if (d.lignes) {
      // Plan d'une ligne : son tracé, ses arrêts ; ceux cités par un message en rouge.
      points = d.lignes.flat();
      const tous = (d.arrets || []).map((a) => a.nom);
      const cite = (a, zones, noms) => zones?.has(a.id) || citeParNom(a.nom, noms || new Set(), tous);
      const touches = (d.arrets || []).filter((a) => cite(a, d.zonesCoupe, d.coupe)).map((a) => [a.lat, a.lon]);
      for (const t of d.lignes) this._tracer(L, c, t, d.couleur || couleur, d.toute, touches, 5, true);
      for (const [i, a] of (d.arrets || []).entries()) {
        const touche = d.toute || d.sautes?.has(i) || cite(a, d.zones, d.touches);
        L.circleMarker([a.lat, a.lon], touche
          ? STYLE_NON_DESSERVI
          : { radius: 3.5, color: d.couleur || couleur, weight: 2, fillColor: "#fff", fillOpacity: 1 })
          .bindTooltip(echapper(a.nom) + (touche ? " · non desservi" + (d.sautes?.get(i) ? ` direction ${echapper(d.sautes.get(i))}` : "") : ""), { direction: "top" })
          .addTo(c.carte);
        if (!d.lignes.length) points.push([a.lat, a.lon]);
      }
    } else if (d.trace?.length > 1) L.polyline(points, { color: couleur, weight: 5, opacity: 0.9 }).addTo(c.carte);
    else if (d.ligne !== false && points.length > 1) L.polyline(points, { color: couleur, weight: 3, opacity: 0.7, dashArray: "6 6" }).addTo(c.carte);
    if (d.a) L.circleMarker(d.a, { radius: 6, color: "#fff", weight: 2, fillColor: "#757575", fillOpacity: 1 }).addTo(c.carte);
    if (d.b) L.circleMarker(d.b, { radius: 8, color: "#fff", weight: 2, fillColor: couleur, fillOpacity: 1 }).addTo(c.carte);
    if (points.length > 1) c.carte.fitBounds(L.latLngBounds(points), { padding: [24, 24], maxZoom: 18 });
    else if (points.length) c.carte.setView(points[0], 17);
    requestAnimationFrame(() => c.carte?.invalidateSize());
  }

  _rame(r) {
    if (!r?.voitures) return "";
    // Rame vue de côté, dans le sens de la marche : l'arrière à gauche, la cabine de tête à droite.
    const voitures = Array.from({ length: r.voitures }, (_, i) => i + 1)
      .reverse()
      .map((v) => `<span class="voiture${v === r.voiture ? " ici" : ""}${v === r.voitures ? " queue" : ""}${v === 1 ? " tete" : ""}"><span class="vitre"></span></span>`)
      .join("");
    const suite = r.type === "sortie" ? `<span class="panneau"><span class="panneau-num">${echapper(r.numero || "?")}</span>${echapper(r.nom || r.vers)}</span>` : "";
    const equipement = r.equipement ? `<span class="sous">${echapper(r.equipement.toLowerCase())}</span>` : "";
    const ou = { avant: "l'avant", milieu: "le milieu", arriere: "l'arrière" }[r.zone] || "";
    const pour = r.type === "sortie" ? "la sortie" : `la correspondance${r.ligne_suivante ? ` (${[r.mode_suivant, r.ligne_suivante].filter(Boolean).join(" ")})` : ""}`;
    return `<span class="rame-ligne"><span class="rame" title="Monter vers ${ou} du train (voiture ${r.voiture} sur ${r.voitures}) pour ${echapper(pour)} ; sens de la marche vers la droite">${voitures}</span>${suite}${equipement}</span>`;
  }

  // ---- Sections ------------------------------------------------------------
  _htmlRecherche() {
    const r = this._rech;
    const sugg = (champ, lieux) =>
      lieux.length
        ? `<ul class="suggestions">${lieux.map((l, n) => `<li data-lieu="${n}" data-champ="${champ}" role="button" tabindex="0"><span class="ico-lieu">${this._iconeLieu(l)}</span><span class="nom-lieu">${echapper(l.libelle || l.nom)}</span></li>`).join("")}</ul>`
        : "";
    let resultats = "";
    if (r.calcul) resultats = `<div class="sous">Calcul…</div>`;
    else if (r.erreur) resultats = `<div class="erreur">${echapper(r.erreur)}</div>`;
    else if (r.resultats) {
      resultats = r.resultats.length
        ? r.resultats.map((it, n) => this._pli(`rech|${n}`, false, this._resumeIt(it), this._etapes(it, `rech|${n}`), "proposition", this._ouvert(`rech|${n}`, false) ? "" : this._messageSi(it.etapes, `rech|${n}`))).join("")
        : `<div class="sous">Aucun itinéraire trouvé.</div>`;
    }
    return { suggDest: sugg("dest", r.lieux), suggDepuis: sugg("depuis", r.lieuxDepuis), resultats };
  }

  _iconeLieu(l) {
    if (l.type === "stop_area" && l.modes?.length) return l.modes.map((g) => `<span class="glyphe g-${norm(g)}">${GLYPHES[g] || ""}</span>`).join("");
    return `<span class="picto ${echapper(l.type || "")}">${PICTOS[l.type] || PICTOS.address}</span>`;
  }

  _jour(iso) {
    if (!iso) return "";
    const j = new Date(`${iso}T12:00:00`);
    const auj = new Date();
    auj.setHours(12, 0, 0, 0);
    const n = Math.round((j - auj) / 86400000);
    return n === 0 ? "aujourd'hui" : n === 1 ? "demain" : j.toLocaleDateString("fr-FR", { weekday: "long" });
  }

  // Indicateur de rappel, recalculé à chaque rendu (toutes les 15 s).
  _rappel(a) {
    const r = a.rappel;
    if (!a.en_veille || !r) return "";
    if (r.impossible) return `<span class="statut mauvais">arrivée à ${echapper(r.heure_cible)} impossible</span>`;
    if (r.retard) return `<span class="statut mauvais">arrivée ${echapper(r.arrivee_h)} (en retard)</span>`;
    let depart = new Date(r.depart_iso).getTime();
    const cible = r.cible_iso ? new Date(r.cible_iso).getTime() : null;
    if (depart < Date.now() - 30000 && a.type_heure === "arrivee" && cible) {
      // Le départ conseillé est passé : un autre départ arrive-t-il encore à l'heure ?
      const encore = (a.direct || [])
        .map((o) => ({ dep: dateNavitia(o.depart), arr: dateNavitia(o.arrivee) }))
        .filter((o) => o.dep && o.arr && o.dep >= Date.now() - 30000 && o.arr <= cible)
        .sort((x, y) => y.dep - x.dep)[0];
      if (encore) depart = encore.dep;
    }
    const minutes = Math.floor((depart - Date.now()) / 60000);
    if (minutes >= 3) return `<span class="statut ok">pars dans ${minutes} min</span>`;
    if (minutes >= 0) return `<span class="statut attention">pars maintenant</span>`;
    const prochain = (a.direct || [])[0];
    const tard = !cible || !prochain?.arrivee || (dateNavitia(prochain.arrivee) || 0) > cible;
    if (!tard) return `<span class="statut attention">pars maintenant</span>`;
    return `<span class="statut mauvais">en retard${prochain?.arrivee_h ? ` · arrivée ${echapper(prochain.arrivee_h)}` : ""}</span>`;
  }

  _htmlTrajet(s) {
    const a = s.attributes;
    const id = s.entity_id;
    const arrivee = a.type_heure === "arrivee";
    const enVeille = !!a.en_veille;
    const direct = a.direct || [];
    const nominal = a.nominal;
    const ouvert = this._ouvert(`${id}|trajet`, enVeille);
    // Déplier un trajet déclenche le calcul au départ de maintenant (au plus une fois par minute).
    if (ouvert && !this._enCours.has(id) && (!a.direct_le || Date.now() - new Date(a.direct_le).getTime() > 60000) && !(this._demandes.get(id) > Date.now() - 60000)) {
      this._demandes.set(id, Date.now());
      setTimeout(() => this._partir(id), 0);
    }

    // Résumé replié : en veille, le meilleur départ maintenant ; sinon le nominal (prochaine occurrence).
    const resume = enVeille && direct[0] ? direct[0] : nominal;
    const etapes = resume?.etapes?.length ? resume.etapes : a.etapes_habituelles || [];
    const horaires = resume?.depart_h
      ? `<span class="horaires">${enVeille ? "" : `${this._jour(nominal?.jour)} `}${echapper(resume.depart_h)} – ${echapper(resume.arrivee_h || "")}</span>`
      : `<span class="horaires">${arrivee ? "arrivée" : "départ"} ${heureCourte(a.heure)}</span>`;
    const classe = s.state === "on" ? (a.alerte === "interrompu" ? "mauvais" : "attention") : (a.rappel?.avance || 0) >= 3 ? "attention" : "";
    const tete = `<span class="trajet-nom">${echapper(a.trajet)}</span><span class="chaine">${this._chaine(etapes, true)}</span>
      <span class="trajet-fin">${this._rappel(a)}${horaires}</span>
      <span class="trajet-depuis">depuis ${echapper(a.origine_nom || "la maison")}${a.detour?.nom ? ` · par ${echapper(a.detour.nom)}` : ""}${a.destination ? ` · vers ${echapper(a.destination)}` : ""} · ${arrivee ? "arrivée" : "départ"} ${heureCourte(a.heure)}</span>`;

    const corps = [];
    if (this._erreurs.has(id)) corps.push(`<div class="erreur">${echapper(this._erreurs.get(id))}</div>`);
    const vus = new Set();
    const prop = (it, cle, note = "", classe = "proposition") => {
      if (!it?.etapes?.length) return;
      const sig = `${it.depart}|${it.resume}`;
      if (vus.has(sig)) return;
      vus.add(sig);
      const cleP = `${id}|${cle}`;
      corps.push(this._pli(cleP, false, this._resumeIt(it, note), this._etapes(it, cleP), classe, this._ouvert(cleP, false) ? "" : this._messageSi(it.etapes, cleP)));
    };
    const ecartHtml = (g) => {
      if (g == null || g === 0) return "";
      return `<span class="ecart${g > 0 ? " mieux" : ""}">${g > 0 ? `${g} min plus rapide` : `${-g} min plus long`}</span>`;
    };
    if (direct.length) {
      direct.forEach((it, n) => {
        if (n === 1 && direct[0].prefere) corps.push(`<div class="separation" aria-hidden="true"></div>`);
        prop(it, `d${n}`, it.prefere ? "" : ecartHtml(it.gain), `proposition${it.prefere ? " prefere" : ""}`);
      });
    } else if (this._enCours.has(id)) corps.push(`<div class="sous">Calcul des départs…</div>`);
    else corps.push(`<div class="sous">Aucun itinéraire au départ de maintenant.</div>`);
    if (nominal?.etapes?.length) {
      corps.push(`<div class="separation" aria-hidden="true"></div>`);
      const cleN = `${id}|nominal`;
      const note = `<span class="ecart">habituel · ${this._jour(nominal.jour)}</span>`;
      corps.push(this._pli(cleN, false, this._resumeIt(nominal, note), this._etapes(nominal, cleN), "proposition nominal", this._ouvert(cleN, false) ? "" : this._messageSi(nominal.etapes, cleN)));
    }
    return this._pli(`${id}|trajet`, enVeille, tete, corps.join(""), `trajet ${classe}`, this._messageSi(etapes, `${id}|trajet`));
  }

  _htmlInfoTrafic() {
    const capteur = this._etats("info_trafic")[0];
    if (!capteur) return "";
    const lignes = capteur.attributes.lignes || [];
    const mesIds = new Set(this._mesTrajets(true).flatMap((t) => (t.attributes.sections || []).map((s) => s.ligne_id)));
    const rangees = GROUPES.map((g) => [g, lignes.filter((l) => l.base && l.groupe === g)]);
    rangees.push(["Mes lignes", lignes.filter((l) => !l.base && (l.interet || mesIds.has(l.id)))]);
    const glyphes = { RER: "RER", Train: "TRAIN", "Métro": "M", Tram: "TRAM", "Mes lignes": "★" };
    const grille = rangees
      .filter(([, ls]) => ls.length)
      .map(([g, ls]) => `<div class="rangee"><span class="glyphe g-${norm(g)}">${glyphes[g]}</span><div class="logos l-${norm(g)}">${ls.map((l) => this._logo(l, { alerte: l.etat, cliquable: "info" }).replace('class="logo ', String(l.nom || "").length > 3 ? 'class="logo tres-long ' : 'class="logo ')).join("")}</div></div>`)
      .join("");
    const touchees = lignes.filter((l) => (l.base || l.interet || mesIds.has(l.id)) && l.etat !== "normal").length;
    const choisie = lignes.find((l) => l.id === this._ligneInfo);
    const detail = choisie
      ? `<div class="detail-ligne"><div class="detail-tete">${this._logo(choisie, { glyphe: true, alerte: choisie.etat })}<span class="${choisie.etat === "normal" ? "statut ok" : `etiquette ${choisie.etat}`}">${choisie.etat === "normal" ? "trafic normal" : LIBELLE_NIVEAU[choisie.etat]}</span></div>
          ${this._htmlPerturbations(choisie)}${this._planLigne(choisie)}</div>`
      : "";
    const resume = touchees ? `<span class="statut attention">${touchees} ligne${touchees > 1 ? "s" : ""} touchée${touchees > 1 ? "s" : ""}</span>` : `<span class="statut ok">trafic normal</span>`;
    return this._pli("info", true, `<span class="section-titre">Info trafic</span>${resume}`, `<div class="grille">${grille}</div>${detail}`, "section info");
  }

  // « Voir le plan de la ligne » : tracé et arrêts demandés à l'intégration (gardés pour la session).
  _planLigne(ligne) {
    const cle = `ligne|${ligne.id}`;
    const ouvert = this._ouvert(cle, false);
    const corps = ouvert ? this._corpsPlanLigne(ligne, cle) : "";
    return `<div class="voir-carte cliquable lien-carte" data-bascule="${echapper(cle)}" data-defaut="0" role="button" tabindex="0" aria-expanded="${ouvert}">${ouvert ? "Masquer le plan de la ligne" : "Voir le plan de la ligne"}</div>${corps}`;
  }

  // Carte d'une ligne (tracé, arrêts, portion interrompue) ; ici : arrêt à mettre en avant.
  _corpsPlanLigne(ligne, cle, ici = null) {
    const plan = this._plansLigne.get(ligne.id);
    if (plan === undefined) {
      this._plansLigne.set(ligne.id, null);
      this._hass
        .callWS({ type: "call_service", domain: "transports_idf", service: "plan_ligne", service_data: { ligne_id: ligne.id }, return_response: true })
        .then((rep) => this._plansLigne.set(ligne.id, rep?.response || { erreur: true }))
        .catch(() => this._plansLigne.set(ligne.id, { erreur: true }))
        .finally(() => this._rendu());
    }
    if (!plan) return `<div class="sous">Chargement du plan…</div>`;
    if (plan.erreur || (!plan.traces?.length && !plan.arrets?.length)) return `<div class="sous">Plan indisponible.</div>`;
    const { noms, coupe, zones, zonesCoupe, toute, sauts } = interruptions(ligne.perturbations);
    const sautes = sauts.length ? arretsSautes(sauts, plan.arrets || [], voisinsParTraces(plan.arrets || [], plan.traces || [])) : new Map();
    this._donneesCartes.set(cle, { lignes: plan.traces || [], arrets: plan.arrets || [], couleur: ligne.couleur || plan.couleur, touches: noms, coupe, zones, zonesCoupe, toute, sautes, b: ici });
    return `<div class="plan plan-ligne"><div class="carte-l" data-carte="${echapper(cle)}"></div></div>`;
  }

  // Tracé de la ligne choisie (clic sur son logo) sous les passages d'un arrêt.
  _planDepuisArret(arret) {
    const id = this._planArret.get(arret.entity_id);
    if (!id) return "";
    const p = (arret.attributes.passages || []).find((x) => x.ligne_id === id) || {};
    const info = (this._etats("info_trafic")[0]?.attributes.lignes || []).find((l) => l.id === id) || {};
    const ligne = { id, couleur: p.couleur || info.couleur, perturbations: info.perturbations || [] };
    const a = arret.attributes;
    const ici = a.lat != null && a.lon != null ? [Number(a.lat), Number(a.lon)] : null;
    return `<div class="plan-depuis-arret">${this._corpsPlanLigne(ligne, `arret-ligne|${arret.entity_id}|${id}`, ici)}</div>`;
  }

  _htmlPerturbations(ligne) {
    const ps = ligne.perturbations || [];
    if (!ps.length) return `<div class="sous">Trafic normal.</div>`;
    const graves = ps.filter((p) => p.niveau !== "equipement");
    const equip = ps.filter((p) => p.niveau === "equipement");
    let html = graves.map((p, n) => this._pli(`info|${ligne.id}|${n}`, false,
      `<span class="etiquette ${p.niveau || "info"}">${PASTILLE[p.niveau] || "i"}</span><span class="alerte-titre">${echapper(p.titre)}</span>`,
      `<div class="message">${echapper(p.message || p.titre)}</div>`, "alerte")).join("");
    if (equip.length) {
      html += this._pli(`info|${ligne.id}|equip`, false,
        `<span class="etiquette equipement">♿</span><span class="alerte-titre">${equip.length === 1 ? "1 ascenseur ou escalier en panne" : `${equip.length} ascenseurs ou escaliers en panne`} (tes arrêts)</span>`,
        equip.map((p) => `<div class="message">${echapper(p.message || p.titre)}</div>`).join(""), "alerte");
    }
    return html;
  }

  _htmlPassages(arret) {
    const a = arret.attributes;
    const marche = Number(a.temps_marche) || 0;
    const marge = Number(a.marge) || 0;
    const n = Number(this._config.nb_passages) || Number(a.nb_passages) || 6;
    const maintenantMs = Date.now();
    const passages = (a.passages || [])
      .map((p) => {
        const minutes = (new Date(p.depart).getTime() - maintenantMs) / 60000;
        return { ...p, minutes, etat: etat(minutes, marche, marge) };
      })
      .filter((p) => p.minutes > -0.5)
      .filter((p) => this._config.afficher_trop_tard || p.etat !== "trop_tard");
    if (!passages.length) return `<div class="vide">Aucun passage annoncé</div>`;
    const nombre = (p) => {
      if (p.supprime) return `<span class="n supprime">supprimé</span>`;
      if (p.a_quai || p.minutes < 0.5) return `<span class="n ${p.etat}">à quai</span>`;
      const m = Math.floor(p.minutes);
      return `<span class="n ${p.etat}" title="${ETATS[p.etat].libelle}${p.temps_reel ? "" : " · horaire théorique"}">${m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : m}</span>`;
    };
    const temps = (ps) => {
      const reel = ps[0]?.temps_reel;
      const unite = ps.some((p) => !p.supprime && !p.a_quai && p.minutes >= 0.5 && p.minutes < 60) ? `<span class="unite">min</span>` : "";
      return `<span class="temps${reel ? "" : " theorique"}">${reel ? ONDES : ""}${ps.map(nombre).join(", ")} ${unite}</span>`;
    };
    if (this._config.regroupement === "chronologique") {
      return passages.slice(0, n).map((p) => `<div class="ligne-passage ${p.etat}">${this._logo(p, { plan: arret.entity_id })}<span class="dest">${echapper(p.destination || "")}${p.quai ? `<span class="quai">voie ${echapper(p.quai)}</span>` : ""}</span>${temps([p])}</div>`).join("");
    }
    const groupes = new Map();
    for (const p of passages) {
      const cle = `${p.ligne}|${p.destination}`;
      if (!groupes.has(cle)) groupes.set(cle, []);
      groupes.get(cle).push(p);
    }
    return [...groupes.values()]
      .map((g) => {
        const tete = g.find((p) => p.etat !== "trop_tard") || g[0];
        return `<div class="ligne-passage ${tete.etat}">${this._logo(g[0], { plan: arret.entity_id })}<span class="dest">${echapper(g[0].destination || "")}</span>${temps(g.slice(0, Math.max(1, Math.min(n, 3))))}</div>`;
      })
      .join("");
  }

  _urlPlan(a) {
    const lat = Number(a.lat), lon = Number(a.lon);
    if (!a.lat || !isFinite(lat) || !isFinite(lon)) return null;
    const dlat = Number(a.depart_lat), dlon = Number(a.depart_lon);
    const depart = a.depart_lat && isFinite(dlat) && isFinite(dlon) ? [dlat, dlon] : null;
    return {
      cle: `plan|${lat},${lon}`,
      donnees: { a: depart, b: [lat, lon], ligne: false },
    };
  }

  // ---- Rendu -------------------------------------------------------------
  _creerSquelette() {
    this.shadowRoot.innerHTML = `<style>${this._style()}</style>
      <ha-card>
        <div class="titre"></div>
        <div class="recherche">
          <div class="champ"><span class="icone">🔎</span><input class="dest" type="search" placeholder="Où vas-tu ? Adresse, arrêt, lieu…" autocomplete="off" aria-label="Destination"><button class="effacer" type="button" data-effacer hidden aria-label="Effacer">✕</button></div>
          <div class="sugg-dest"></div>
          <div class="options-recherche" hidden>
            <input class="depuis" type="search" placeholder="Depuis : la maison" autocomplete="off" aria-label="Départ">
            <select class="mode" aria-label="Quand"><option value="maintenant">Partir maintenant</option><option value="depart">Partir à</option><option value="arrivee">Arriver à</option></select>
            <input class="quand" type="datetime-local" step="60" hidden aria-label="Jour et heure">
          </div>
          <div class="sugg-depuis"></div>
          <div class="resultats"></div>
        </div>
        <div class="trajets"></div>
        <div class="titre-arrets"></div>
        <div class="arrets"></div>
        <div class="aucun"></div>
        <div class="info"></div>
      </ha-card>`;
    const q = (sel) => this.shadowRoot.querySelector(sel);
    this._el = {
      style: q("style"), titre: q(".titre"), recherche: q(".recherche"), dest: q(".dest"), depuis: q(".depuis"), mode: q(".mode"),
      quand: q(".quand"), effacer: q(".effacer"), options: q(".options-recherche"), suggDest: q(".sugg-dest"),
      suggDepuis: q(".sugg-depuis"), resultats: q(".resultats"), trajets: q(".trajets"), info: q(".info"), titreArrets: q(".titre-arrets"), arrets: q(".arrets"), aucun: q(".aucun"),
    };
    this._el.dest.addEventListener("input", (e) => this._saisie("dest", e.target.value));
    this._el.depuis.addEventListener("input", (e) => this._saisie("depuis", e.target.value));
    this._el.mode.addEventListener("change", (e) => {
      this._rech.mode = e.target.value;
      if (this._rech.mode !== "maintenant" && !this._rech.heure) {
        const d = new Date(Date.now() + (this._rech.mode === "arrivee" ? 45 : 0) * 60000);
        this._rech.heure = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
        this._rech.date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        this._el.quand.value = `${this._rech.date}T${this._rech.heure}`;
      }
      this._calculer();
    });
    this._el.quand.addEventListener("change", (e) => {
      const [jour, heure] = String(e.target.value || "").split("T");
      if (!jour || !heure) return;
      this._rech.date = jour;
      this._rech.heure = heure.slice(0, 5);
      this._calculer();
    });
    this._blocs.clear();
    for (const c of this._cartes.values()) c.carte?.remove();
    this._cartes.clear();
    this._observateur?.disconnect();
    this._observateur = null;
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
    section.innerHTML = `<div class="entete" data-gare="${echapper(id)}" role="button" tabindex="0"></div><div class="plan" hidden></div><div class="corps"></div>`;
    b = { section, entete: section.querySelector(".entete"), plan: section.querySelector(".plan"), corps: section.querySelector(".corps") };
    this._blocs.set(id, b);
    return b;
  }

  _rendu() {
    if (!this._config || !this._hass) return;
    if (!this._squelette) this._creerSquelette();
    const style = this._style();
    if (this._el.style.textContent !== style) this._el.style.textContent = style;
    const cfg = this._config;
    this._maj(this._el.titre, cfg.titre ? `<h1>${echapper(cfg.titre)}</h1>` : "");

    // Recherche
    this._el.recherche.hidden = !cfg.afficher_recherche;
    if (cfg.afficher_recherche) {
      const r = this._rech;
      const h = this._htmlRecherche();
      this._el.options.hidden = !(r.dest || r.texte || r.resultats);
      this._el.effacer.hidden = !(r.texte || r.resultats);
      this._el.quand.hidden = r.mode === "maintenant";
      this._maj(this._el.suggDest, h.suggDest);
      this._maj(this._el.suggDepuis, h.suggDepuis);
      this._maj(this._el.resultats, h.resultats);
    }

    // Trajets préférés
    const trajets = cfg.afficher_trajets === "aucun" ? [] : this._mesTrajets();
    this._maj(this._el.trajets, trajets.length ? `<div class="section-titre hors-pli">Trajets préférés</div>${trajets.map((s) => this._htmlTrajet(s)).join("")}` : "");

    // Info trafic
    this._maj(this._el.info, cfg.afficher_info_trafic ? this._htmlInfoTrafic() : "");

    // Gares
    const arrets = cfg.afficher_gares ? this._arrets() : [];
    this._maj(this._el.titreArrets, arrets.length ? `<div class="section-titre hors-pli">Arrêts préférés</div>` : "");
    const ids = arrets.map((a) => a.entity_id);
    for (const [id, b] of this._blocs) {
      if (!ids.includes(id)) {
        b.section.remove();
        this._blocs.delete(id);
      }
    }
    arrets.forEach((arret, rang) => {
      const a = arret.attributes;
      const id = arret.entity_id;
      const b = this._blocArret(id);
      if (this._el.arrets.children[rang] !== b.section) this._el.arrets.insertBefore(b.section, this._el.arrets.children[rang] || null);
      const replie = this._replies.has(id);
      const plan = cfg.afficher_plan ? this._urlPlan(a) : null;
      const planOuvert = !replie && plan && this._plans.has(id);
      // Temps de marche : un clic dessus montre l'arrêt et le chemin sur la carte.
      const marche = cfg.afficher_marche && a.temps_marche != null
        ? plan
          ? `<span class="marche lien-carte" data-plan="${echapper(id)}" role="button" tabindex="0" aria-expanded="${!!planOuvert}">🚶 ${echapper(a.temps_marche)} min</span>`
          : `<span class="marche">🚶 ${echapper(a.temps_marche)} min</span>`
        : "";
      const indispo = arret.state === "unavailable" ? `<span class="indispo">indisponible</span>` : "";
      b.section.classList.toggle("replie", replie);
      b.entete.setAttribute("aria-expanded", String(!replie));
      this._maj(b.entete, `<span class="chevron">${replie ? "▸" : "▾"}</span><span class="arret">${echapper(a.arret || a.friendly_name)}</span>${indispo}${marche}`);
      if (planOuvert) {
        this._donneesCartes.set(plan.cle, plan.donnees);
        this._maj(b.plan, `<div class="carte-l" data-carte="${echapper(plan.cle)}"></div>`);
        b.plan.hidden = false;
      } else {
        this._maj(b.plan, "");
        b.plan.hidden = true;
      }
      b.corps.hidden = replie;
      if (!replie) this._maj(b.corps, this._htmlPassages(arret) + this._planDepuisArret(arret));
    });
    this._maj(this._el.aucun, cfg.afficher_gares && !arrets.length ? `<div class="vide">Aucun arrêt : ajoute-en un depuis l'intégration Transports IDF.</div>` : "");
    this._monterCartes();
  }

  _style() {
    return `
      :host {
        --ti-atteignable: ${this._couleur("atteignable")};
        --ti-presse: ${this._couleur("presse")};
        --ti-trop-tard: ${this._couleur("trop_tard")};
        --ti-fond: var(--secondary-background-color, rgba(127,127,127,.08));
        --ti-trait: var(--divider-color, rgba(127,127,127,.18));
        --ti-rouge: var(--error-color, #db4437);
        --ti-orange: var(--warning-color, #ef8f00);
      }
      ha-card { padding: 12px; display: flex; flex-direction: column; gap: 12px; }
      .titre:empty, .trajets:empty, .info:empty, .aucun:empty, .resultats:empty, .sugg-dest:empty, .sugg-depuis:empty { display: none; }
      [hidden] { display: none !important; }
      h1 { font-size: 1.15em; font-weight: 500; margin: 0 4px; }
      .trajets, .arrets { display: flex; flex-direction: column; gap: 8px; }
      .section-titre { font-weight: 600; font-size: .8em; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); }
      .hors-pli { padding: 0 4px; }
      .sous { color: var(--secondary-text-color); font-size: .85em; }
      .erreur { color: var(--ti-rouge); font-size: .85em; }
      .vide { color: var(--secondary-text-color); font-size: .9em; padding: 4px 0; }

      /* Plis */
      .pli-tete { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; cursor: pointer; padding: 4px 0; }
      .pli-tete:focus-visible, .entete:focus-visible { outline: 2px solid var(--primary-color); border-radius: 4px; }
      .chevron { color: var(--secondary-text-color); font-size: .8em; width: .8em; flex: none; }
      .pli-corps { display: flex; flex-direction: column; gap: 2px; padding: 2px 0 4px 1.2em; }

      /* Recherche */
      .recherche { display: flex; flex-direction: column; gap: 6px; }
      .champ { display: flex; align-items: center; gap: 8px; background: var(--ti-fond); border-radius: 22px; padding: 6px 12px; }
      .champ input { flex: 1; min-width: 0; border: 0; background: transparent; font: inherit; color: var(--primary-text-color); outline: none; }
      .effacer { border: 0; background: transparent; color: var(--secondary-text-color); cursor: pointer; font-size: 1em; }
      .options-recherche { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); gap: 6px; }
      .options-recherche input, .options-recherche select {
        font: inherit; font-size: .9em; color: var(--primary-text-color); background: var(--ti-fond);
        border: 1px solid var(--ti-trait); border-radius: 16px; padding: 4px 10px; min-width: 0;
      }
      .options-recherche .depuis { grid-column: 1 / -1; }
      .options-recherche:has(.quand[hidden]) { grid-template-columns: minmax(0, 1fr); }
      .options-recherche .quand { width: 100%; box-sizing: border-box; }
      .suggestions { list-style: none; margin: 0; padding: 4px; border: 1px solid var(--ti-trait); border-radius: 10px; display: flex; flex-direction: column; }
      input[type="search"]::-webkit-search-cancel-button, input[type="search"]::-webkit-search-decoration { -webkit-appearance: none; appearance: none; display: none; }
      .suggestions li { display: flex; align-items: center; gap: 10px; padding: 6px 8px; border-radius: 6px; cursor: pointer; }
      .suggestions li:hover { background: var(--ti-fond); }
      .ico-lieu { flex: none; min-width: 2.2em; display: inline-flex; align-items: center; gap: 3px; color: var(--secondary-text-color); }
      .ico-lieu .glyphe { margin-right: 0; }
      .picto svg { width: 18px; height: 18px; fill: currentColor; display: block; }
      .nom-lieu { min-width: 0; overflow-wrap: anywhere; }
      .resultats { display: flex; flex-direction: column; }

      /* Logos de lignes */
      .logo {
        position: relative; display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box;
        min-width: 1.9em; height: 1.55em; padding: 0 5px; font-weight: 700; font-size: .85em; line-height: 1;
        background: var(--c); color: var(--t); white-space: nowrap; border-radius: 4px;
      }
      .logo.f-metro { border-radius: 50%; min-width: 1.6em; width: 1.6em; height: 1.6em; padding: 0; }
      .logo.f-metro.long { width: auto; border-radius: .8em; padding: 0 5px; font-size: .75em; }
      .logo.f-rer, .logo.f-train { border-radius: 5px; min-width: 1.6em; height: 1.6em; }
      .logo.f-tram { background: var(--ha-card-background, #fff); color: var(--primary-text-color); border-top: 3px solid var(--c); border-bottom: 3px solid var(--c); border-radius: 2px; }
      .logo.a-interrompu { outline: 2px solid var(--ti-rouge); outline-offset: 1px; }
      .logo[data-ligne-info], .logo[data-ligne-plan] { cursor: pointer; }
      .plan-depuis-arret { margin-top: 6px; }
      .logo.choisi { outline: 2px solid var(--primary-color); outline-offset: 2px; }
      .pastille {
        position: absolute; right: -6px; bottom: -6px; min-width: 13px; height: 13px; border-radius: 3px; font-size: 9px; font-weight: 800;
        display: inline-flex; align-items: center; justify-content: center; color: #fff; border: 1px solid var(--ha-card-background, #fff);
      }
      .pastille.interrompu { background: var(--ti-rouge); }
      .pastille.perturbe { background: var(--ti-orange); border-radius: 50%; }
      .pastille.travaux { background: #757575; }
      .pastille.equipement { background: #5c6bc0; font-size: 8px; }
      .glyphe { font-size: .62em; font-weight: 800; letter-spacing: .02em; color: var(--primary-text-color); border: 1.5px solid currentColor; border-radius: 4px; padding: 1px 3px; margin-right: 3px; }
      .glyphe.g-metro { border-radius: 50%; width: 1.4em; height: 1.4em; padding: 0; display: inline-flex; align-items: center; justify-content: center; }
      .glyphe.g-bus { border: 0; border-bottom: 1.5px solid currentColor; border-top: 1.5px solid currentColor; border-radius: 0; }
      .glyphe.g-meslignes { border: 0; color: var(--secondary-text-color); font-size: .9em; }
      .chaine { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 4px; }
      .sep { color: var(--secondary-text-color); font-size: .9em; }
      .pied { font-size: .95em; white-space: nowrap; }
      .pied sub { font-size: .7em; color: var(--secondary-text-color); }
      .etiquette { font-size: .75em; font-weight: 600; padding: 0 6px; border-radius: 8px; color: #fff; white-space: nowrap; }
      .etiquette.interrompu { background: var(--ti-rouge); }
      .etiquette.perturbe { background: var(--ti-orange); }
      .etiquette.travaux { background: #757575; }
      .etiquette.equipement { background: #5c6bc0; }
      .etiquette.info { background: var(--secondary-text-color); }

      /* Trajets préférés */
      .trajet { background: var(--ti-fond); border-radius: 10px; padding: 4px 12px; border-left: 4px solid transparent; }
      .trajet.attention { border-left-color: var(--ti-orange); }
      .trajet.mauvais { border-left-color: var(--ti-rouge); }
      .trajet > .pli-tete { row-gap: 2px; }
      .trajet-nom { font-weight: 600; }
      .trajet-fin { margin-left: auto; display: inline-flex; gap: 8px; align-items: baseline; }
      .arrivee { font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .trajet-depuis { flex-basis: 100%; padding-left: 1.2em; color: var(--secondary-text-color); font-size: .8em; }
      .statut { font-size: .85em; font-weight: 600; white-space: nowrap; }
      .statut.ok { color: var(--ti-atteignable); }
      .statut.attention { color: var(--ti-orange); }
      .statut.mauvais { color: var(--ti-rouge); }
      .statut.neutre { color: var(--secondary-text-color); font-weight: 400; }
      .actions { display: flex; align-items: center; gap: 8px; padding: 2px 0 4px; }
      .partir {
        font: inherit; font-size: .82em; font-weight: 600; color: var(--primary-color); cursor: pointer; white-space: nowrap;
        background: transparent; border: 1px solid var(--primary-color); border-radius: 14px; padding: 2px 10px;
      }
      .partir:disabled { opacity: .6; cursor: progress; }
      .proposition { border-top: 1px solid var(--ti-trait); }
      .proposition .pli-tete { font-size: .92em; }
      .intitule { font-weight: 600; min-width: 4.5em; }
      .proposition.mieux .intitule, .proposition.maintenant .intitule { color: var(--primary-color); }
      .duree { font-weight: 700; }
      .horaires { font-variant-numeric: tabular-nums; color: var(--secondary-text-color); font-size: .9em; }
      .alerte { border-top: 1px solid var(--ti-trait); }
      .alerte .pli-tete { font-size: .9em; }
      .alerte-titre { flex: 1; min-width: 0; }
      .message { font-size: .9em; white-space: pre-line; overflow-wrap: anywhere; }
      .etapes { list-style: none; margin: 4px 0 2px; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: .9em; }
      .etapes li { display: grid; grid-template-columns: 4.2em auto minmax(0, 1fr); align-items: start; gap: 8px; }
      .etapes li.pause { grid-template-columns: 4.2em minmax(0, 1fr); color: var(--secondary-text-color); }
      .proposition.nominal { opacity: .75; }
      .etapes li.marche { grid-template-columns: 4.2em minmax(0, 1fr); color: var(--secondary-text-color); }
      .etapes .h { font-weight: 600; font-variant-numeric: tabular-nums; display: flex; flex-direction: column; line-height: 1.25; }
      .etapes .h .suivants { font-weight: 400; font-size: .78em; color: var(--secondary-text-color); white-space: nowrap; margin: 0; }
      .trajet-ligne { display: flex; flex-direction: column; min-width: 0; overflow-wrap: anywhere; }
      .suivants { margin-right: 6px; }
      .rame-ligne { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; font-size: .85em; margin-top: 4px; }
      .rame { display: inline-flex; align-items: center; gap: 2px; }
      .voiture { position: relative; width: 16px; height: 11px; background: #8a97b5; border-radius: 2px; }
      .voiture.ici { background: #4caf50; }
      .voiture.queue { border-radius: 6px 2px 2px 6px; }
      .voiture.tete { border-radius: 2px 2px 2px 2px; clip-path: polygon(0 0, 62% 0, 100% 55%, 100% 100%, 0 100%); width: 18px; }
      .voiture .vitre { position: absolute; left: 6px; top: 2px; width: 3px; height: 4px; border-radius: 1px; background: rgba(255,255,255,.85); }
      .voiture.tete .vitre { left: 5px; }
      .panneau {
        display: inline-flex; align-items: center; gap: 6px; background: #1d2654; color: #fff; font-weight: 700;
        border-radius: 3px; padding: 2px 8px 2px 4px; font-size: .95em; letter-spacing: .01em;
      }
      .panneau-num { display: inline-flex; align-items: center; justify-content: center; min-width: 1.15em; height: 1.15em; border: 1.5px solid #fff; border-radius: 2px; font-size: .9em; padding: 0 1px; }
      .vers-corresp { display: inline-flex; align-items: center; gap: 4px; color: var(--secondary-text-color); }
      .train b { color: var(--primary-text-color); }
      .mission { font-family: monospace; letter-spacing: .08em; }
      .msg-ligne { margin: 2px 0 4px 1.2em; padding: 6px 8px; border-radius: 6px; font-size: .88em; border-left: 3px solid var(--ti-orange); background: color-mix(in srgb, var(--ti-orange) 8%, transparent); }
      .msg-ligne.interrompu { border-left-color: var(--ti-rouge); background: color-mix(in srgb, var(--ti-rouge) 8%, transparent); }
      .msg-ligne.travaux { border-left-color: #757575; background: var(--ti-fond); }
      .msg-ligne.equipement { border-left-color: #5c6bc0; background: color-mix(in srgb, #5c6bc0 8%, transparent); }
      .msg-ligne .equip { margin-top: 2px; }
      .msg-ligne .equip .pli-tete { padding: 2px 0; }
      .msg-ligne .msg-titre + .message { margin-bottom: 4px; }
      .msg-equip { font-weight: 600; font-size: .95em; }
      .etapes .msg-ligne { margin-left: 0; }
      .msg-titre { font-weight: 600; }
      .marche.cliquable { cursor: pointer; }
      .etapes li.marche > span:last-child { overflow-wrap: anywhere; min-width: 0; }
      .marche.cliquable:hover { color: var(--primary-text-color); }
      .lien-carte { text-decoration: underline dotted; text-underline-offset: 3px; text-decoration-color: color-mix(in srgb, currentColor 45%, transparent); }
      .carte-marche { display: grid; grid-template-columns: 4.2em minmax(0, 1fr); }
      .carte-l { width: 100%; }
      .carte-int { position: relative; z-index: 0; height: 190px; border-radius: 8px; overflow: hidden; background: var(--ti-fond); font-size: .8em; color: var(--secondary-text-color); }
      .carte-marche .carte-int { height: 180px; }
      .etapes li.carte-marche { grid-template-columns: minmax(0, 1fr); }
      .carte-marche .plan { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
      .carte-int.attenue .leaflet-tile-pane { filter: brightness(.82) contrast(.95) saturate(.9); }
      .plan .carte-int { height: 210px; }
      .plan-ligne .carte-int { height: 260px; }
      .voir-carte { color: var(--primary-color); font-size: .9em; cursor: pointer; margin-top: 6px; width: fit-content; }
      .etapes li.voir-carte { color: var(--primary-color); }
      .carte-globale .carte-int { height: 240px; }

      .carte-int .leaflet-control-attribution { font-size: 9px; line-height: 1.3; background: rgba(255,255,255,.6); padding: 0 4px; }
      .carte-int .leaflet-control-attribution a { color: inherit; }
      .carte-int .leaflet-bar a { width: 26px; height: 26px; line-height: 26px; }
      .separation { margin: 8px 0 4px; border-top: 2px solid var(--ti-trait); }
      .separation + .proposition { border-top: 0; }
      .proposition.prefere { border-top: 0; }
      .ecart.maintenant { color: var(--primary-color); font-weight: 600; }
      .ecart { font-weight: 400; font-size: .85em; color: var(--secondary-text-color); }
      .ecart.mieux { color: var(--ti-atteignable); font-weight: 600; }
      .intitule { min-width: 0 !important; }
      .nez { font-size: .8em; color: var(--secondary-text-color); margin-left: 1px; }

      /* Info trafic */
      .section { background: var(--ti-fond); border-radius: 10px; padding: 4px 12px; }
      .section > .pli-tete .statut { margin-left: auto; }
      /* Info trafic : un tableau. Libellés alignés à droite et centrés sur leurs logos ; mêmes colonnes pour tous les modes. */
      .grille { display: grid; grid-template-columns: max-content minmax(0, 1fr); column-gap: 12px; padding: 2px 0 6px; }
      .rangee { grid-column: 1 / -1; display: grid; grid-template-columns: subgrid; align-items: center; padding: 8px 0; }
      .rangee + .rangee { border-top: 1px solid color-mix(in srgb, var(--divider-color, rgba(127,127,127,.3)) 70%, transparent); }
      .rangee > .glyphe { justify-self: end; margin: 0; }
      .logos { display: grid; grid-template-columns: repeat(auto-fill, 2.5em); gap: 10px 2px; align-items: center; justify-items: center; }
      .logos .logo { font-size: .8em; }
      .logos .logo.f-tram { min-width: 2.75em; padding: 0 2px; }
      .logos .logo.f-bus { min-width: 2.6em; }
      /* Noms de bus longs (39-14, N145…) : texte réduit pour rester dans une case, alignés sur les autres colonnes. */
      .logos .logo.tres-long { font-size: .62em; min-width: 3.6em; padding: 0 3px; letter-spacing: -.02em; }
      .detail-tete { display: flex; align-items: center; gap: 8px; }
      .detail-ligne { border-top: 1px solid var(--ti-trait); padding-top: 6px; display: flex; flex-direction: column; gap: 2px; }

      /* Gares */
      .arret-bloc { background: var(--ti-fond); border-radius: 10px; padding: 2px 12px 4px; }
      .arret-bloc.replie { padding-bottom: 2px; }
      .entete { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: .85em; color: var(--secondary-text-color); padding: 6px 0 4px; }
      .arret { flex: 1; min-width: 0; overflow-wrap: anywhere; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--primary-text-color); }
      .entete .marche { white-space: nowrap; }
      .indispo { color: var(--ti-rouge); }
      .bouton-plan { border: 0; background: transparent; cursor: pointer; font-size: 1em; padding: 0 2px; opacity: .7; }
      .bouton-plan.actif { opacity: 1; }
      .plan { margin: 2px 0 8px; display: flex; flex-direction: column; gap: 4px; }
      .plan iframe { width: 100%; height: 200px; border: 0; border-radius: 8px; }
      .plan a { font-size: .85em; color: var(--primary-color); align-self: flex-end; }
      .ligne-passage { display: flex; align-items: center; gap: 10px; padding: 5px 0; border-top: 1px solid var(--ti-trait); }
      .corps > .ligne-passage:first-child { border-top: 0; }
      .dest { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .ligne-passage.trop_tard .dest, .ligne-passage.trop_tard .logo { opacity: .45; }
      .quai { margin-left: 6px; font-size: .8em; color: var(--secondary-text-color); }
      .temps { font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .temps .unite { font-weight: 400; font-size: .8em; color: var(--secondary-text-color); }
      .temps.theorique .n { font-weight: 500; }
      .ondes { width: 12px; height: 12px; vertical-align: -1px; fill: none; stroke: #f2b600; stroke-width: 2.2; stroke-linecap: round; margin-right: 3px; }
      .ondes circle { fill: #f2b600; stroke: none; }
      .n.atteignable { color: var(--ti-atteignable); }
      .n.presse { color: var(--ti-presse); }
      .n.trop_tard { color: var(--ti-trop-tard); font-weight: 400; }
      .n.supprime { color: var(--ti-rouge); text-decoration: line-through; font-weight: 400; }
    `;
  }
}

if (!customElements.get("transports-idf-card")) {
  customElements.define("transports-idf-card", TransportsIdfCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "transports-idf-card",
    name: "Transports IDF",
    description: "Recherche d'itinéraire, trajets préférés, info trafic et passages colorés selon le temps de marche.",
    preview: true,
  });
  console.info(`%c TRANSPORTS-IDF-CARD %c ${VERSION} `, "background:#1f6f8b;color:#fff", "");
}
