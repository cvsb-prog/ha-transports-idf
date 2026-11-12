"""Coordinateurs : prochains passages (arrêts) et info trafic (trajets)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ClientPrim,
    ErreurJeton,
    ErreurPrim,
    active_a,
    cles_arret,
    concerne_ligne,
    date_navitia,
    messages_affiches,
    pertinente,
    pire_niveau,
    refs_candidates,
)
from .const import (
    A_LIGNES,
    A_LIGNES_SUIVIES,
    A_MARCHE,
    A_NOM,
    A_REFS,
    A_ZONE_ID,
    CONF_ZONE,
    DEFAUTS,
    DOMAIN,
    ETAT_ATTEIGNABLE,
    ETAT_PRESSE,
    ETAT_TROP_TARD,
    LIGNES_BASE,
    OPT_DEBUT_NUIT,
    OPT_FIN_NUIT,
    OPT_IGNORER_INFO,
    OPT_INTERVALLE_JOUR,
    OPT_INTERVALLE_NUIT,
    OPT_LIGNES_INTERET,
    OPT_MARGE,
    SOUS_ARRET,
    SOUS_LIGNE,
    SOUS_TRAJET,
    T_DEPART_HABITUEL,
    T_DUREE,
    T_HEURE,
    T_ORIGINE,
    T_ORIGINE_ID,
    T_SECTIONS,
    T_TYPE_HEURE,
)
from .const import (
    A_LIGNES as _A_LIGNES,
)

_LOGGER = logging.getLogger(__name__)

HORIZON = timedelta(minutes=120)  # passages gardés au-delà de maintenant
MAX_PASSAGES = 40
TRAFIC_VEILLE = timedelta(minutes=2)
TRAFIC_REPOS = timedelta(minutes=20)
APRES_DEPART = timedelta(minutes=10)  # la veille se prolonge un peu après l'heure prévue
VALIDITE_RECHANGE = timedelta(minutes=10)
DUREE_MAINTENANT = timedelta(minutes=15)  # affichage du résultat de « Partir maintenant »
INFO_TRAFIC = timedelta(minutes=5)
CACHE_PERTURBATIONS = timedelta(minutes=5)


def option(entry: ConfigEntry, cle: str) -> Any:
    return entry.options.get(cle, DEFAUTS[cle])


def _heure(texte: str) -> time:
    h, m, *s = (int(x) for x in texte.split(":"))
    return time(h, m, s[0] if s else 0)


def est_la_nuit(maintenant: datetime, debut: str, fin: str) -> bool:
    t, d, f = maintenant.time(), _heure(debut), _heure(fin)
    return d <= t < f if d <= f else (t >= d or t < f)


def etat_passage(minutes: float, marche: float, marge: float) -> str:
    """atteignable si ≥ marche + marge ; en se pressant si ≥ marche ; sinon trop tard."""
    if minutes >= marche + marge:
        return ETAT_ATTEIGNABLE
    if minutes >= marche:
        return ETAT_PRESSE
    return ETAT_TROP_TARD


def depart_habituel(d: dict[str, Any]) -> str:
    """Heure de départ habituelle « HH:MM:SS » d'un trajet, quel que soit son mode."""
    if d.get(T_TYPE_HEURE) != "arrivee":
        return d[T_HEURE]
    if d.get(T_DEPART_HABITUEL):
        return d[T_DEPART_HABITUEL]
    h = _heure(d[T_HEURE])
    debut = datetime(2000, 1, 2, h.hour, h.minute) - timedelta(seconds=int(d.get(T_DUREE) or 3600))
    return debut.strftime("%H:%M:%S")


def origine_navitia(hass: HomeAssistant, entry: ConfigEntry, d: dict[str, Any]) -> str:
    """Départ d'un trajet pour le calculateur : lieu Navitia choisi, sinon coordonnées de la zone."""
    if d.get(T_ORIGINE_ID):
        return d[T_ORIGINE_ID]
    lat, lon = coordonnees_zone(hass, d.get(T_ORIGINE) or entry.data.get(CONF_ZONE))
    return f"{lon};{lat}"


def coordonnees_zone(hass: HomeAssistant, zone: str | None) -> tuple[float, float]:
    etat = hass.states.get(zone) if zone else None
    if etat and "latitude" in etat.attributes and "longitude" in etat.attributes:
        return float(etat.attributes["latitude"]), float(etat.attributes["longitude"])
    return hass.config.latitude, hass.config.longitude


