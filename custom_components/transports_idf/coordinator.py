"""Coordinateurs : prochains passages (arrêts) et info trafic (trajets)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ClientPrim,
    ErreurJeton,
    ErreurPrim,
    date_navitia,
    niveau_perturbation,
    pire_niveau,
    refs_candidates,
)
from .const import (
    A_LIGNES,
    A_LIGNES_SUIVIES,
    A_MARCHE,
    A_REFS,
    CONF_ZONE,
    DEFAUTS,
    DOMAIN,
    ECART_MINUTES,
    EFFETS_BLOQUANTS,
    ETAT_ATTEIGNABLE,
    ETAT_PRESSE,
    ETAT_TROP_TARD,
    EVENEMENT_ALERTE,
    LIGNES_BASE,
    OPT_DEBUT_NUIT,
    OPT_FIN_NUIT,
    OPT_IGNORER_INFO,
    OPT_INTERVALLE_JOUR,
    OPT_INTERVALLE_NUIT,
    OPT_LIGNES_INTERET,
    OPT_MARGE,
    SOUS_ARRET,
    SOUS_TRAJET,
    T_AVANCE,
    T_DEPART_HABITUEL,
    T_DEST_ID,
    T_DUREE,
    T_ETAPES,
    T_HEURE,
    T_NOM,
    T_ORIGINE,
    T_ORIGINE_ID,
    T_PERSONNE,
    T_RESUME,
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



@dataclass
class EtatTrajet:
    perturbations: list[dict[str, Any]] = field(default_factory=list)
    niveaux: dict[str, str] = field(default_factory=dict)  # ligne → interrompu / perturbe / travaux
    rechange: dict[str, Any] | None = None
    reference: dict[str, Any] | None = None
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    calcule_le: datetime | None = None
    en_veille: bool = False
    notifiees: set[str] = field(default_factory=set)
    conseil: dict[str, Any] | None = None  # mode « arrivée » : itinéraire à prendre au plus tard
    depart_notifie: datetime | None = None
    bloquees: list[str] = field(default_factory=list)
    maintenant: dict[str, Any] | None = None  # résultat de « Partir maintenant »


class Itineraires:
    """Calculs d'itinéraires partagés : trajets, « Partir maintenant », recherche de la carte."""

    def __init__(self, hass: HomeAssistant, client: ClientPrim) -> None:
        self.hass = hass
        self.client = client
        self._cache: dict[tuple, tuple[datetime, list[datetime]]] = {}
        self.niveau_ligne = lambda ligne_id: None  # remplacé par l'info trafic
        self.rame = None  # PositionRame, branchée au démarrage

    async def suivants(self, its: list[dict[str, Any]]) -> None:
        """Ajoute à chaque étape en transport les départs suivants (minutes après le départ visé)."""
        taches, cibles = [], []
        for it in its:
            for e in it.get("etapes") or []:
                if e.get("type") != "transport":
                    continue
                e["alerte"] = self.niveau_ligne(e.get("ligne_id"))
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

    def alertes(self, its: list[dict[str, Any]], niveaux: dict[str, str]) -> None:
        """État des lignes de chaque étape, et où monter dans la rame quand on le sait."""
        for it in its:
            etapes = it.get("etapes") or []
            for n, e in enumerate(etapes):
                if e.get("type") != "transport":
                    continue
                e["alerte"] = niveaux.get(e.get("ligne_id")) or self.niveau_ligne(e.get("ligne_id"))
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


