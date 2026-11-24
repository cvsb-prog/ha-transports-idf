"""Trajets préférés : itinéraires au départ de maintenant, trajet nominal, rappel, détour, alertes.

Principes :
- les propositions affichées sont toujours calculées au départ de maintenant (veille et à la demande) ;
- le « nominal » est l'itinéraire préféré pour la prochaine occurrence du trajet (jour et heure prévus),
  recalculé une fois par jour, et en temps réel pendant la veille ;
- l'heure du trajet (arrivée cible ou départ) ne sert qu'au rappel « pars à … » et à la veille ;
- un détour (lieu, mode, temps sur place, heure d'arrivée imposée, jours) peut précéder les transports.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ClientPrim,
    ErreurJeton,
    ErreurPrim,
    concerne_ligne,
    date_navitia,
    heure_navitia,
    messages_affiches,
    niveau_perturbation,
    pertinente,
    pire_niveau,
    resume_itineraire,
    texte_itineraire,
)
from .const import (
    DOMAIN,
    ECART_MINUTES,
    RAPPEL_MINUTES,
    EFFETS_BLOQUANTS,
    EVENEMENT_ALERTE,
    JOUR_FERIE,
    JOURS_DEFAUT,
    MODES_DETOUR,
    OPT_IGNORER_INFO,
    SOUS_TRAJET,
    T_DEST_ID,
    T_DET_DUREE,
    T_DET_HEURE,
    T_DUREE,
    T_DET_ID,
    T_DET_JOURS,
    T_DET_MODE,
    T_DET_NOM,
    T_DET_PAUSE,
    T_ETAPES,
    T_HEURE,
    T_JOURS,
    T_NOM,
    T_PERSONNE,
    T_RESUME,
    T_SECTIONS,
    T_TYPE_HEURE,
    VEILLE_MINUTES,
)
from .coordinator import Itineraires, _heure, coordonnees_zone, option, origine_navitia
from .planning import INF, besoin_planning, bornes_point, minutes, planifier, points_du_jour
from .points import points_du_trajet

_LOGGER = logging.getLogger(__name__)

VEILLE = timedelta(minutes=2)
REPOS = timedelta(minutes=20)
APRES_DEPART = timedelta(minutes=10)
FRAICHEUR_DIRECT = timedelta(seconds=60)  # « à la demande » : pas plus d'un calcul par minute
DUREE_DIRECT = timedelta(minutes=15)  # durée d'affichage d'un calcul à la demande hors veille
NB_DIRECT = 4


def _fmt(d: datetime) -> str:
    return d.strftime("%Y%m%dT%H%M%S")


def _h(d: datetime | None) -> str | None:
    return f"{d.hour} h {d.minute:02d}" if d else None


def _lignes(o: dict[str, Any]) -> list[str]:
    return [s["ligne_id"] for s in o.get("sections", []) if s.get("ligne_id")]


def _dep(o: dict[str, Any] | None) -> datetime | None:
    return date_navitia((o or {}).get("depart"))


def _arr(o: dict[str, Any] | None) -> datetime | None:
    return date_navitia((o or {}).get("arrivee"))


def sections_trajet(d: dict[str, Any]) -> list[dict[str, Any]]:
    """Sections (lignes) de tous les tronçons en transports du trajet, sinon de l'itinéraire habituel enregistré."""
    if d.get("points") and besoin_planning(d["points"]):
        return [s for p in d["points"][1:] if (p.get("mode") or "transports") == "transports" for s in p.get("sections") or []]
    return list(d.get(T_SECTIONS) or [])


def etapes_trajet(d: dict[str, Any]) -> list[dict[str, Any]]:
    """Étapes habituelles (résumé replié de la carte) de tous les tronçons."""
    if d.get("points") and besoin_planning(d["points"]):
        return [e for p in d["points"][1:] for e in (p.get("habituel") or {}).get("etapes") or []]
    return list(d.get(T_ETAPES) or [])


def jours_trajet(d: dict[str, Any]) -> set[int]:
    return {int(j) for j in (d.get(T_JOURS) or JOURS_DEFAUT) if str(j).isdigit()}


def aussi_feries(d: dict[str, Any]) -> bool:
    """Option « Jours fériés » cochée : le trajet a lieu aussi les jours fériés (parmi les jours cochés)."""
    return JOUR_FERIE in (d.get(T_JOURS) or [])


def jour_du_trajet(d: dict[str, Any], jour: date, feries: set[date] | frozenset = frozenset()) -> bool:
    return jour.weekday() in jours_trajet(d) and (jour not in feries or aussi_feries(d))


async def jours_feries(hass: HomeAssistant, debut: date, nombre: int = 9) -> set[date]:
    """Jours fériés à venir, d'après l'intégration Workday (sa liste, ses exceptions), sinon le calendrier français."""
    jours = [debut + timedelta(days=n) for n in range(nombre)]
    feries: set[date] = set()
    registre = er.async_get(hass)
    capteur = next((e.entity_id for e in registre.entities.values() if e.platform == "workday" and e.domain == "binary_sensor" and not e.disabled), None)
    ouvres: set[str] = set()
    etat = hass.states.get(capteur) if capteur else None
    # Au démarrage, Workday peut ne pas être encore chargé : l'appeler ferait journaliser une erreur
    # par Home Assistant (« Referenced entities … are missing ») ; on s'en tient alors au calendrier.
    if etat and etat.state not in ("unavailable", "unknown") and hass.services.has_service("workday", "check_date"):
        ouvres = set((etat.attributes.get("workdays") if etat else None) or ["mon", "tue", "wed", "thu", "fri"])
        noms = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        for j in jours:
            if noms[j.weekday()] not in ouvres:
                continue
            try:
                rep = await hass.services.async_call(
                    "workday", "check_date", {"check_date": j.isoformat()}, target={"entity_id": capteur}, blocking=True, return_response=True
                )
            except Exception as err:  # noqa: BLE001 — Workday indisponible : on passe au calendrier
                _LOGGER.debug("Workday check_date %s : %s", j, err)
                ouvres = set()
                break
            if (rep or {}).get(capteur, {}).get("workday") is False:
                feries.add(j)
    # Jours que Workday ne juge pas (week-end) ou sans Workday : calendrier français.
    noms = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    calendrier = set().union(*(feries_france(a) for a in {j.year for j in jours}))
    feries |= {j for j in jours if j in calendrier and noms[j.weekday()] not in ouvres}
    return feries