class PassagesCoordinator(DataUpdateCoordinator[dict[str, list[dict[str, Any]]]]):
    """Prochains passages de tous les arrêts de l'entrée (clé : id de sous-entrée)."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ClientPrim) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} passages",
            update_interval=timedelta(seconds=option(entry, OPT_INTERVALLE_JOUR)),
        )
        self.client = client

    def _regler_intervalle(self) -> None:
        e = self.config_entry
        nuit = est_la_nuit(dt_util.now(), option(e, OPT_DEBUT_NUIT), option(e, OPT_FIN_NUIT))
        self.update_interval = timedelta(seconds=option(e, OPT_INTERVALLE_NUIT if nuit else OPT_INTERVALLE_JOUR))

    async def _async_update_data(self) -> dict[str, list[dict[str, Any]]]:
        self._regler_intervalle()
        arrets = [s for s in self.config_entry.subentries.values() if s.subentry_type == SOUS_ARRET]
        travaux = [(s.subentry_id, ref) for s in arrets for ref in s.data.get(A_REFS, [])]
        if not travaux:
            return {}
        resultats = await asyncio.gather(*(self.client.passages(ref) for _, ref in travaux), return_exceptions=True)
        if any(isinstance(r, ErreurJeton) for r in resultats):
            raise ConfigEntryAuthFailed("Jeton PRIM refusé")
        if all(isinstance(r, Exception) for r in resultats):
            raise UpdateFailed(f"Prochains passages indisponibles : {resultats[0]}")

        maintenant = dt_util.utcnow()
        par_arret: dict[str, list[dict[str, Any]]] = {s.subentry_id: [] for s in arrets}
        vus: dict[str, set] = {s.subentry_id: set() for s in arrets}
        for (sid, ref), res in zip(travaux, resultats, strict=True):
            if isinstance(res, Exception):
                _LOGGER.debug("Passages %s : %s", ref, res)
                continue
            for p in res:
                cle = (p["voyage"] or p["destination"], p["ligne"], p["depart"])
                if cle in vus[sid]:
                    continue
                vus[sid].add(cle)
                par_arret[sid].append(p)

        for s in arrets:
            suivies = set(s.data.get(A_LIGNES_SUIVIES) or [])
            codes = {l["code"] for l in s.data.get(A_LIGNES, []) if l["id"] in suivies}
            passages = [
                p
                for p in par_arret[s.subentry_id]
                if (not codes or p["ligne"] in codes)
                and maintenant - timedelta(minutes=1) <= p["depart"] <= maintenant + HORIZON
            ]
            passages.sort(key=lambda p: p["depart"])
            par_arret[s.subentry_id] = passages[:MAX_PASSAGES]
        return par_arret

    def passages_affiches(self, sous_id: str) -> list[dict[str, Any]]:
        """Passages enrichis (ligne, minutes, état) pour une sous-entrée d'arrêt."""
        s = self.config_entry.subentries.get(sous_id)
        if s is None or not self.data:
            return []
        lignes = {l["code"]: l for l in s.data.get(A_LIGNES, [])}
        marche = float(s.data.get(A_MARCHE, 0))
        marge = float(option(self.config_entry, OPT_MARGE))
        maintenant = dt_util.utcnow()
        sortie = []
        for p in self.data.get(sous_id, []):
            minutes = (p["depart"] - maintenant).total_seconds() / 60
            if minutes < -0.5:
                continue
            meta = lignes.get(p["ligne"], {})
            sortie.append(
                {
                    "ligne": meta.get("nom") or p["ligne"],
                    "ligne_id": meta.get("id"),
                    "mode": meta.get("mode"),
                    "couleur": meta.get("couleur"),
                    "couleur_texte": meta.get("couleur_texte"),
                    "destination": p["destination"],
                    "depart": p["depart"].isoformat(),
                    "minutes": round(minutes, 1),
                    "temps_reel": p["temps_reel"],
                    "quai": p["quai"],
                    "a_quai": p["a_quai"],
                    "supprime": p["supprime"],
                    "mission": p["mission"],
                    "etat": etat_passage(minutes, marche, marge),
                }
            )
        return sortie