class TraficCoordinator(DataUpdateCoordinator[dict[str, EtatTrajet]]):
    """Info trafic des lignes des trajets, itinéraires en direct et alertes pendant la veille."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ClientPrim, calcul: Itineraires) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} trafic", update_interval=TRAFIC_REPOS)
        self.client = client
        self.calcul = calcul
        self.etats: dict[str, EtatTrajet] = {}

    def _trajets(self):
        return [s for s in self.config_entry.subentries.values() if s.subentry_type == SOUS_TRAJET]

    @staticmethod
    def fenetre(heure: str, avance: int, maintenant: datetime) -> bool:
        """Vrai si maintenant est dans [départ − avance ; départ + 10 min] (heure locale)."""
        depart = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
        h = _heure(heure)
        for jour in (-1, 0, 1):  # gère une veille qui chevauche minuit
            d = depart + timedelta(days=jour, hours=h.hour, minutes=h.minute)
            if d - timedelta(minutes=avance) <= maintenant <= d + APRES_DEPART:
                return True
        return False

    async def _async_update_data(self) -> dict[str, EtatTrajet]:
        trajets = self._trajets()
        maintenant = dt_util.now()
        lignes = sorted({sec["ligne_id"] for t in trajets for sec in t.data.get(T_SECTIONS, []) if sec.get("ligne_id")})
        resultats = await asyncio.gather(*(self.client.perturbations_ligne(l) for l in lignes), return_exceptions=True)
        if any(isinstance(r, ErreurJeton) for r in resultats):
            raise ConfigEntryAuthFailed("Jeton PRIM refusé")
        if lignes and all(isinstance(r, Exception) for r in resultats):
            raise UpdateFailed(f"Info trafic indisponible : {resultats[0]}")
        par_ligne: dict[str, list[dict[str, Any]]] = {}
        for l, r in zip(lignes, resultats, strict=True):
            if isinstance(r, ErreurPrim):
                _LOGGER.debug("Info trafic %s : %s", l, r)
                continue
            if isinstance(r, Exception):
                raise r
            par_ligne[l] = r

        ignorer_info = option(self.config_entry, OPT_IGNORER_INFO)
        une_veille = False
        for t in trajets:
            etat = self.etats.setdefault(t.subentry_id, EtatTrajet())
            noms = {sec["ligne_id"]: sec for sec in t.data.get(T_SECTIONS, []) if sec.get("ligne_id")}
            perts: dict[str, dict[str, Any]] = {}
            niveaux: dict[str, list] = {}
            for lid, sec in noms.items():
                for p in par_ligne.get(lid, []):
                    niveau = niveau_perturbation(p)
                    if ignorer_info and niveau in (None, "travaux") and p["information"]:
                        continue
                    niveaux.setdefault(lid, []).append(niveau)
                    d = perts.setdefault(p["id"], {**p, "niveau": niveau, "lignes_trajet": [], "_ids": []})
                    nom = f"{sec.get('mode') or ''} {sec.get('nom') or ''}".strip()
                    if nom not in d["lignes_trajet"]:
                        d["lignes_trajet"].append(nom)
                        d["_ids"].append(lid)
            bloquees = sorted({lid for p in perts.values() if p["effet"] in EFFETS_BLOQUANTS for lid in p["_ids"]})
            etat.perturbations = [{k: v for k, v in p.items() if k != "_ids"} for p in perts.values()]
            etat.niveaux = {lid: pire_niveau(n) for lid, n in niveaux.items()}
            etat.bloquees = bloquees

            etat.en_veille = self.fenetre(depart_habituel(t.data), int(t.data.get(T_AVANCE, 60)), maintenant)
            une_veille |= etat.en_veille
            # Itinéraires en direct : à chaque rafraîchissement pendant la veille,
            # toutes les 10 min hors veille si une ligne du trajet est bloquée.
            if etat.en_veille or (bloquees and (etat.calcule_le is None or maintenant - etat.calcule_le > VALIDITE_RECHANGE)):
                await self._comparer(t, etat, bloquees)
                etat.calcule_le = maintenant
            elif not etat.en_veille and not bloquees:
                etat.rechange, etat.reference, etat.alternatives, etat.calcule_le, etat.conseil = None, None, [], None, None
            self._alerter(t, etat)

        for sid in list(self.etats):
            if sid not in {t.subentry_id for t in trajets}:
                del self.etats[sid]
        self.update_interval = TRAFIC_VEILLE if une_veille else TRAFIC_REPOS
        return dict(self.etats)

    def etapes_habituelles(self, sous_id: str) -> list[dict[str, Any]]:
        """Étapes enregistrées de l'itinéraire préféré, avec l'état de chaque ligne (résumé replié)."""
        sous = self.config_entry.subentries[sous_id]
        etat = self.etats.get(sous_id) or EtatTrajet()
        etapes = [dict(e) for e in sous.data.get(T_ETAPES) or []]
        if not etapes:  # trajet créé avant la 0.4.0 : on se contente des lignes
            etapes = [{"type": "transport", **{k: s.get(k) for k in ("nom", "mode", "couleur", "couleur_texte", "ligne_id", "de", "vers", "direction")}} for s in sous.data.get(T_SECTIONS, [])]
        for e in etapes:
            if e.get("type") == "transport":
                e["alerte"] = etat.niveaux.get(e.get("ligne_id")) or self.calcul.niveau_ligne(e.get("ligne_id"))
        return etapes

    async def partir_maintenant(self, sous_id: str) -> dict[str, Any]:
        """Itinéraires au départ de maintenant pour un trajet, gardés DUREE_MAINTENANT pour la carte."""
        sous = self.config_entry.subentries[sous_id]
        etat = self.etats.setdefault(sous_id, EtatTrajet())
        d = sous.data
        depuis, vers = origine_navitia(self.hass, self.config_entry, d), d[T_DEST_ID]
        quand = dt_util.now().replace(tzinfo=None)
        lignes_ref = [s["ligne_id"] for s in d.get(T_SECTIONS, []) if s.get("ligne_id")]
        bloquees = etat.bloquees
        appels = [self.client.itineraires(depuis, vers, quand, bloquees)]
        if lignes_ref:
            appels.append(self.client.itineraires(depuis, vers, quand, autorises=lignes_ref))
        resultats = await asyncio.gather(*appels, return_exceptions=True)
        if isinstance(resultats[0], ErreurJeton):
            raise HomeAssistantError("Jeton PRIM refusé")
        if isinstance(resultats[0], Exception):
            raise HomeAssistantError(f"Calculateur PRIM indisponible : {resultats[0]}")
        options = resultats[0]
        refs = resultats[1] if len(resultats) > 1 and isinstance(resultats[1], list) else []
        habituel = min((o for o in refs + options if _lignes(o) == lignes_ref), key=lambda o: _arrivee(o) or datetime.max, default=None)
        autres = [o for o in options if _lignes(o) != lignes_ref and not set(_lignes(o)) & set(bloquees)]
        liste = ([{**habituel, "habituel": True}] if habituel else []) + [{**o, "habituel": False} for o in autres]
        vus, tries = set(), []
        for o in sorted(liste, key=lambda o: (not o["habituel"], _arrivee(o) or datetime.max)):
            cle = (o.get("depart"), o.get("resume"))
            if cle not in vus:
                vus.add(cle)
                tries.append(o)
        tries = tries[:4]
        self.calcul.alertes(tries, etat.niveaux)
        await self.calcul.suivants(tries)
        etat.maintenant = {"le": dt_util.utcnow().isoformat(), "itineraires": tries, "perturbe": bool(etat.perturbations)}
        self.async_update_listeners()
        return etat.maintenant

    async def _comparer(self, trajet, etat: EtatTrajet, bloquees: list[str]) -> None:
        """Préféré recalculé (même perturbé), alternatives, rechange et conseil de départ.

        Mode « départ » : itinéraires au départ de maintenant, comparés sur l'heure d'arrivée.
        Mode « arrivée » : itinéraires arrivant au plus tard à l'heure cible, comparés sur l'heure
        de départ (plus tard = mieux) ; le conseil est le départ le plus tardif qui arrive à l'heure.
        """
        d = trajet.data
        arrivee_cible = d.get(T_TYPE_HEURE) == "arrivee"
        depuis, vers = origine_navitia(self.hass, self.config_entry, d), d[T_DEST_ID]
        maintenant = dt_util.now().replace(tzinfo=None)
        if arrivee_cible:
            h = _heure(d[T_HEURE])
            quand = maintenant.replace(hour=h.hour, minute=h.minute, second=0, microsecond=0)
            if quand < maintenant:
                quand += timedelta(days=1)
            represente = "arrival"
        else:
            quand, represente = maintenant, "departure"
        lignes_ref = [s["ligne_id"] for s in d.get(T_SECTIONS, []) if s.get("ligne_id")]
        appels = [self.client.itineraires(depuis, vers, quand, bloquees, represente=represente)]
        if lignes_ref:  # le préféré est toujours recalculé, même si une de ses lignes est perturbée
            appels.append(self.client.itineraires(depuis, vers, quand, autorises=lignes_ref, represente=represente))
        resultats = await asyncio.gather(*appels, return_exceptions=True)
        for r in resultats:
            if isinstance(r, Exception):
                _LOGGER.debug("Itinéraires du trajet %s : %s", trajet.title, r)
        options = resultats[0] if isinstance(resultats[0], list) else []
        refs = resultats[1] if len(resultats) > 1 and isinstance(resultats[1], list) else []

        habituels = [o for o in refs + options if _lignes(o) == lignes_ref]
        if arrivee_cible:
            habituels = [o for o in habituels if _arrivee(o) and _arrivee(o) <= quand]
            etat.reference = max(habituels, key=_depart, default=None)
        else:
            etat.reference = min(habituels, key=lambda o: _arrivee(o) or datetime.max, default=None)
        autres = [o for o in options if _lignes(o) != lignes_ref and not set(_lignes(o)) & set(bloquees) and o["sections"]]
        if arrivee_cible:
            autres = [o for o in autres if _arrivee(o) and _arrivee(o) <= quand]
            autres.sort(key=_depart, reverse=True)
        else:
            autres.sort(key=lambda o: _arrivee(o) or datetime.max)

        etat.rechange = (autres[0] if autres else {"resume": "Aucun itinéraire de rechange trouvé", "etapes": [], "texte": ""}) if bloquees else None

        # Alternatives proposées même quand tout va bien ; « gain » en minutes (négatif = moins bien).
        ref = etat.reference
        etat.alternatives = []
        for o in autres[:3]:
            gain = None
            if ref:
                if arrivee_cible and _depart(o) and _depart(ref):
                    gain = round((_depart(o) - _depart(ref)).total_seconds() / 60)
                elif not arrivee_cible and _arrivee(o) and _arrivee(ref):
                    gain = round((_arrivee(ref) - _arrivee(o)).total_seconds() / 60)
            etat.alternatives.append({**o, "gain": gain, "plus_rapide": gain is not None and gain >= ECART_MINUTES})

        etat.conseil = None
        if arrivee_cible:
            candidats = ([ref] if ref and ref.get("_ok", True) and not set(_lignes(ref)) & set(bloquees) else []) + autres
            meilleur = max(candidats, key=_depart, default=None)
            cible = f"{quand.hour} h {quand.minute:02d}"
            if meilleur:
                habituel = date_navitia(quand.strftime("%Y%m%dT") + (d.get(T_DEPART_HABITUEL) or "00:00:00").replace(":", ""))
                avance = round((habituel - _depart(meilleur)).total_seconds() / 60) if d.get(T_DEPART_HABITUEL) and _depart(meilleur) else 0
                etat.conseil = {**meilleur, "habituel": meilleur is ref, "arrivee_cible": cible, "avance": avance}
            else:
                etat.conseil = {"resume": "Aucun itinéraire n'arrive à l'heure", "etapes": [], "texte": "", "arrivee_cible": cible, "avance": None, "impossible": True}

        affiches = [i for i in [etat.reference, etat.rechange, etat.conseil, *etat.alternatives] if i and i.get("etapes")]
        self.calcul.alertes(affiches, etat.niveaux)
        await self.calcul.suivants(affiches)

    def _alerter(self, trajet, etat: EtatTrajet) -> None:
        if not etat.en_veille:
            etat.notifiees = set()  # hors veille : on repart à zéro, sans rien envoyer
            etat.depart_notifie = None
            return
        ids = {p["id"] for p in etat.perturbations}
        nouvelles = ids - etat.notifiees
        type_ = None
        if nouvelles:
            type_ = "perturbation"
        elif not ids and etat.notifiees:
            type_ = "retour_normal"
        etat.notifiees = ids
        if type_ is not None:
            self._envoyer(trajet, etat, type_, nouvelles)
        # Mode « arrivée » : il faut partir plus tôt que d'habitude (ou c'est devenu impossible).
        c = etat.conseil
        if c and (c.get("impossible") or (c.get("avance") or 0) >= ECART_MINUTES):
            dep = datetime.min if c.get("impossible") else date_navitia(c.get("depart"))
            if etat.depart_notifie is None or (dep and dep <= etat.depart_notifie - timedelta(minutes=ECART_MINUTES)):
                etat.depart_notifie = dep
                self._envoyer(trajet, etat, "depart_anticipe", set())

    def _envoyer(self, trajet, etat: EtatTrajet, type_: str, nouvelles: set[str]) -> None:
        donnees = {
            "type": type_,
            "trajet": trajet.data.get(T_NOM) or trajet.title,
            "personne": trajet.data.get(T_PERSONNE),
            "heure": trajet.data.get(T_HEURE),
            "type_heure": trajet.data.get(T_TYPE_HEURE, "depart"),
            "depart_habituel": depart_habituel(trajet.data),
            "conseil": etat.conseil,
            "itineraire": trajet.data.get(T_RESUME),
            "nouvelles": [p for p in etat.perturbations if p["id"] in nouvelles],
            "perturbations": etat.perturbations,
            "rechange": etat.rechange,
            "reference": etat.reference,
            "alternatives": etat.alternatives,
            "sous_entree": trajet.subentry_id,
        }
        self.hass.bus.async_fire(EVENEMENT_ALERTE, donnees)


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

    def niveau_ligne(self, ligne_id: str | None) -> str | None:
        n = self.niveaux.get(ligne_id or "")
        return n if n and n != "normal" else None

    def _lignes_suivies(self) -> dict[str, dict[str, Any]]:
        """Lignes ajoutées par l'utilisateur, ou utilisées par ses trajets et ses arrêts."""
        sortie: dict[str, dict[str, Any]] = {}
        for l in option(self.config_entry, OPT_LIGNES_INTERET) or []:
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

        self.niveaux = {}
        for l in lignes:
            ps = [par_id[i] for i in par_ligne.get(l["id"], set()) if i in par_id]
            ps = [{**p, "niveau": niveau_perturbation(p)} for p in ps]
            ps = [p for p in ps if p["niveau"]]
            ps.sort(key=lambda p: -{"interrompu": 3, "perturbe": 2, "travaux": 1}.get(p["niveau"], 0))
            l["perturbations"] = [{k: p.get(k) for k in ("id", "titre", "message", "niveau", "cause", "fin")} for p in ps]
            l["etat"] = pire_niveau(p["niveau"] for p in ps)
            self.niveaux[l["id"]] = l["etat"]
        return lignes