def feries_france(annee: int) -> set[date]:
    """Jours fériés nationaux (métropole) : dates fixes et fêtes mobiles calculées depuis Pâques."""
    a, b, c = annee % 19, annee // 100, annee % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mois = (h + l - 7 * m + 114) // 31
    paques = date(annee, mois, (h + l - 7 * m + 114) % 31 + 1)
    fixes = {(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)}
    return {date(annee, mo, j) for mo, j in fixes} | {paques + timedelta(days=n) for n in (1, 39, 50)}


def avec_detour(d: dict[str, Any], jour: date) -> bool:
    jours = d.get(T_DET_JOURS)
    return bool(d.get(T_DET_ID)) and str(jour.weekday()) in set(JOURS_DEFAUT if jours is None else jours)


def heure_depart_maison(d: dict[str, Any]) -> str:
    """Heure habituelle de départ de la maison « HH:MM:SS » (veille), d'après le nominal enregistré."""
    if d.get(T_TYPE_HEURE) != "arrivee":
        return d[T_HEURE]
    if d.get("depart_habituel"):
        return d["depart_habituel"]
    h = _heure(d[T_HEURE])
    debut = datetime(2000, 1, 2, h.hour, h.minute) - timedelta(seconds=int(d.get("duree") or 3600))
    return debut.strftime("%H:%M:%S")


@dataclass
class EtatTrajet:
    perturbations: list[dict[str, Any]] = field(default_factory=list)
    niveaux: dict[str, str] = field(default_factory=dict)
    bloquees: list[str] = field(default_factory=list)
    en_veille: bool = False
    nominal: dict[str, Any] | None = None  # itinéraire préféré pour la prochaine occurrence
    nominal_jour: date | None = None
    nominal_le: datetime | None = None
    direct: list[dict[str, Any]] = field(default_factory=list)  # au départ de maintenant
    direct_le: datetime | None = None
    rappel: dict[str, Any] | None = None  # « pars à … » pour l'occurrence du jour
    rechange: dict[str, Any] | None = None
    notifiees: set[str] = field(default_factory=set)
    depart_notifie: datetime | None = None
    rappel_envoye: datetime | None = None  # départ pour lequel « pars dans 10 min » est parti
    # Départ supposé respecté (« pars à » envoyé, ou heure passée) : la suite est calculée à partir de lui.
    fige: dict[str, Any] | None = None
    ids_fige: frozenset[str] | None = None  # perturbations au dernier calcul « en route »
    fige_le: datetime | None = None  # heure du dernier calcul « en route »
    prevision_notifiee: tuple | None = None  # dernière prévision envoyée (départ, arrivée, écarts)