class Itineraires:
    """Calculs d'itinéraires partagés : trajets, « Partir maintenant », recherche de la carte."""

    def __init__(self, hass: HomeAssistant, client: ClientPrim) -> None:
        self.hass = hass
        self.client = client
        self._cache: dict[tuple, tuple[datetime, list[datetime]]] = {}
        self._cache_perts: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}
        # Perturbations d'une ligne suivie par l'info trafic (None : ligne non suivie) ; branchée au démarrage.
        self.perturbations_info: Any = lambda ligne_id: None
        self.ignorer_info = True
        self.rame = None  # PositionRame, branchée au démarrage

    async def suivants(self, its: list[dict[str, Any]]) -> None:
        """Ajoute à chaque étape en transport les départs suivants (minutes après le départ visé)."""
        taches, cibles = [], []
        for it in its:
            for e in it.get("etapes") or []:
                if e.get("type") != "transport":
                    continue
                vise = date_navitia(e.get("depart_brut"))
                if not (vise and e.get("de_point") and e.get("route_id")):
                    continue
                cle = (e["de_point"], e["route_id"], vise)
                cache = self._cache.get(cle)
                if cache and dt_util.utcnow() - cache[0] < timedelta(minutes=2):
                    e["suivants"] = _ecarts(vise, cache[1])
                    continue
                cibles.append((e, cle, vise))
                taches.append(self.client.departs_route(e["de_point"], e["route_id"], vise))
        await self._voies(its)
        if not taches:
            return
        for (e, cle, vise), r in zip(cibles, await asyncio.gather(*taches, return_exceptions=True), strict=True):
            if isinstance(r, Exception):
                _LOGGER.debug("Départs suivants : %s", r)
                continue
            self._cache[cle] = (dt_util.utcnow(), r)
            e["suivants"] = _ecarts(vise, r)
        if len(self._cache) > 300:
            self._cache.clear()

    async def _voies(self, its: list[dict[str, Any]]) -> None:
        """RER / Transilien : voie et nom de mission du train visé, lus dans les prochains passages en gare."""
        cibles = []
        for it in its:
            for e in it.get("etapes") or []:
                if e.get("type") != "transport" or not e.get("de_point") or not _ferre(e.get("mode")):
                    continue
                vise = date_navitia(e.get("depart_brut"))
                if vise:
                    cibles.append((e, vise.replace(tzinfo=dt_util.get_default_time_zone())))
        if not cibles:
            return
        refs = sorted({refs_candidates("", [e["de_point"]])[0] for e, _ in cibles if refs_candidates("", [e["de_point"]])})
        reponses = dict(zip(refs, await asyncio.gather(*(self.client.passages(r) for r in refs), return_exceptions=True), strict=True))
        for e, vise in cibles:
            refs_e = refs_candidates("", [e["de_point"]])
            passages = reponses.get(refs_e[0]) if refs_e else None
            if not isinstance(passages, list):
                continue
            proches = [p for p in passages if p["ligne"] == e.get("code") and abs((p["depart"] - vise).total_seconds()) <= 150]
            if proches:
                p = min(proches, key=lambda p: abs((p["depart"] - vise).total_seconds()))
                e["voie"] = p.get("quai") or e.get("voie")
                e["mission"] = e.get("mission") or (p.get("mission") if p.get("mission") and len(p["mission"]) == 4 else None)

    def _connues(self, ligne_id: str | None) -> list[dict[str, Any]] | None:
        """Perturbations d'une ligne déjà connues (info trafic, ou lecture récente à part)."""
        if not ligne_id:
            return []
        connues = self.perturbations_info(ligne_id)
        if connues is not None:
            return connues
        cache = self._cache_perts.get(ligne_id)
        if cache and dt_util.utcnow() - cache[0] < CACHE_PERTURBATIONS:
            return cache[1]
        return None

    async def alertes(self, its: list[dict[str, Any]], extra: dict[str, list[dict[str, Any]]] | None = None) -> None:
        """Messages trafic qui concernent chaque étape (portion empruntée, heure de passage), et où monter dans la rame."""
        extra = extra or {}
        manquantes = sorted(
            {e.get("ligne_id") for it in its for e in it.get("etapes") or [] if e.get("type") == "transport" and e.get("ligne_id")}
            - set(extra)
        )
        manquantes = [l for l in manquantes if self._connues(l) is None]
        if manquantes:
            reponses = await asyncio.gather(*(self.client.perturbations_ligne(l) for l in manquantes), return_exceptions=True)
            for l, r in zip(manquantes, reponses, strict=True):
                if isinstance(r, ErreurJeton):
                    raise r
                self._cache_perts[l] = (dt_util.utcnow(), r if isinstance(r, list) else [])
            if len(self._cache_perts) > 200:
                self._cache_perts.clear()
        for it in its:
            etapes = it.get("etapes") or []
            for n, e in enumerate(etapes):
                if e.get("type") != "transport":
                    continue
                lid = e.get("ligne_id")
                vues: dict[str, dict[str, Any]] = {}
                for p in (self._connues(lid) or []) + extra.get(lid, []):
                    if not concerne_ligne(p, lid):
                        continue
                    vues.setdefault(p.get("id") or p.get("titre"), p)
                quand = date_navitia(e.get("depart_brut"))
                touchent = [p for p in vues.values() if pertinente(p, e, quand)]
                e["messages"] = messages_affiches(touchent, self.ignorer_info, pour_trajet=True)
                niveau = pire_niveau(m["niveau"] for m in e["messages"] if m["niveau"] != "equipement")
                e["alerte"] = None if niveau == "normal" else niveau
                if self.rame is not None:
                    suite = etapes[n + 1 :]
                    suivante = next((x for x in suite if x.get("type") == "transport"), None)
                    marche = suite[0] if suite and suite[0].get("type") == "marche" else None
                    e["rame"] = self.rame.conseil(e, suivante, marche)
            it["alerte"] = pire_niveau(e.get("alerte") for e in it.get("etapes") or [])


