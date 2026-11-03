/* Carte « Transports IDF » (intégration transports_idf, servie sans ressource à déclarer).
 *
 * Sections : recherche d'itinéraire · trajets préférés · info trafic · gares.
 * Tout se replie : les trajets sont repliés (résumé sur une ligne) sauf pendant leur veille ;
 * à l'intérieur, propositions et alertes sont repliées ; les gares se replient d'un clic.
 * Passages : vert = atteignable (départ ≥ marche + marge), orange = en se pressant, gris = trop tard.
 */
const VERSION = "0.6.0";

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
const NIVEAUX = { interrompu: 3, perturbe: 2, travaux: 1 };
const PASTILLE = { interrompu: "✕", perturbe: "!", travaux: "⚒" };
const LIBELLE_NIVEAU = { interrompu: "trafic interrompu", perturbe: "trafic perturbé", travaux: "travaux" };
// Ondes « temps réel » (comme dans les applis de transport).
const ONDES = `<svg class="ondes" viewBox="0 0 12 12" aria-label="temps réel"><path d="M2 10a8 8 0 0 1 8-8" /><path d="M2 10a4.5 4.5 0 0 1 4.5-4.5" /></svg>`;

const echapper = (t) =>
  String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const heureCourte = (h) => (h ? `${Number(h.slice(0, 2))} h ${h.slice(3, 5)}` : "");
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
    this._enCours = new Set();
    this._erreurs = new Map();
    this._ligneInfo = null; // ligne dont on lit le détail dans l'info trafic
    this._rech = { texte: "", lieux: [], dest: null, depuis: null, depuisTexte: "", lieuxDepuis: [], mode: "maintenant", heure: "", resultats: null, erreur: "", calcul: false };
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

  static getConfigForm() {
    const choix = (options) => ({ select: { mode: "dropdown", options } });
    return {
      schema: [
        { name: "titre", selector: { text: {} } },
        {
          type: "grid",
          name: "",
          schema: [
            { name: "afficher_recherche", selector: { boolean: {} } },
            { name: "afficher_info_trafic", selector: { boolean: {} } },
            { name: "afficher_gares", selector: { boolean: {} } },
            { name: "afficher_trajets", selector: choix([
              { value: "miens", label: "Mes trajets" }, { value: "tous", label: "Tous les trajets" }, { value: "aucun", label: "Aucun" }]) },
          ],
        },
        { name: "entities", selector: { entity: { multiple: true, filter: { integration: "transports_idf", domain: "sensor" } } } },
        {
          type: "grid",
          name: "",
          schema: [
            { name: "filtre_arrets", selector: choix([{ value: "tous", label: "Tous les arrêts" }, { value: "mes_trajets", label: "Arrêts de mes trajets" }]) },
            { name: "regroupement", selector: choix([{ value: "ligne", label: "Par ligne et direction" }, { value: "chronologique", label: "Chronologique" }]) },
            { name: "nb_passages", selector: { number: { min: 0, max: 30, mode: "box" } } },
            { name: "afficher_trop_tard", selector: { boolean: {} } },
            { name: "afficher_marche", selector: { boolean: {} } },
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
          afficher_recherche: "Recherche d'itinéraire",
          afficher_info_trafic: "Info trafic",
          afficher_gares: "Gares et arrêts",
          afficher_trajets: "Trajets préférés",
          entities: "Arrêts (vide = selon le filtre)",
          filtre_arrets: "Arrêts affichés",
          regroupement: "Présentation des passages",
          nb_passages: "Passages par arrêt (0 = réglage de l'intégration)",
          afficher_trop_tard: "Montrer les passages trop tard",
          afficher_marche: "Montrer le temps de marche",
          afficher_plan: "Bouton plan sur les arrêts",
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
    const partir = t.closest?.("[data-partir]");
    if (partir) return this._partir(partir.dataset.partir);
    const ligne = t.closest?.("[data-ligne-info]");
    if (ligne) {
      this._ligneInfo = this._ligneInfo === ligne.dataset.ligneInfo ? null : ligne.dataset.ligneInfo;
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
    this._ouverts.set(`${id}|trajet`, true);
    this._rendu();
    try {
      await this._hass.callService("transports_idf", "partir_maintenant", { trajet: id });
    } catch (err) {
      this._erreurs.set(id, err?.message || "Calcul impossible");
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
    return `<div class="pli ${classe}${ouvert ? " ouvert" : ""}">
      <div class="pli-tete" data-bascule="${echapper(cle)}" data-defaut="${defaut ? 1 : 0}" role="button" tabindex="0" aria-expanded="${ouvert}">
        <span class="chevron">${ouvert ? "▾" : "▸"}</span>${tete}</div>${extra}
      ${ouvert ? `<div class="pli-corps">${corps}</div>` : ""}</div>`;
  }

  _logo(l, { glyphe = false, alerte = null, cliquable = false } = {}) {
    const groupe = groupeDe(l);
    const code = l.nom ?? l.ligne ?? l.code ?? "?";
    const fond = l.couleur || "#666";
    const texte = l.couleur_texte || "#fff";
    const g = glyphe ? `<span class="glyphe g-${norm(groupe)}">${{ "Métro": "M", RER: "RER", Train: "TRAIN", Tram: "TRAM", Bus: "BUS" }[groupe] || ""}</span>` : "";
    const niveau = alerte && alerte !== "normal" ? alerte : null;
    const pastille = niveau ? `<span class="pastille ${niveau}" title="${LIBELLE_NIVEAU[niveau]}">${PASTILLE[niveau]}</span>` : "";
    const ident = l.id ?? l.ligne_id;
    const attrs = cliquable ? ` data-ligne-info="${echapper(ident)}" role="button" tabindex="0" title="Voir l'info trafic"` : "";
    const long = String(code).length > 2 ? " long" : "";
    return `${g}<span class="logo f-${norm(groupe)}${long}${niveau ? ` a-${niveau}` : ""}${cliquable && this._ligneInfo === ident ? " choisi" : ""}" style="--c:${echapper(fond)};--t:${echapper(texte)}"${attrs}>${echapper(code)}${pastille}</span>`;
  }

  _chaine(etapes, compacte = false) {
    // Version compacte (résumé d'un trajet replié) : seule la marche du début est gardée.
    return (etapes || [])
      .filter((e, n) => e.type === "transport" || (e.duree && (!compacte || n === 0)))
      .map((e) => (e.type === "marche" ? `<span class="pied">🚶<sub>${echapper(e.duree)}</sub></span>` : this._logo(e, { glyphe: true, alerte: e.alerte, cliquable: !!(e.alerte && e.alerte !== "normal") })))
      .join(`<span class="sep">›</span>`);
  }

  _duree(it) {
    if (!it?.duree) return "";
    const m = Math.round(it.duree / 60);
    return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min`;
  }

  _resumeIt(it, intitule = "") {
    return `${intitule ? `<span class="intitule">${intitule}</span>` : ""}<span class="duree">${this._duree(it)}</span>
      <span class="horaires">${echapper(it.depart_h || "")}${it.arrivee_h ? ` – ${echapper(it.arrivee_h)}` : ""}</span>
      <span class="chaine">${this._chaine(it.etapes)}</span>`;
  }

  // Messages trafic d'une ligne (info trafic, sinon perturbations des trajets).
  _messagesLigne(id) {
    const info = this._etats("info_trafic")[0]?.attributes.lignes?.find((l) => l.id === id);
    if (info?.perturbations?.length) return info.perturbations;
    const vus = new Set();
    return this._etats("trajet").flatMap((t) => t.attributes.perturbations || [])
      .filter((p) => (p.lignes || []).includes(id) && !vus.has(p.id) && vus.add(p.id));
  }

  // Bloc affiché sous un résumé ou une étape quand on a cliqué sur le logo d'une de ses lignes.
  _messageSi(etapes) {
    const id = this._ligneInfo;
    const e = id && (etapes || []).find((x) => x.type === "transport" && x.ligne_id === id && x.alerte && x.alerte !== "normal");
    if (!e) return "";
    const ps = this._messagesLigne(id);
    const corps = ps.length
      ? ps.map((p) => `<div class="msg-titre">${echapper(p.titre)}</div><div class="message">${echapper(p.message && p.message !== p.titre ? p.message : "")}</div>`).join("")
      : `<div class="message">${LIBELLE_NIVEAU[e.alerte]}</div>`;
    return `<div class="msg-ligne ${e.alerte}">${corps}</div>`;
  }

  _etapes(it) {
    const etapes = it?.etapes || [];
    if (!etapes.length) return `<div class="sous">${echapper(it?.resume || "")}</div>`;
    return `<ol class="etapes">${etapes
      .map((e) => {
        if (e.type === "marche") return `<li class="marche"><span class="h"></span><span>🚶 ${echapper(e.duree)} min${e.vers ? ` jusqu'à ${echapper(e.vers)}` : ""}</span></li>`;
        const suivants = e.suivants?.length ? `<span class="suivants" title="Départs suivants si tu le rates">+${e.suivants.join(", +")} min</span>` : "";
        const train = [e.voie ? `voie <b>${echapper(e.voie)}</b>` : "", e.mission ? `<b class="mission">${echapper(e.mission)}</b>` : ""].filter(Boolean).join(" · ");
        return `<li><span class="h">${echapper(e.depart || "")}${suivants}</span>${this._logo(e, { alerte: e.alerte, cliquable: !!(e.alerte && e.alerte !== "normal") })}
          <span class="trajet-ligne"><span>${echapper(e.de)} → ${echapper(e.vers)}</span>
          <span class="sous">${e.direction ? `dir. ${echapper(e.direction)} · ` : ""}arr. ${echapper(e.arrivee || "?")}</span>
          ${train ? `<span class="sous train">${train}</span>` : ""}${this._rame(e.rame)}${this._messageSi([e])}</span></li>`;
      })
      .join("")}</ol>`;
  }

  _rame(r) {
    if (!r?.voitures) return "";
    // Rame vue de côté, dans le sens de la marche : l'arrière à gauche, la cabine de tête à droite.
    const voitures = Array.from({ length: r.voitures }, (_, i) => i + 1)
      .reverse()
      .map((v) => `<span class="voiture${v === r.voiture ? " ici" : ""}${v === r.voitures ? " queue" : ""}${v === 1 ? " tete" : ""}"><span class="vitre"></span></span>`)
      .join("");
    const suite = r.type === "sortie"
      ? `<span class="panneau"><span class="panneau-num">${echapper(r.numero || "?")}</span>${echapper(r.nom || r.vers)}</span>`
      : `<span class="vers-corresp">↳ ${r.suivante ? this._logo(r.suivante, { glyphe: true }) : echapper([r.mode_suivant, r.ligne_suivante].filter(Boolean).join(" "))}</span>`;
    const equipement = r.equipement ? `<span class="sous">${echapper(r.equipement.toLowerCase())}</span>` : "";
    const ou = { avant: "l'avant", milieu: "le milieu", arriere: "l'arrière" }[r.zone] || "";
    return `<span class="rame-ligne"><span class="rame" title="Monter vers ${ou} du train (voiture ${r.voiture} sur ${r.voitures}) ; sens de la marche vers la droite">${voitures}</span>${suite}${equipement}</span>`;
  }

  // ---- Sections ------------------------------------------------------------
  _htmlRecherche() {
    const r = this._rech;
    const sugg = (champ, lieux) =>
      lieux.length
        ? `<ul class="suggestions">${lieux.map((l, n) => `<li data-lieu="${n}" data-champ="${champ}" role="button" tabindex="0">${{ stop_area: "🚉", address: "📍", poi: "⭐", administrative_region: "🏙️" }[l.type] || "📍"} ${echapper(l.libelle || l.nom)}</li>`).join("")}</ul>`
        : "";
    let resultats = "";
    if (r.calcul) resultats = `<div class="sous">Calcul…</div>`;
    else if (r.erreur) resultats = `<div class="erreur">${echapper(r.erreur)}</div>`;
    else if (r.resultats) {
      resultats = r.resultats.length
        ? r.resultats.map((it, n) => this._pli(`rech|${n}`, false, this._resumeIt(it), this._etapes(it), "proposition", this._ouvert(`rech|${n}`, false) ? "" : this._messageSi(it.etapes))).join("")
        : `<div class="sous">Aucun itinéraire trouvé.</div>`;
    }
    return { suggDest: sugg("dest", r.lieux), suggDepuis: sugg("depuis", r.lieuxDepuis), resultats };
  }

  _htmlTrajet(s) {
    const a = s.attributes;
    const id = s.entity_id;
    const arrivee = a.type_heure === "arrivee";
    const c = a.conseil;
    const ref = a.reference;
    const enVeille = !!a.en_veille;
    const maintenant = this._maintenant(a);
    const ouvert = this._ouvert(`${id}|trajet`, enVeille);

    // Résumé replié : lignes (avec pastille si problème), départ, arrivée.
    const etapes = (enVeille && ref?.etapes?.length ? ref.etapes : a.etapes_habituelles) || [];
    const heureArr = arrivee ? heureCourte(a.heure) : enVeille && ref?.arrivee_h ? ref.arrivee_h : "";
    let statut = "";
    if (c?.impossible) statut = `<span class="statut mauvais">arrivée impossible</span>`;
    else if (arrivee && enVeille && c?.depart_h) {
      const tot = (c.avance || 0) >= 3;
      statut = `<span class="statut ${tot ? "attention" : "ok"}">pars à ${echapper(c.depart_h)}${tot ? ` (−${c.avance} min)` : ""}</span>`;
    } else if (!arrivee) statut = `<span class="statut neutre">départ ${heureCourte(a.heure)}</span>`;
    const classe = s.state === "on" ? (a.alerte === "interrompu" ? "mauvais" : "attention") : (c?.avance || 0) >= 3 ? "attention" : "";
    const tete = `<span class="trajet-nom">${echapper(a.trajet)}</span><span class="chaine">${this._chaine(etapes, true)}</span>
      <span class="trajet-fin">${statut}${heureArr ? `<span class="arrivee">→ ${echapper(heureArr)}</span>` : ""}</span>
      <span class="trajet-depuis">depuis ${echapper(a.origine_nom || "la maison")}${a.destination ? ` · vers ${echapper(a.destination)}` : ""}</span>`;

    const corps = [];
    const bouton = `<button class="partir" type="button" data-partir="${echapper(id)}"${this._enCours.has(id) ? " disabled" : ""}>${this._enCours.has(id) ? "Calcul…" : "🚶 Partir maintenant"}</button>`;
    corps.push(`<div class="actions">${bouton}${this._erreurs.has(id) ? `<span class="erreur">${echapper(this._erreurs.get(id))}</span>` : ""}</div>`);
    const vus = new Set();
    const prop = (it, cle, intitule, classe = "proposition") => {
      if (!it?.etapes?.length) return;
      const sig = `${it.depart}|${it.resume}`;
      if (vus.has(sig)) return;
      vus.add(sig);
      const cleP = `${id}|${cle}`;
      corps.push(this._pli(cleP, false, this._resumeIt(it, intitule), this._etapes(it), classe, this._ouvert(cleP, false) ? "" : this._messageSi(it.etapes)));
    };
    if (maintenant) {
      (maintenant.itineraires || []).forEach((it, n) => prop(it, `m${n}`, n === 0 ? "Maintenant" : "Ou", "proposition maintenant"));
      if (!(maintenant.itineraires || []).length) corps.push(`<div class="sous">Aucun itinéraire au départ de maintenant.</div>`);
    }
    if (enVeille || s.state === "on") {
      if (ref) prop(ref, "ref", "Préféré", "proposition prefere");
      else if (s.state === "on") corps.push(`<div class="sous">Itinéraire préféré indisponible pour l'instant.</div>`);
      if (arrivee && c && !c.impossible && !c.habituel) prop(c, "conseil", "À prendre");
      if (a.rechange?.etapes?.length) prop(a.rechange, "rechange", "Rechange");
      for (const [n, it] of (a.alternatives || []).entries()) {
        const g = it.gain;
        const libelle = g == null || g === 0 ? "Autre" : arrivee ? (g >= 0 ? `Part ${g} min plus tard` : `Part ${-g} min plus tôt`) : g >= 0 ? `${g} min plus rapide` : `${-g} min plus long`;
        prop(it, `alt${n}`, libelle, `proposition${it.plus_rapide ? " mieux" : ""}`);
      }
    }
    for (const p of a.perturbations || []) {
      const fin = p.fin ? `<div class="sous">Jusqu'au ${echapper(p.fin.slice(6, 8))}/${echapper(p.fin.slice(4, 6))} à ${Number(p.fin.slice(9, 11))} h ${echapper(p.fin.slice(11, 13))}</div>` : "";
      corps.push(this._pli(`${id}|p|${p.id}`, false,
        `<span class="etiquette ${p.niveau || "perturbe"}">${PASTILLE[p.niveau] || "!"}</span><span class="alerte-titre">${echapper((p.lignes_trajet || []).join(", "))} — ${echapper(p.titre)}</span>`,
        `<div class="message">${echapper(p.message || p.titre)}${fin}</div>`, "alerte"));
    }
    if (!enVeille && s.state !== "on" && !maintenant) corps.push(`<div class="sous">Propositions en direct pendant la veille (à partir de ${heureCourte(a.depart_habituel)} moins ${a.avance} min), ou avec « Partir maintenant ».</div>`);
    return this._pli(`${id}|trajet`, enVeille, tete, corps.join(""), `trajet ${classe}`, ouvert ? "" : this._messageSi(etapes));
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
      .map(([g, ls]) => `<div class="rangee"><span class="glyphe g-${norm(g)}">${glyphes[g]}</span><div class="logos">${ls.map((l) => this._logo(l, { alerte: l.etat, cliquable: true })).join("")}</div></div>`)
      .join("");
    const touchees = lignes.filter((l) => (l.base || l.interet || mesIds.has(l.id)) && l.etat !== "normal").length;
    const choisie = lignes.find((l) => l.id === this._ligneInfo);
    const detail = choisie
      ? `<div class="detail-ligne"><div class="detail-tete">${this._logo(choisie, { glyphe: true, alerte: choisie.etat })}<span class="${choisie.etat === "normal" ? "statut ok" : `etiquette ${choisie.etat}`}">${choisie.etat === "normal" ? "trafic normal" : LIBELLE_NIVEAU[choisie.etat]}</span></div>
          ${(choisie.perturbations || []).length ? choisie.perturbations.map((p, n) => this._pli(`info|${choisie.id}|${n}`, n === 0,
            `<span class="etiquette ${p.niveau}">${PASTILLE[p.niveau] || "!"}</span><span class="alerte-titre">${echapper(p.titre)}</span>`,
            `<div class="message">${echapper(p.message || p.titre)}</div>`, "alerte")).join("") : `<div class="sous">Trafic normal.</div>`}</div>`
      : "";
    const resume = touchees ? `<span class="statut attention">${touchees} ligne${touchees > 1 ? "s" : ""} touchée${touchees > 1 ? "s" : ""}</span>` : `<span class="statut ok">trafic normal</span>`;
    return this._pli("info", true, `<span class="section-titre">Info trafic</span>${resume}`, `<div class="grille">${grille}</div>${detail}`, "section info");
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
      return `<span class="n ${p.etat}" title="${ETATS[p.etat].libelle}${p.temps_reel ? "" : " · horaire théorique"}">${m >= 60 ? new Date(p.depart).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }) : m}</span>`;
    };
    const temps = (ps) => {
      const reel = ps[0]?.temps_reel;
      const unite = ps.some((p) => !p.supprime && !p.a_quai && p.minutes >= 0.5 && p.minutes < 60) ? `<span class="unite">min</span>` : "";
      return `<span class="temps${reel ? "" : " theorique"}">${reel ? ONDES : ""}${ps.map(nombre).join(", ")} ${unite}</span>`;
    };
    if (this._config.regroupement === "chronologique") {
      return passages.slice(0, n).map((p) => `<div class="ligne-passage ${p.etat}">${this._logo(p)}<span class="dest">${echapper(p.destination || "")}${p.quai ? `<span class="quai">voie ${echapper(p.quai)}</span>` : ""}</span>${temps([p])}</div>`).join("");
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
        return `<div class="ligne-passage ${tete.etat}">${this._logo(g[0])}<span class="dest">${echapper(g[0].destination || "")}</span>${temps(g.slice(0, Math.max(1, Math.min(n, 3))))}</div>`;
      })
      .join("");
  }

  _urlPlan(a) {
    const lat = Number(a.lat), lon = Number(a.lon);
    if (!a.lat || !isFinite(lat) || !isFinite(lon)) return null;
    const dlat = Number(a.depart_lat), dlon = Number(a.depart_lon);
    const pts = a.depart_lat && isFinite(dlat) && isFinite(dlon) ? [[lat, lon], [dlat, dlon]] : [[lat, lon]];
    const m = 0.0025;
    const s = Math.min(...pts.map((p) => p[0])) - m, n = Math.max(...pts.map((p) => p[0])) + m;
    const o = Math.min(...pts.map((p) => p[1])) - m * 1.5, e = Math.max(...pts.map((p) => p[1])) + m * 1.5;
    return {
      carte: `https://www.openstreetmap.org/export/embed.html?bbox=${o},${s},${e},${n}&layer=mapnik&marker=${lat},${lon}`,
      pied: pts.length > 1
        ? `https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=${dlat}%2C${dlon}%3B${lat}%2C${lon}`
        : `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=17/${lat}/${lon}`,
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
            <input class="heure" type="time" hidden aria-label="Heure">
          </div>
          <div class="sugg-depuis"></div>
          <div class="resultats"></div>
        </div>
        <div class="trajets"></div>
        <div class="info"></div>
        <div class="arrets"></div>
        <div class="aucun"></div>
      </ha-card>`;
    const q = (sel) => this.shadowRoot.querySelector(sel);
    this._el = {
      style: q("style"), titre: q(".titre"), recherche: q(".recherche"), dest: q(".dest"), depuis: q(".depuis"), mode: q(".mode"),
      heure: q(".heure"), effacer: q(".effacer"), options: q(".options-recherche"), suggDest: q(".sugg-dest"),
      suggDepuis: q(".sugg-depuis"), resultats: q(".resultats"), trajets: q(".trajets"), info: q(".info"), arrets: q(".arrets"), aucun: q(".aucun"),
    };
    this._el.dest.addEventListener("input", (e) => this._saisie("dest", e.target.value));
    this._el.depuis.addEventListener("input", (e) => this._saisie("depuis", e.target.value));
    this._el.mode.addEventListener("change", (e) => {
      this._rech.mode = e.target.value;
      if (this._rech.mode !== "maintenant" && !this._rech.heure) {
        const d = new Date(Date.now() + (this._rech.mode === "arrivee" ? 45 : 0) * 60000);
        this._rech.heure = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
        this._el.heure.value = this._rech.heure;
      }
      this._calculer();
    });
    this._el.heure.addEventListener("change", (e) => {
      this._rech.heure = e.target.value;
      this._calculer();
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
      this._el.heure.hidden = r.mode === "maintenant";
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
      const marche = cfg.afficher_marche && a.temps_marche != null ? `<span class="marche">🚶 ${echapper(a.temps_marche)} min</span>` : "";
      const indispo = arret.state === "unavailable" ? `<span class="indispo">indisponible</span>` : "";
      const boutonPlan = plan ? `<button class="bouton-plan${planOuvert ? " actif" : ""}" type="button" data-plan="${echapper(id)}" aria-label="Plan">🗺️</button>` : "";
      b.section.classList.toggle("replie", replie);
      b.entete.setAttribute("aria-expanded", String(!replie));
      this._maj(b.entete, `<span class="chevron">${replie ? "▸" : "▾"}</span><span class="arret">${echapper(a.arret || a.friendly_name)}</span>${indispo}${marche}${boutonPlan}`);
      if (planOuvert) {
        if (b.plan._url !== plan.carte) {
          b.plan._url = plan.carte;
          b.plan.innerHTML = `<iframe loading="lazy" referrerpolicy="no-referrer" title="Plan" src="${echapper(plan.carte)}"></iframe><a href="${echapper(plan.pied)}" target="_blank" rel="noopener">Itinéraire à pied ↗</a>`;
        }
        b.plan.hidden = false;
      } else b.plan.hidden = true;
      b.corps.hidden = replie;
      if (!replie) this._maj(b.corps, this._htmlPassages(arret));
    });
    this._maj(this._el.aucun, cfg.afficher_gares && !arrets.length ? `<div class="vide">Aucun arrêt : ajoute-en un depuis l'intégration Transports IDF.</div>` : "");
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
      .options-recherche { display: flex; flex-wrap: wrap; gap: 6px; }
      .options-recherche input, .options-recherche select {
        font: inherit; font-size: .9em; color: var(--primary-text-color); background: var(--ti-fond);
        border: 1px solid var(--ti-trait); border-radius: 16px; padding: 4px 10px; min-width: 0;
      }
      .options-recherche .depuis { flex: 1 1 160px; }
      .suggestions { list-style: none; margin: 0; padding: 4px; border: 1px solid var(--ti-trait); border-radius: 10px; display: flex; flex-direction: column; }
      .suggestions li { padding: 6px 8px; border-radius: 6px; cursor: pointer; overflow-wrap: anywhere; }
      .suggestions li:hover { background: var(--ti-fond); }
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
      .logo[data-ligne-info] { cursor: pointer; }
      .logo.choisi { outline: 2px solid var(--primary-color); outline-offset: 2px; }
      .pastille {
        position: absolute; right: -6px; bottom: -6px; min-width: 13px; height: 13px; border-radius: 3px; font-size: 9px; font-weight: 800;
        display: inline-flex; align-items: center; justify-content: center; color: #fff; border: 1px solid var(--ha-card-background, #fff);
      }
      .pastille.interrompu { background: var(--ti-rouge); }
      .pastille.perturbe { background: var(--ti-orange); border-radius: 50%; }
      .pastille.travaux { background: #757575; }
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
      .etapes li { display: grid; grid-template-columns: 4.2em auto 1fr; align-items: start; gap: 8px; }
      .etapes li.marche { grid-template-columns: 4.2em 1fr; color: var(--secondary-text-color); }
      .etapes .h { font-weight: 600; font-variant-numeric: tabular-nums; display: flex; flex-direction: column; line-height: 1.25; }
      .etapes .h .suivants { font-weight: 400; font-size: .78em; color: var(--secondary-text-color); white-space: nowrap; margin: 0; }
      .trajet-ligne { display: flex; flex-direction: column; min-width: 0; overflow-wrap: anywhere; }
      .suivants { margin-right: 6px; }
      .rame-ligne { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; font-size: .85em; margin-top: 4px; }
      .rame { display: inline-flex; align-items: center; gap: 2px; padding: 3px 4px; border-radius: 4px; background: #1d2654; }
      .voiture { position: relative; width: 16px; height: 11px; background: #5b6a8f; border-radius: 2px; }
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
      .etapes .msg-ligne { margin-left: 0; }
      .msg-titre { font-weight: 600; }
      .nez { font-size: .8em; color: var(--secondary-text-color); margin-left: 1px; }

      /* Info trafic */
      .section { background: var(--ti-fond); border-radius: 10px; padding: 4px 12px; }
      .section > .pli-tete .statut { margin-left: auto; }
      .grille { display: flex; flex-direction: column; gap: 8px; padding: 4px 0 6px; }
      .rangee { display: grid; grid-template-columns: 3em 1fr; align-items: center; gap: 6px; }
      .rangee .glyphe { justify-self: start; }
      .logos { display: flex; flex-wrap: wrap; gap: 9px 9px; }
      .detail-tete { display: flex; align-items: center; gap: 8px; }
      .detail-ligne { border-top: 1px solid var(--ti-trait); padding-top: 6px; display: flex; flex-direction: column; gap: 2px; }

      /* Gares */
      .arret-bloc { background: var(--ti-fond); border-radius: 10px; padding: 2px 12px 4px; }
      .arret-bloc.replie { padding-bottom: 2px; }
      .entete { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: .85em; color: var(--secondary-text-color); padding: 6px 0 4px; }
      .arret { flex: 1; min-width: 0; overflow-wrap: anywhere; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--primary-text-color); }
      .marche { white-space: nowrap; }
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
      .ondes { width: 11px; height: 11px; vertical-align: -1px; fill: none; stroke: #f2b600; stroke-width: 2; stroke-linecap: round; margin-right: 2px; }
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