class TraficCoordinator(DataUpdateCoordinator[dict[str, EtatTrajet]]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ClientPrim, calcul: Itineraires) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} trajets", update_interval=REPOS)
        self.client = client
        self.calcul = calcul
        self.etats: dict[str, EtatTrajet] = {}
        self.feries: set[date] = set()
        self._feries_le: date | None = None
        self._par_ligne: dict[str, list[dict[str, Any]]] = {}

    def _trajets(self):
        return [s for s in self.config_entry.subentries.values() if s.subentry_type == SOUS_TRAJET]

    # ---- Calendrier ---------------------------------------------------------
    @staticmethod
    def fenetre(heure: str, avance: int, maintenant: datetime, jours: set[int] | None = None, exclus: set[date] | frozenset = frozenset(),
                apres: timedelta = APRES_DEPART) -> bool:
        """Vrai si maintenant est dans [départ − avance ; départ + apres] (heure locale), un jour du trajet (hors jours exclus)."""
        base = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
        h = _heure(heure)
        for decalage in (-1, 0, 1):
            d = base + timedelta(days=decalage, hours=h.hour, minutes=h.minute)
            if (jours is not None and d.weekday() not in jours) or d.date() in exclus:
                continue
            if d - timedelta(minutes=avance) <= maintenant <= d + apres:
                return True
        return False

    def prochaine_occurrence(self, d: dict[str, Any], maintenant: datetime) -> date:
        """Jour de la prochaine occurrence du trajet (aujourd'hui tant que le départ habituel n'est pas passé)."""
        h = _heure(heure_depart_maison(d))
        for n in range(8):
            jour = (maintenant + timedelta(days=n)).date()
            if not jour_du_trajet(d, jour, self.feries):
                continue
            if n == 0 and maintenant > datetime.combine(jour, h) + APRES_DEPART:
                continue
            return jour
        return maintenant.date()

    # ---- Plans --------------------------------------------------------------
    async def _options(self, depuis: str, vers: str, quand: datetime, represente: str, lignes: list[str] | None, bloquees: list[str]) -> list[dict[str, Any]]:
        """Itinéraires du calculateur : lignes préférées seulement (lignes) ou toutes sauf les bloquées."""
        try:
            if lignes:
                its = await self.client.itineraires(depuis, vers, quand, autorises=lignes, represente=represente)
                exacts = [o for o in its if _lignes(o) == lignes]
                return exacts or [o for o in its if set(_lignes(o)) <= set(lignes) and o["sections"]]
            its = await self.client.itineraires(depuis, vers, quand, bloquees, represente=represente)
            return [o for o in its if not set(_lignes(o)) & set(bloquees)]
        except ErreurJeton as err:
            raise ConfigEntryAuthFailed("Jeton PRIM refusé") from err
        except ErreurPrim as err:
            _LOGGER.debug("Itinéraires %s → %s : %s", depuis, vers, err)
            return []

    async def _aller_detour(self, d: dict[str, Any], depuis: str, quand: datetime, represente: str, bloquees: list[str]):
        """Trajet maison → détour : (départ, arrivée, étapes, sections) ou None."""
        mode = d.get(T_DET_MODE) or "marche"
        if mode != "transports":
            duree = timedelta(minutes=float(d.get(T_DET_DUREE) or 0))
            dep, arr = (quand, quand + duree) if represente == "departure" else (quand - duree, quand)
            etape = {"type": "detour", "mode": mode, "duree": round(duree.total_seconds() / 60), "vers": d.get(T_DET_NOM)}
            # Tracé du détour pour la carte du trajet (la durée reste celle réglée dans le trajet).
            try:
                trace = await self.client.trace_directe(depuis, d[T_DET_ID], MODES_DETOUR.get(mode, "walking"))
            except ErreurJeton as err:
                raise ConfigEntryAuthFailed("Jeton PRIM refusé") from err
            except ErreurPrim as err:
                _LOGGER.debug("Tracé du détour %s → %s : %s", depuis, d.get(T_DET_ID), err)
                trace = None
            if trace:
                etape.update(trace=trace["trace"], de_coord=trace["de"], vers_coord=trace["vers"])
            return dep, arr, [etape], []
        options = await self._options(depuis, d[T_DET_ID], quand, represente, None, bloquees)
        options = [o for o in options if _dep(o) and _arr(o)]
        if not options:
            return None
        o = min(options, key=_arr) if represente == "departure" else max(options, key=_dep)
        etapes = [{**e, "vers": e.get("vers") or d.get(T_DET_NOM)} for e in o["etapes"]]
        return _dep(o), _arr(o), etapes, o["sections"]

    def _assembler(self, d, depart: datetime, prefixe: list, sections_prefixe: list, reprise: datetime | None, suite: dict[str, Any]) -> dict[str, Any]:
        etapes = list(prefixe)
        if reprise is not None:
            etapes.append({"type": "pause", "duree": int(float(d.get(T_DET_PAUSE) or 0)), "lieu": d.get(T_DET_NOM), "reprise": _h(reprise)})
        etapes += suite["etapes"]
        arrivee = _arr(suite)
        sections = sections_prefixe + suite["sections"]
        return {
            **{k: suite.get(k) for k in ("correspondances", "marche")},
            "depart": _fmt(depart),
            "arrivee": suite.get("arrivee"),
            "depart_h": _h(depart),
            "arrivee_h": heure_navitia(suite.get("arrivee")),
            "duree": int((arrivee - depart).total_seconds()) if arrivee else suite.get("duree"),
            "etapes": etapes,
            "sections": sections,
            "resume": resume_itineraire(sections, int((arrivee - depart).total_seconds()) if arrivee else None, len(sections) - 1 if sections else None),
            "texte": texte_itineraire(etapes, _fmt(depart), suite.get("arrivee")),
            "detour": reprise is not None,
        }

    async def _plans(self, t, jour: date, quand: datetime | None, lignes: list[str] | None, bloquees: list[str], nombre: int = 1, pas_avant: bool = False) -> list[dict[str, Any]]:
        """Itinéraires complets (détour compris) pour un jour donné.

        quand=None : on suit l'heure du trajet (arrivée cible ou départ) — nominal et rappel.
        quand=datetime : départ de la maison à cette heure — calcul au départ de maintenant.
        pas_avant (trajet à étapes) : quand n'est qu'une borne ; si les horaires du jour font partir plus
        tard, on les garde (sinon, au départ de maintenant on attendrait des heures à une étape).
        """
        d = t.data
        if d.get("points") and besoin_planning(d["points"]):
            preferees, bloq = lignes is not None, [] if lignes is not None else bloquees
            if pas_avant and quand is not None:
                prevus = await self._plans_etapes(t, jour, None, preferees, bloq, nombre)
                if prevus and date_navitia(prevus[0]["depart"]) >= quand - timedelta(minutes=1):
                    return prevus
            return await self._plans_etapes(t, jour, quand, preferees, bloq, nombre)
        depuis, vers = origine_navitia(self.hass, self.config_entry, d), d[T_DEST_ID]
        heure = _heure(d[T_HEURE])
        cible = datetime.combine(jour, heure)
        arrivee_cible = quand is None and d.get(T_TYPE_HEURE) == "arrivee"
        depart_maison = quand or (None if arrivee_cible else cible)
        detour = avec_detour(d, jour)
        heure_detour = datetime.combine(jour, _heure(d[T_DET_HEURE])) if detour and d.get(T_DET_HEURE) else None
        if detour and quand is not None and heure_detour and quand > heure_detour:
            detour = False  # heure du détour passée : on part directement
            heure_detour = None
        pause = timedelta(minutes=float(d.get(T_DET_PAUSE) or 0))
        sortie: list[dict[str, Any]] = []

        if not detour:
            if arrivee_cible:
                its = [o for o in await self._options(depuis, vers, cible, "arrival", lignes, bloquees) if _arr(o) and _arr(o) <= cible]
                its.sort(key=_dep, reverse=True)
            else:
                its = await self._options(depuis, vers, depart_maison, "departure", lignes, bloquees)
                its.sort(key=lambda o: _arr(o) or datetime.max)
            for o in its[:nombre]:
                sortie.append(self._assembler(d, _dep(o), [], [], None, o))
            return sortie

        # Avec détour : on fixe d'abord l'heure de reprise depuis le détour.
        if heure_detour is not None:
            aller = await self._aller_detour(d, depuis, heure_detour if quand is None else quand, "arrival" if quand is None else "departure", bloquees)
            if aller is None:
                return []
            dep_maison, arr_detour, prefixe, sec = aller
            reprise = max(arr_detour, heure_detour) + pause
            suites = await self._options(d[T_DET_ID], vers, reprise, "departure", lignes, bloquees)
            suites.sort(key=lambda o: _arr(o) or datetime.max)
        elif arrivee_cible:
            suites = [o for o in await self._options(d[T_DET_ID], vers, cible, "arrival", lignes, bloquees) if _arr(o) and _arr(o) <= cible]
            suites.sort(key=_dep, reverse=True)
            if not suites:
                return []
            reprise = _dep(suites[0])
            aller = await self._aller_detour(d, depuis, reprise - pause, "arrival", bloquees)
            if aller is None:
                return []
            dep_maison, arr_detour, prefixe, sec = aller
        else:
            aller = await self._aller_detour(d, depuis, depart_maison, "departure", bloquees)
            if aller is None:
                return []
            dep_maison, arr_detour, prefixe, sec = aller
            reprise = arr_detour + pause
            suites = await self._options(d[T_DET_ID], vers, reprise, "departure", lignes, bloquees)
            suites.sort(key=lambda o: _arr(o) or datetime.max)
        for o in suites[:nombre]:
            if _dep(o) and _dep(o) < reprise - timedelta(minutes=1) and heure_detour is None and not arrivee_cible:
                continue
            sortie.append(self._assembler(d, dep_maison, prefixe, sec, reprise, o))
        return sortie

    # ---- Mise à jour --------------------------------------------------------
    # ---- Trajets à étapes -------------------------------------------------------
    def _navitia_point(self, p: dict[str, Any]) -> str:
        if p.get("id"):
            return p["id"]
        lat, lon = coordonnees_zone(self.hass, p.get("zone") or self.config_entry.data.get("zone"))
        return f"{lon};{lat}"

    async def _simuler(self, points, jour: date, depart: datetime, preferees: bool, bloquees: list[str], nombre: int = 1):
        """Trajet réel au départ de « depart » : chaque tronçon calculé par PRIM (transports) ou à durée réglée.

        Retourne une liste (une par option du dernier tronçon en transports) de [segments], chaque segment
        {i, depart, arrivee, option | etape}, ou [] si un tronçon en transports n'a pas d'itinéraire.
        """
        n = len(points)
        navitia = [self._navitia_point(p) for p in points]
        derniers = max((i for i in range(1, n) if (points[i].get("mode") or "transports") == "transports"), default=None)
        branches: list[list[dict[str, Any]]] = [[]]
        t = depart
        for i in range(1, n):
            p = points[i]
            mode = p.get("mode") or "transports"
            if mode != "transports":
                duree = timedelta(minutes=float(p.get("duree") or 0))
                etape = {"type": "detour", "mode": mode, "duree": round(duree.total_seconds() / 60), "vers": p.get("nom")}
                try:
                    trace = await self.client.trace_directe(navitia[i - 1], navitia[i], MODES_DETOUR.get(mode, "walking"))
                except ErreurJeton as err:
                    raise ConfigEntryAuthFailed("Jeton PRIM refusé") from err
                except ErreurPrim as err:
                    _LOGGER.debug("Tracé %s → %s : %s", navitia[i - 1], navitia[i], err)
                    trace = None
                if trace:
                    etape.update(trace=trace["trace"], de_coord=trace["de"], vers_coord=trace["vers"])
                for b in branches:
                    b.append({"i": i, "depart": t, "arrivee": t + duree, "etape": etape})
                arrivee = t + duree
            else:
                lignes = [x["ligne_id"] for x in p.get("sections") or [] if x.get("ligne_id")] if preferees and not p.get("libre") else None
                opts = [o for o in await self._options(navitia[i - 1], navitia[i], t, "departure", lignes, bloquees) if _arr(o) and _dep(o)]
                if not opts and lignes:  # lignes habituelles sans itinéraire à cette heure : au plus rapide
                    opts = [o for o in await self._options(navitia[i - 1], navitia[i], t, "departure", None, bloquees) if _arr(o) and _dep(o)]
                if not opts:
                    return []
                opts.sort(key=_arr)
                if i == derniers and nombre > 1:
                    branches = [b + [{"i": i, "depart": _dep(o), "arrivee": _arr(o), "option": o}] for b in branches for o in opts[:nombre]]
                else:
                    for b in branches:
                        b.append({"i": i, "depart": _dep(opts[0]), "arrivee": _arr(opts[0]), "option": opts[0]})
                arrivee = _arr(opts[0])
            if i < n - 1:
                a_bas, _, d_bas, _ = bornes_point(points, i)
                base = datetime.combine(jour, datetime.min.time())
                pret = max(arrivee, base + timedelta(minutes=a_bas) if a_bas > -INF else arrivee) + timedelta(minutes=float(p.get("sejour") or 0))
                t = max(pret, base + timedelta(minutes=d_bas)) if d_bas > -INF else pret
        # Après le dernier tronçon en transports, chaque branche recalcule ses heures (options d'arrivées différentes).
        sortie = []
        for b in branches:
            t = depart
            refaits = []
            for seg in b:
                i = seg["i"]
                if "etape" in seg:
                    duree = seg["arrivee"] - seg["depart"]
                    seg = {**seg, "depart": t, "arrivee": t + duree}
                elif seg["depart"] < t - timedelta(minutes=1):
                    refaits = None  # option partie avant qu'on soit là : branche impossible
                    break
                refaits.append(seg)
                if i < n - 1:
                    a_bas, _, d_bas, _ = bornes_point(points, i)
                    base = datetime.combine(jour, datetime.min.time())
                    pret = max(seg["arrivee"], base + timedelta(minutes=a_bas) if a_bas > -INF else seg["arrivee"]) + timedelta(minutes=float(points[i].get("sejour") or 0))
                    t = max(pret, base + timedelta(minutes=d_bas)) if d_bas > -INF else pret
            if refaits:
                sortie.append(refaits)
        return sortie

    def _assembler_etapes(self, points, jour: date, segs: list[dict[str, Any]]) -> dict[str, Any]:
        """Itinéraire complet d'un trajet à étapes, avec les heures et écarts à chaque point."""
        n = len(points)
        base = datetime.combine(jour, datetime.min.time())
        etapes: list[dict[str, Any]] = []
        sections: list[dict[str, Any]] = []
        horaires = [{"nom": points[0].get("nom"), "depart_h": _h(segs[0]["depart"]), "retard": 0}]
        for k, seg in enumerate(segs):
            i, p = seg["i"], points[seg["i"]]
            if "option" in seg:
                o = seg["option"]
                etapes += [{**e, "vers": e.get("vers") or p.get("nom")} for e in o["etapes"]]
                sections += o["sections"]
            else:
                etapes.append(seg["etape"])
            _, a_haut, _, d_haut = bornes_point(points, i)
            retard = max(0, round((seg["arrivee"] - (base + timedelta(minutes=a_haut))).total_seconds() / 60)) if a_haut < INF else 0
            h = {"nom": p.get("nom"), "arrivee_h": _h(seg["arrivee"]), "retard": retard}
            if i < n - 1 and k + 1 < len(segs):
                # On quitte le point à la fin du séjour (ou plus tard, borne de départ) ; l'attente du véhicule suit.
                fin_sejour = max(seg["arrivee"] + timedelta(minutes=float(p.get("sejour") or 0)), segs[k + 1]["depart"] if "etape" in segs[k + 1] else seg["arrivee"])
                _, _, d_bas, _ = bornes_point(points, i)
                if d_bas > -INF:
                    fin_sejour = max(fin_sejour, base + timedelta(minutes=d_bas))
                h["depart_h"] = _h(fin_sejour)
                if d_haut < INF:
                    h["retard"] = max(retard, round((fin_sejour - (base + timedelta(minutes=d_haut))).total_seconds() / 60))
                etapes.append({"type": "pause", "duree": int(float(p.get("sejour") or 0)), "lieu": p.get("nom"), "reprise": _h(fin_sejour)})
            horaires.append(h)
        depart, arrivee = segs[0]["depart"], segs[-1]["arrivee"]
        retards = [h for h in horaires if h["retard"]]
        duree = int((arrivee - depart).total_seconds())
        return {
            "depart": _fmt(depart),
            "arrivee": _fmt(arrivee),
            "depart_h": _h(depart),
            "arrivee_h": _h(arrivee),
            "duree": duree,
            "etapes": etapes,
            "sections": sections,
            "correspondances": max(0, len(sections) - 1),
            "resume": resume_itineraire(sections, duree, len(sections) - 1 if sections else None),
            "texte": texte_itineraire(etapes, _fmt(depart), _fmt(arrivee)),
            "detour": True,
            "points": horaires,
            "possible": not retards,
            "bloquant": retards[0]["nom"] if retards else None,
        }

    async def _plans_etapes(self, t, jour: date, quand: datetime | None, preferees: bool, bloquees: list[str], nombre: int = 1) -> list[dict[str, Any]]:
        """Trajet à étapes : horaires planifiés (planning.py) puis vérifiés tronçon par tronçon avec PRIM.

        1. durées estimées (itinéraire habituel, durée réglée) → heure de départ ;
        2. trajet réel à partir de cette heure → durées réelles (attente du véhicule comprise) → nouvelle heure ;
        3. trajet réel à partir de la nouvelle heure : c'est le plan.
        quand : départ imposé (au départ de maintenant) ; on enchaîne au plus tôt.
        """
        points = points_du_jour(points_du_trajet(t.data), jour)
        base = datetime.combine(jour, datetime.min.time())
        estime = [0.0] + [
            float(p.get("duree") or 0) if (p.get("mode") or "transports") != "transports" else ((p.get("habituel") or {}).get("duree") or 1800) / 60
            for p in points[1:]
        ]
        partir_a = (quand - base).total_seconds() / 60 if quand else None
        libre = minutes(t.data.get("depart_habituel")) or 8 * 60
        plan = planifier(points, estime, depart_libre=libre, partir_a=partir_a)
        if plan.depart is None:
            return []
        essais = await self._simuler(points, jour, base + timedelta(minutes=plan.depart), preferees, bloquees)
        if not essais:
            return []
        if quand is None:
            segs = essais[0]
            reel = [0.0] * len(points)
            for k, seg in enumerate(segs):
                avant = segs[k - 1]["arrivee"] if k else base + timedelta(minutes=plan.depart)
                # Durée réelle depuis la fin du séjour précédent : trajet + attente du véhicule.
                fin = avant if k == 0 else max(avant + timedelta(minutes=float(points[seg["i"] - 1].get("sejour") or 0)), avant)
                reel[seg["i"]] = max(1.0, (seg["arrivee"] - min(fin, seg["depart"])).total_seconds() / 60)
            plan = planifier(points, reel, depart_libre=libre)
            if plan.depart is None:
                return []
            essais = await self._simuler(points, jour, base + timedelta(minutes=plan.depart), preferees, bloquees, nombre)
        else:
            essais = await self._simuler(points, jour, quand, preferees, bloquees, nombre)
        return [self._assembler_etapes(points, jour, segs) for segs in essais if segs]

    async def _async_update_data(self) -> dict[str, EtatTrajet]:
        trajets = self._trajets()
        maintenant = dt_util.now().replace(tzinfo=None)
        lignes = sorted({sec["ligne_id"] for t in trajets for sec in sections_trajet(t.data) if sec.get("ligne_id")})
        resultats = await asyncio.gather(*(self.client.perturbations_ligne(l) for l in lignes), return_exceptions=True)
        if any(isinstance(r, ErreurJeton) for r in resultats):
            raise ConfigEntryAuthFailed("Jeton PRIM refusé")
        if lignes and all(isinstance(r, Exception) for r in resultats):
            raise UpdateFailed(f"Info trafic indisponible : {resultats[0]}")
        par_ligne = {l: r for l, r in zip(lignes, resultats, strict=True) if isinstance(r, list)}
        self._par_ligne = par_ligne
        if self._feries_le != maintenant.date():
            self.feries = await jours_feries(self.hass, maintenant.date() - timedelta(days=1))
            self._feries_le = maintenant.date()

        ignorer_info = option(self.config_entry, OPT_IGNORER_INFO)
        self.calcul.ignorer_info = ignorer_info
        une_veille = False
        for t in trajets:
            etat = self.etats.setdefault(t.subentry_id, EtatTrajet())
            exclus = set() if aussi_feries(t.data) else self.feries
            etat.en_veille = self.fenetre(heure_depart_maison(t.data), VEILLE_MINUTES, maintenant, jours_trajet(t.data), exclus, self._apres_depart(t, etat))
            self._perturbations(t, etat, par_ligne, ignorer_info, maintenant)
            une_veille |= etat.en_veille
            await self._nominal(t, etat, maintenant)
            if etat.en_veille:
                await self._direct(t, etat, maintenant)
                if etat.fige:
                    await self._en_route(t, etat)
                else:
                    await self._rappel(t, etat, maintenant)
            else:
                etat.rappel = None
                if etat.direct_le and maintenant - etat.direct_le > DUREE_DIRECT:
                    etat.direct, etat.direct_le = [], None
            etat.rechange = next((o for o in etat.direct if not set(_lignes(o)) & set(etat.bloquees)), None) if etat.bloquees else None
            self._alerter(t, etat)

        for sid in list(self.etats):
            if sid not in {t.subentry_id for t in trajets}:
                del self.etats[sid]
        self.update_interval = VEILLE if une_veille else REPOS
        return dict(self.etats)

    def _perturbations(self, t, etat: EtatTrajet, par_ligne: dict, ignorer_info: bool, maintenant: datetime) -> None:
        """Perturbations qui touchent la portion empruntée du trajet habituel, à l'heure où on y passe.

        Pendant la veille : maintenant ; sinon : à la prochaine occurrence. Les équipements (ascenseurs…) n'alertent pas.
        """
        d = t.data
        if etat.en_veille:
            reference = maintenant
        else:
            reference = datetime.combine(self.prochaine_occurrence(d, maintenant), _heure(heure_depart_maison(d)))
        # Arrêts parcourus : ceux du trajet habituel recalculé (à jour), sinon ceux enregistrés à la création.
        etapes = {e.get("ligne_id"): e for e in etapes_trajet(d) if e.get("type") == "transport" and e.get("ligne_id")}
        for e in (etat.nominal or {}).get("etapes") or []:
            if e.get("type") == "transport" and e.get("ligne_id") in etapes and e.get("arrets"):
                etapes[e["ligne_id"]] = e
        noms = {sec["ligne_id"]: sec for sec in sections_trajet(d) if sec.get("ligne_id")}
        perts: dict[str, dict[str, Any]] = {}
        niveaux: dict[str, list] = {}
        for lid, sec in noms.items():
            etape = etapes.get(lid) or {}
            for p in par_ligne.get(lid, []):
                if not concerne_ligne(p, lid):
                    continue
                niveau = niveau_perturbation(p, pour_trajet=True)
                if p.get("equipement") or (ignorer_info and niveau in (None, "travaux") and p["information"]):
                    continue
                if not pertinente(p, etape, reference):
                    continue
                niveaux.setdefault(lid, []).append(niveau)
                x = perts.setdefault(p["id"], {**p, "niveau": niveau, "lignes_trajet": [], "lignes_ids": []})
                nom = f"{sec.get('mode') or ''} {sec.get('nom') or ''}".strip()
                if lid not in x["lignes_ids"]:
                    x["lignes_trajet"].append(nom)
                    x["lignes_ids"].append(lid)
        etat.bloquees = sorted({lid for p in perts.values() if p["effet"] in EFFETS_BLOQUANTS for lid in p["lignes_ids"]})
        garder = ("id", "titre", "message", "niveau", "effet", "gravite", "cause", "fin", "lignes", "lignes_trajet", "lignes_ids", "information")
        etat.perturbations = [{k: p.get(k) for k in garder} for p in perts.values()]
        etat.niveaux = {lid: pire_niveau(n) for lid, n in niveaux.items()}

    def _preferees(self, t) -> list[str]:
        return [s["ligne_id"] for s in sections_trajet(t.data) if s.get("ligne_id")]

    async def _enrichir(self, its: list[dict[str, Any]], etat: EtatTrajet) -> None:
        await self.calcul.alertes(its, self._par_ligne)
        await self.calcul.suivants(its)

    async def _nominal(self, t, etat: EtatTrajet, maintenant: datetime) -> None:
        """Préféré pour la prochaine occurrence : une fois par jour, et à chaque mise à jour pendant la veille."""
        jour = self.prochaine_occurrence(t.data, maintenant)
        if not etat.en_veille and etat.nominal_jour == jour and etat.nominal_le and maintenant - etat.nominal_le < timedelta(hours=12):
            await self.calcul.alertes([etat.nominal] if etat.nominal else [], self._par_ligne)
            return
        plans = await self._plans(t, jour, None, self._preferees(t), [])
        etat.nominal = plans[0] if plans else None
        if etat.nominal:
            etat.nominal["jour"] = jour.isoformat()
            await self._enrichir([etat.nominal], etat)
        etat.nominal_jour, etat.nominal_le = jour, maintenant

    async def _direct(self, t, etat: EtatTrajet, maintenant: datetime) -> None:
        """Au départ de maintenant : le préféré (même perturbé) puis les autres itinéraires."""
        # Jour de référence pour le détour : l'occurrence en cours de veille, sinon aujourd'hui.
        jour = self.prochaine_occurrence(t.data, maintenant) if etat.en_veille else maintenant.date()
        pref, autres = await asyncio.gather(
            self._plans(t, jour, maintenant, self._preferees(t), [], 1, pas_avant=True),
            self._plans(t, jour, maintenant, None, etat.bloquees, NB_DIRECT, pas_avant=True),
        )
        liste = [{**o, "prefere": True} for o in pref]
        vus = {(o["depart"], o["resume"]) for o in liste}
        for o in autres:
            if (o["depart"], o["resume"]) not in vus and _lignes(o) != self._preferees(t):
                vus.add((o["depart"], o["resume"]))
                liste.append({**o, "prefere": False})
        liste = liste[:NB_DIRECT]
        if liste and liste[0].get("prefere"):
            ref = _arr(liste[0])
            for o in liste[1:]:
                o["gain"] = round((ref - _arr(o)).total_seconds() / 60) if ref and _arr(o) else None
        await self._enrichir(liste, etat)
        etat.direct, etat.direct_le = liste, maintenant

    async def _rappel(self, t, etat: EtatTrajet, maintenant: datetime) -> None:
        """Heure de départ de la maison pour l'occurrence du jour (meilleure option, préférées ou non)."""
        d = t.data
        jour = self.prochaine_occurrence(d, maintenant)
        if not jour_du_trajet(d, jour, self.feries):
            etat.rappel = None
            return
        if d.get("points") and besoin_planning(d["points"]):
            await self._rappel_etapes(t, etat, jour, maintenant)
            return
        plans = await self._plans(t, jour, None, None, etat.bloquees, 3)
        cible = datetime.combine(jour, _heure(d[T_HEURE]))
        if d.get(T_TYPE_HEURE) == "arrivee":
            # Départs calculés pour l'heure d'arrivée, et ceux au départ de maintenant (un départ plus tardif peut
            # encore arriver à l'heure). Seuls ceux à venir sont proposés : on ne conseille pas un départ déjà passé…
            # sauf sans conseil antérieur (démarrage de Home Assistant après l'heure) : départ supposé respecté.
            candidats = plans + list(etat.direct) if jour == maintenant.date() else plans
            if jour == maintenant.date() and etat.rappel is not None:
                candidats = [o for o in candidats if _dep(o) and _dep(o) >= maintenant - timedelta(minutes=1)]
            a_l_heure = [o for o in candidats if _arr(o) and _arr(o) <= cible]
            if a_l_heure:
                meilleur = max(a_l_heure, key=_dep)  # le plus tard possible en arrivant à l'heure
            else:
                meilleur = min(candidats, key=lambda o: _arr(o) or datetime.max, default=None)  # en retard : arriver au plus tôt
        else:
            meilleur = min(plans, key=lambda o: _arr(o) or datetime.max, default=None)
        habituel = datetime.combine(jour, _heure(heure_depart_maison(d)))
        if meilleur is None:
            etat.rappel = {"impossible": True, "heure_cible": _h(cible), "type_heure": d.get(T_TYPE_HEURE, "depart")}
            return
        await self._enrichir([meilleur], etat)
        depart = _dep(meilleur)
        etat.rappel = {
            **meilleur,
            "type_heure": d.get(T_TYPE_HEURE, "depart"),
            "heure_cible": _h(cible),
            "cible_iso": dt_util.as_utc(cible.replace(tzinfo=dt_util.get_default_time_zone())).isoformat(),
            "depart_iso": dt_util.as_utc(depart.replace(tzinfo=dt_util.get_default_time_zone())).isoformat(),
            # > 0 : partir plus tôt que d'habitude (seulement pour une heure d'arrivée ou un détour à heure imposée)
            "avance": round((habituel - depart).total_seconds() / 60)
            if d.get(T_TYPE_HEURE) == "arrivee" or (avec_detour(d, jour) and d.get(T_DET_HEURE))
            else 0,
            "retard": d.get(T_TYPE_HEURE) == "arrivee" and bool(_arr(meilleur) and _arr(meilleur) > cible),
            "retard_min": max(0, round((_arr(meilleur) - cible).total_seconds() / 60)) if d.get(T_TYPE_HEURE) == "arrivee" and _arr(meilleur) else 0,
            "heure": d.get(T_HEURE),
            "habituel": _lignes(meilleur) == self._preferees(t),
        }

    def _apres_depart(self, t, etat: EtatTrajet) -> timedelta:
        """Fin de la veille après le départ habituel : jusqu'à l'arrivée prévue + 15 min (suivi en route)."""
        d = t.data
        duree = (etat.nominal or {}).get("duree") or d.get(T_DUREE) or 0
        fin = timedelta(seconds=float(duree)) + timedelta(minutes=15)
        f = etat.fige
        if f and f.get("arrivee") and f.get("depart"):
            habituel = datetime.combine(date_navitia(f["depart"]).date(), _heure(heure_depart_maison(d)))
            fin = max(fin, date_navitia(f["arrivee"]) - habituel + timedelta(minutes=15))
        return max(APRES_DEPART, fin)

    async def _en_route(self, t, etat: EtatTrajet) -> None:
        """Départ supposé respecté : prévision recalculée à partir de lui, quand les perturbations changent, et au plus
        toutes les 6 min si une perturbation est en cours (elle peut s'aggraver ou se résorber)."""
        ids = frozenset(p["id"] for p in etat.perturbations)
        maintenant = dt_util.now().replace(tzinfo=None)
        if ids == etat.ids_fige and not (ids and etat.fige_le and maintenant - etat.fige_le >= timedelta(minutes=6)):
            return
        etat.ids_fige, etat.fige_le = ids, maintenant
        f = etat.fige
        depart = date_navitia(f.get("depart"))
        if depart is None:
            return
        jour = depart.date()
        plans = await self._plans(t, jour, depart, None, etat.bloquees, 1)
        meilleur = min(plans, key=lambda o: _arr(o) or datetime.max, default=None)
        if meilleur is None:
            etat.rappel = {**f, "en_route": True, "impossible": True}
            return
        await self._enrichir([meilleur], etat)
        r = {**f, **meilleur, "depart": f["depart"], "depart_h": f.get("depart_h"), "depart_iso": f.get("depart_iso"), "en_route": True}
        if not meilleur.get("points"):  # trajet simple : retard sur l'heure visée
            cible = None
            if f.get("type_heure") == "arrivee" and f.get("heure"):
                cible = datetime.combine(jour, _heure(f["heure"]))
            arr = _arr(meilleur)
            r["retard"] = bool(cible and arr and arr > cible)
            r["retard_min"] = max(0, round((arr - cible).total_seconds() / 60)) if cible and arr else 0
        else:
            r["retard"] = not meilleur.get("possible", True)
        r["impossible"] = False
        etat.rappel = r

    async def _rappel_etapes(self, t, etat: EtatTrajet, jour: date, maintenant: datetime) -> None:
        """Trajet à étapes : le départ planifié (comme le rappel des autres trajets).

        Une fois le départ conseillé passé, il est gardé tel quel (Home Assistant ne sait pas si la personne est
        partie). Si le plan voudrait un départ déjà passé alors que le conseil précédent ne l'est pas (perturbation,
        trajet plus long que prévu), on repart de maintenant : retards et écarts, une alerte.
        """
        d = t.data
        plans = await self._plans(t, jour, None, None, etat.bloquees, 1)
        meilleur = plans[0] if plans else None
        passe = meilleur is not None and _dep(meilleur) and _dep(meilleur) < maintenant - timedelta(minutes=1)
        if jour == maintenant.date() and (meilleur is None or (passe and etat.rappel is not None)):
            # Le plan voudrait un départ déjà passé alors qu'on conseillait un départ à venir (perturbation, trajet
            # plus long que prévu) : au mieux, maintenant. Sans conseil antérieur (démarrage de Home Assistant après
            # l'heure), le départ planifié est supposé respecté.
            maintenant_plans = await self._plans(t, jour, maintenant, None, etat.bloquees, 1)
            meilleur = maintenant_plans[0] if maintenant_plans else None
        pts = points_du_jour(points_du_trajet(d), jour)
        _, haut, _, _ = bornes_point(pts, len(pts) - 1)
        cible = datetime.combine(jour, datetime.min.time()) + timedelta(minutes=haut) if haut < INF else None
        if meilleur is None:
            etat.rappel = {"impossible": True, "heure_cible": _h(cible), "type_heure": "arrivee" if cible else "depart"}
            return
        await self._enrichir([meilleur], etat)
        depart = _dep(meilleur)
        habituel = datetime.combine(jour, _heure(heure_depart_maison(d)))
        fuseau = dt_util.get_default_time_zone()
        etat.rappel = {
            **meilleur,
            "type_heure": "arrivee" if cible else "depart",
            "heure_cible": _h(cible),
            "heure": cible.strftime("%H:%M:%S") if cible else None,
            "cible_iso": dt_util.as_utc(cible.replace(tzinfo=fuseau)).isoformat() if cible else None,
            "depart_iso": dt_util.as_utc(depart.replace(tzinfo=fuseau)).isoformat(),
            "avance": round((habituel - depart).total_seconds() / 60),
            "retard": not meilleur.get("possible", True),
            "habituel": _lignes(meilleur) == self._preferees(t),
        }

    async def calculer_maintenant(self, sous_id: str) -> dict[str, Any]:
        """À la demande (dépliage d'un trajet dans la carte, action) : propositions au départ de maintenant."""
        t = self.config_entry.subentries[sous_id]
        etat = self.etats.setdefault(sous_id, EtatTrajet())
        maintenant = dt_util.now().replace(tzinfo=None)
        if not (etat.direct_le and maintenant - etat.direct_le < FRAICHEUR_DIRECT):
            try:
                await self._direct(t, etat, maintenant)
            except ConfigEntryAuthFailed as err:
                raise HomeAssistantError("Jeton PRIM refusé") from err
            except HomeAssistantError:
                raise
            except Exception as err:
                _LOGGER.exception("Calcul au départ de maintenant impossible pour %s", t.title)
                raise HomeAssistantError(f"Calcul impossible ({type(err).__name__} : {err or 'sans détail'}) — voir le journal") from err
            self.async_update_listeners()
        return {"le": dt_util.utcnow().isoformat(), "itineraires": etat.direct}

    # ---- Alertes -------------------------------------------------------------
    @staticmethod
    def _prevision(r: dict[str, Any] | None) -> tuple:
        """Ce qu'une alerte annonce : départ, arrivée, écarts. Une autre valeur = une nouvelle prévision."""
        if not r:
            return ()
        if r.get("impossible"):
            return ("impossible",)
        ecarts = tuple((p.get("nom"), p.get("retard")) for p in r.get("points") or [] if p.get("retard"))
        return (r.get("depart_h"), r.get("arrivee_h"), r.get("retard_min") or 0, ecarts)

    def _alerter(self, t, etat: EtatTrajet) -> None:
        """Avant le départ conseillé : départ à avancer, retard (départ qui serait déjà passé), « pars à » 10 min avant.
        Une fois le « pars à » envoyé (ou l'heure passée), le départ est supposé respecté (figé) : la suite se calcule
        à partir de lui. Toute alerte porte la prévision ; dès qu'une alerte est partie, chaque changement de
        prévision en envoie une nouvelle.
        """
        if not etat.en_veille:
            etat.notifiees = set()
            etat.depart_notifie = None
            etat.rappel_envoye = None
            etat.fige = etat.ids_fige = etat.prevision_notifiee = etat.fige_le = None
            return
        ids = {p["id"] for p in etat.perturbations}
        r = etat.rappel
        maintenant = dt_util.now().replace(tzinfo=None)
        dep = date_navitia(r.get("depart")) if r and not r.get("impossible") else None
        envoyees = []
        nouvelles = ids - etat.notifiees
        type_ = "perturbation" if nouvelles else "retour_normal" if not ids and etat.notifiees else None
        etat.notifiees = ids
        if type_:
            envoyees.append((type_, nouvelles))
        if not etat.fige and r:
            if r.get("impossible") or r.get("retard") or (r.get("avance") or 0) >= ECART_MINUTES:
                d_ = datetime.min if (r.get("impossible") or r.get("retard")) else dep
                # Déjà prévenu d'un retard (datetime.min) : on ne renvoie pas (et datetime.min − 3 min déborderait).
                if etat.depart_notifie is None or (d_ and etat.depart_notifie != datetime.min and d_ <= etat.depart_notifie - timedelta(minutes=ECART_MINUTES)):
                    etat.depart_notifie = d_
                    envoyees.append(("depart_anticipe", set()))
            # « Pars à … » : une fois, de 10 min avant le départ conseillé jusqu'à l'heure du départ ; ensuite le départ
            # est supposé respecté.
            if dep and etat.rappel_envoye is None and dep - timedelta(minutes=RAPPEL_MINUTES) <= maintenant:
                etat.rappel_envoye = dep
                if maintenant <= dep + timedelta(minutes=1):
                    envoyees.append(("rappel_depart", set()))
                etat.fige = {**r}
        prevision = self._prevision(r)
        if not envoyees and etat.prevision_notifiee is not None and prevision != etat.prevision_notifiee:
            envoyees.append(("prevision", set()))
        for type_, nouv in envoyees:
            self._envoyer(t, etat, type_, nouv)
        if envoyees and any(x[0] != "rappel_depart" for x in envoyees):
            etat.prevision_notifiee = prevision
        elif envoyees and etat.prevision_notifiee is not None:
            etat.prevision_notifiee = prevision

    def _envoyer(self, t, etat: EtatTrajet, type_: str, nouvelles: set[str]) -> None:
        r = etat.rappel or {}
        conseil = {**r, "impossible": bool(r.get("impossible") or r.get("retard"))} if r else None
        self.hass.bus.async_fire(
            EVENEMENT_ALERTE,
            {
                "type": type_,
                "trajet": t.data.get(T_NOM) or t.title,
                "personne": t.data.get(T_PERSONNE),
                # Trajet à étapes : l'heure visée est celle de la destination (rappel), pas le départ planifié.
                "heure": r.get("heure") or t.data.get(T_HEURE),
                "type_heure": r.get("type_heure") if r.get("points") else t.data.get(T_TYPE_HEURE, "depart"),
                "ecarts": [f"{p['nom']} {p.get('arrivee_h') or p.get('depart_h')} (+{p['retard']} min)" for p in r.get("points") or [] if p.get("retard")]
                or ([f"arrivée {r.get('arrivee_h')} (+{r['retard_min']} min)"] if r.get("retard_min") else []),
                "arrivee_prevue": r.get("arrivee_h"),
                "en_route": bool(r.get("en_route")),
                "depart_habituel": heure_depart_maison(t.data),
                "conseil": conseil,
                "itineraire": t.data.get(T_RESUME),
                "nouvelles": [p for p in etat.perturbations if p["id"] in nouvelles],
                "perturbations": etat.perturbations,
                "rechange": etat.rechange,
                "nominal": etat.nominal,
                "sous_entree": t.subentry_id,
            },
        )

    # ---- Pour l'affichage --------------------------------------------------
    def etapes_habituelles(self, sous_id: str) -> list[dict[str, Any]]:
        sous = self.config_entry.subentries[sous_id]
        etat = self.etats.get(sous_id) or EtatTrajet()
        etapes = [dict(e) for e in etapes_trajet(sous.data)]
        if not etapes:
            etapes = [{"type": "transport", **{k: s.get(k) for k in ("nom", "mode", "couleur", "couleur_texte", "ligne_id", "de", "vers", "direction")}} for s in sections_trajet(sous.data)]
        for e in etapes:
            if e.get("type") == "transport":
                lid = e.get("ligne_id")
                niveau = etat.niveaux.get(lid)
                e["alerte"] = niveau if niveau and niveau != "normal" else None
                e["messages"] = messages_affiches([p for p in etat.perturbations if lid in (p.get("lignes_ids") or [])], False)
        return etapes