def _ferre(mode: Any) -> bool:
    m = str(mode or "").lower()
    return "rer" in m or "train" in m or "transilien" in m


def _ecarts(vise: datetime, departs: list[datetime]) -> list[int]:
    """Minutes entre le départ visé et les 2 suivants (≥ 1 min après lui)."""
    apres = [d for d in departs if d > vise + timedelta(seconds=30)]
    return [round((d - vise).total_seconds() / 60) for d in apres[:2]]


def _lignes(o: dict[str, Any]) -> list[str]:
    return [s["ligne_id"] for s in o.get("sections", [])]


def _depart(o: dict[str, Any]) -> datetime | None:
    return date_navitia(o.get("depart"))


def _arrivee(o: dict[str, Any]) -> datetime | None:
    return date_navitia(o.get("arrivee"))


def _norm(texte: Any) -> str:
    import unicodedata

    t = unicodedata.normalize("NFD", str(texte or "")).encode("ascii", "ignore").decode().lower()
    return t


def groupe_ligne(ligne: dict[str, Any]) -> str:
    mode = _norm(ligne.get("mode"))
    physique = _norm(ligne.get("mode_physique"))
    if "rer" in mode or "rapidtransit" in physique:
        return "RER"
    if "metro" in mode or "metro" in physique:
        return "Métro"
    if "tram" in mode or "tram" in physique:
        return "Tram"
    if "train" in mode or "transilien" in mode or "train" in physique:
        return "Train"
    if "bus" in mode or "bus" in physique:
        return "Bus"
    return "Autre"


def code_normalise(code: Any) -> str:
    return str(code or "").upper().replace("BIS", "B").replace(" ", "")


class InfoTraficCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """État de chaque ligne : lignes de base (comme en gare), lignes d'intérêt, lignes des trajets et arrêts."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ClientPrim) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} info trafic", update_interval=INFO_TRAFIC)
        self.client = client
        self.reseau: list[dict[str, Any]] = []
        self.reseau_le: datetime | None = None
        self.niveaux: dict[str, str] = {}
        self.par_ligne: dict[str, list[dict[str, Any]]] = {}  # perturbations (portée, périodes) par ligne affichée

    def niveau_ligne(self, ligne_id: str | None) -> str | None:
        n = self.niveaux.get(ligne_id or "")
        return n if n and n != "normal" else None

    def perturbations(self, ligne_id: str) -> list[dict[str, Any]] | None:
        return self.par_ligne.get(ligne_id) if self.data is not None and ligne_id in self.niveaux else None

    def _arrets_suivis(self) -> set[str]:
        cles: set[str] = set()
        for s in self.config_entry.subentries.values():
            if s.subentry_type == SOUS_ARRET:
                cles |= cles_arret({"id": s.data.get(A_ZONE_ID), "name": s.data.get(A_NOM)})
        return cles

    def _lignes_suivies(self) -> dict[str, dict[str, Any]]:
        """Lignes ajoutées par l'utilisateur, ou utilisées par ses trajets et ses arrêts."""
        sortie: dict[str, dict[str, Any]] = {}
        lignes_interet = list(option(self.config_entry, OPT_LIGNES_INTERET) or []) + [
            dict(s.data) for s in self.config_entry.subentries.values() if s.subentry_type == SOUS_LIGNE
        ]
        for l in lignes_interet:
            if l.get("id"):
                sortie[l["id"]] = {**l, "interet": True}
        for s in self.config_entry.subentries.values():
            if s.subentry_type == SOUS_TRAJET:
                for sec in s.data.get(T_SECTIONS, []):
                    if sec.get("ligne_id"):
                        sortie.setdefault(sec["ligne_id"], {"id": sec["ligne_id"], "code": sec.get("code"), "nom": sec.get("nom"), "mode": sec.get("mode"), "couleur": sec.get("couleur"), "couleur_texte": sec.get("couleur_texte")})
            elif s.subentry_type == SOUS_ARRET:
                suivies = set(s.data.get(A_LIGNES_SUIVIES) or [])
                for l in s.data.get(_A_LIGNES, []):
                    if not suivies or l["id"] in suivies:
                        sortie.setdefault(l["id"], dict(l))
        return sortie

    async def _async_update_data(self) -> list[dict[str, Any]]:
        maintenant = dt_util.utcnow()
        if not self.reseau or self.reseau_le is None or maintenant - self.reseau_le > timedelta(hours=24):
            try:
                self.reseau = await self.client.lignes_reseau()
                self.reseau_le = maintenant
            except ErreurJeton as err:
                raise ConfigEntryAuthFailed("Jeton PRIM refusé") from err
            except ErreurPrim as err:
                _LOGGER.debug("Lignes du réseau : %s", err)
        try:
            perts, par_ligne = await self.client.perturbations_reseau()
        except ErreurJeton as err:
            raise ConfigEntryAuthFailed("Jeton PRIM refusé") from err
        except ErreurPrim as err:
            raise UpdateFailed(f"Info trafic indisponible : {err}") from err
        par_id = {p["id"]: p for p in perts}

        # Lignes de base, dans l'ordre des écrans en gare.
        lignes: list[dict[str, Any]] = []
        vues: set[str] = set()
        index = {(groupe_ligne(l), code_normalise(l.get("nom") or l.get("code"))): l for l in self.reseau}
        for groupe, codes in LIGNES_BASE.items():
            for code in codes:
                l = index.get((groupe, code))
                if l and l["id"] not in vues:
                    vues.add(l["id"])
                    lignes.append({**l, "groupe": groupe, "base": True})

        # Lignes suivies hors réseau ferré (bus…) : leur info trafic ligne par ligne.
        suivies = self._lignes_suivies()
        reseau_ids = {l["id"] for l in self.reseau}
        a_part = [lid for lid in suivies if lid not in reseau_ids]
        resultats = await asyncio.gather(*(self.client.perturbations_ligne(lid) for lid in a_part), return_exceptions=True)
        for lid, r in zip(a_part, resultats, strict=True):
            if isinstance(r, Exception):
                _LOGGER.debug("Info trafic %s : %s", lid, r)
                continue
            for p in r:
                par_id.setdefault(p["id"], p)
                par_ligne.setdefault(lid, set()).add(p["id"])
        index_id = {l["id"]: l for l in self.reseau}
        for lid, l in suivies.items():
            if lid in vues:
                for x in lignes:
                    if x["id"] == lid:
                        x["interet"] = x.get("interet") or l.get("interet", False)
                continue
            vues.add(lid)
            meta = {**l, **{k: v for k, v in index_id.get(lid, {}).items() if v}}
            lignes.append({**meta, "groupe": groupe_ligne(meta), "base": False, "interet": l.get("interet", False)})

        self.niveaux, self.par_ligne = {}, {}
        ignorer_info = option(self.config_entry, OPT_IGNORER_INFO)
        maintenant_local = dt_util.now().replace(tzinfo=None)
        suivis = self._arrets_suivis()
        for l in lignes:
            ps = [par_id[i] for i in par_ligne.get(l["id"], set()) if i in par_id and concerne_ligne(par_id[i], l["id"])]
            self.par_ligne[l["id"]] = ps
            # Panneau de la ligne : messages en cours ; les équipements (ascenseurs…) seulement aux arrêts suivis.
            en_cours = [p for p in ps if active_a(p, maintenant_local) and (not p.get("equipement") or set(p.get("arrets") or []) & suivis)]
            l["perturbations"] = messages_affiches(en_cours, ignorer_info)
            l["etat"] = pire_niveau(m["niveau"] for m in l["perturbations"] if m["niveau"] != "equipement")
            self.niveaux[l["id"]] = l["etat"]
        return lignes
