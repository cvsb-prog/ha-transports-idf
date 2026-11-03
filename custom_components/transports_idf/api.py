"""Client PRIM (Île-de-France Mobilités) : prochains passages, calculateur, info trafic.

Les fonctions `analyser_*` sont pures (sans réseau) pour être testées à part.
"""

from __future__ import annotations

import asyncio
import html
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import BASE_URL, EFFETS_INFO, NAVITIA

_LOGGER = logging.getLogger(__name__)

DELAI = aiohttp.ClientTimeout(total=20)
VITESSE_MARCHE = 1.2  # m/s (≈ 4,3 km/h), pour l'estimation de secours
DETOUR = 1.3  # rapport distance à pied / distance à vol d'oiseau


class ErreurPrim(Exception):
    """Erreur générique de l'API PRIM."""


class ErreurJeton(ErreurPrim):
    """Jeton refusé (401/403)."""


class ErreurQuota(ErreurPrim):
    """Quota atteint (429)."""


class ClientPrim:
    """Accès aux API PRIM avec un jeton."""

    def __init__(self, session: aiohttp.ClientSession, jeton: str) -> None:
        self._session = session
        self._jeton = jeton
        # PRIM limite le nombre de requêtes par seconde : on en garde peu en vol.
        self._verrou = asyncio.Semaphore(4)

    async def _get(self, url: str, params: list[tuple[str, str]] | None = None) -> Any:
        async with self._verrou:
            try:
                async with self._session.get(
                    url, params=params, headers={"apikey": self._jeton, "Accept": "application/json"}, timeout=DELAI
                ) as rep:
                    if rep.status in (401, 403):
                        raise ErreurJeton(f"jeton refusé ({rep.status})")
                    if rep.status == 429:
                        raise ErreurQuota("quota PRIM atteint")
                    if rep.status == 404:
                        # Navitia répond 404 pour « aucune solution » : on rend le corps.
                        try:
                            return await rep.json(content_type=None)
                        except (aiohttp.ContentTypeError, ValueError):
                            return {}
                    if rep.status >= 400:
                        raise ErreurPrim(f"HTTP {rep.status} sur {url}")
                    return await rep.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise ErreurPrim(f"PRIM injoignable : {err}") from err

    # ---- Prochains passages (SIRI Lite) ---------------------------------
    async def passages(self, ref: str) -> list[dict[str, Any]]:
        donnees = await self._get(f"{BASE_URL}/stop-monitoring", [("MonitoringRef", ref)])
        return analyser_passages(donnees)

    # ---- Calculateur (Navitia) ------------------------------------------
    async def lieux(self, texte: str, types: list[str]) -> list[dict[str, Any]]:
        params = [("q", texte), ("count", "15")] + [("type[]", t) for t in types]
        return analyser_lieux(await self._get(f"{NAVITIA}/places", params))

    async def lignes_zone(self, zone_id: str) -> list[dict[str, Any]]:
        donnees = await self._get(f"{NAVITIA}/stop_areas/{quote(zone_id)}/lines", [("count", "200")])
        return analyser_lignes(donnees)

    async def points_zone(self, zone_id: str) -> list[str]:
        donnees = await self._get(f"{NAVITIA}/stop_areas/{quote(zone_id)}/stop_points", [("count", "200")])
        return [p["id"] for p in donnees.get("stop_points", []) if "id" in p]

    async def temps_marche(self, lat: float, lon: float, zone_id: str) -> int | None:
        """Durée à pied (secondes) calculée par le calculateur, None si pas de solution."""
        params = [
            ("from", f"{lon};{lat}"),
            ("to", zone_id),
            ("direct_path", "only"),
            ("direct_path_mode[]", "walking"),
            ("first_section_mode[]", "walking"),
            ("last_section_mode[]", "walking"),
        ]
        donnees = await self._get(f"{NAVITIA}/journeys", params)
        durees = [j.get("duration") for j in donnees.get("journeys", []) if isinstance(j.get("duration"), int)]
        return min(durees) if durees else None

    async def itineraires(
        self,
        depart: str,
        arrivee: str,
        quand: datetime | None = None,
        interdits: list[str] | None = None,
        autorises: list[str] | None = None,
        represente: str = "departure",
    ) -> list[dict[str, Any]]:
        params = [("from", depart), ("to", arrivee), ("min_nb_journeys", "3"), ("count", "5")]
        if quand is not None:
            params += [("datetime", quand.strftime("%Y%m%dT%H%M%S")), ("datetime_represents", represente)]
        params += [("forbidden_uris[]", i) for i in (interdits or [])]
        params += [("allowed_id[]", i) for i in (autorises or [])]
        return analyser_itineraires(await self._get(f"{NAVITIA}/journeys", params))

    async def perturbations_ligne(self, ligne_id: str) -> list[dict[str, Any]]:
        url = f"{NAVITIA}/line_reports/lines/{quote(ligne_id)}/line_reports"
        return analyser_perturbations(await self._get(url, [("count", "100")]))

    async def lignes_reseau(self) -> list[dict[str, Any]]:
        """Toutes les lignes hors bus (RER, Transilien, métro, tram…), avec code et couleurs."""
        params = [("count", "1000"), ("depth", "1"), ("forbidden_uris[]", "physical_mode:Bus")]
        return analyser_lignes(await self._get(f"{NAVITIA}/lines", params))

    async def chercher_lignes(self, texte: str) -> list[dict[str, Any]]:
        donnees = await self._get(f"{NAVITIA}/pt_objects", [("q", texte), ("type[]", "line"), ("count", "20")])
        lignes = [o.get("line") for o in (donnees or {}).get("pt_objects", []) if o.get("embedded_type") == "line"]
        return analyser_lignes({"lines": [l for l in lignes if l]})

    async def perturbations_reseau(self, pages_max: int = 5) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
        """Info trafic de tout le réseau hors bus (paginé par 100) → perturbations et lignes touchées."""
        url = f"{NAVITIA}/line_reports/line_reports"
        fusion: dict[str, list] = {"disruptions": [], "line_reports": []}
        for page in range(pages_max):
            params = [("count", "100"), ("start_page", str(page)), ("forbidden_uris[]", "physical_mode:Bus")]
            donnees = await self._get(url, params) or {}
            fusion["disruptions"] += donnees.get("disruptions", []) or []
            fusion["line_reports"] += donnees.get("line_reports", []) or []
            pagination = donnees.get("pagination") or {}
            total, par_page = pagination.get("total_result"), pagination.get("items_per_page") or 100
            if not isinstance(total, int) or (page + 1) * par_page >= total:
                break
        return analyser_perturbations(fusion), lignes_touchees(fusion)

    async def departs_route(self, point: str, route: str, depuis: datetime, nombre: int = 4) -> list[datetime]:
        """Prochains départs (temps réel si disponible) d'une route à un point d'arrêt."""
        url = f"{NAVITIA}/stop_points/{quote(point)}/routes/{quote(route)}/departures"
        params = [
            ("from_datetime", (depuis - timedelta(minutes=1)).strftime("%Y%m%dT%H%M%S")),
            ("count", str(nombre)),
            ("data_freshness", "realtime"),
        ]
        donnees = await self._get(url, params) or {}
        sortie = []
        for d in donnees.get("departures", []) or []:
            sdt = d.get("stop_date_time") or {}
            t = date_navitia(sdt.get("departure_date_time") or sdt.get("arrival_date_time"))
            if t:
                sortie.append(t)
        return sorted(sortie)


# ---- Identifiants ---------------------------------------------------------
_NUM = re.compile(r"(\d+)$")
_CODE_LIGNE = re.compile(r"(C\d{5})")


def refs_candidates(zone_id: str, points: list[str]) -> list[str]:
    """MonitoringRef SIRI possibles pour une zone d'arrêt Navitia et ses points."""
    refs: list[str] = []
    m = _NUM.search(zone_id.rstrip(":"))
    if m:
        refs.append(f"STIF:StopArea:SP:{m.group(1)}:")
    for p in points:
        m = _NUM.search(p.rstrip(":"))
        if not m:
            continue
        ref = f"STIF:StopArea:SP:{m.group(1)}:" if "monomodalStopPlace" in p else f"STIF:StopPoint:Q:{m.group(1)}:"
        if ref not in refs:
            refs.append(ref)
    return refs


def code_ligne(ref: str | None) -> str | None:
    """« STIF:Line::C01742: » ou « line:IDFM:C01742 » → « C01742 »."""
    if not ref:
        return None
    m = _CODE_LIGNE.search(ref)
    return m.group(1) if m else None


# ---- Analyse des réponses -------------------------------------------------
def _valeur(x: Any) -> str | None:
    """SIRI met souvent les textes dans [{"value": …}] ou {"value": …}."""
    if isinstance(x, list):
        x = x[0] if x else None
    if isinstance(x, dict):
        x = x.get("value")
    return x if isinstance(x, str) and x else None


def _date_siri(x: Any) -> datetime | None:
    if not isinstance(x, str):
        return None
    try:
        d = datetime.fromisoformat(x.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def analyser_passages(donnees: Any) -> list[dict[str, Any]]:
    """Réponse stop-monitoring → passages triés (dates en UTC)."""
    sortie: list[dict[str, Any]] = []
    try:
        livraisons = donnees["Siri"]["ServiceDelivery"]["StopMonitoringDelivery"]
    except (KeyError, TypeError):
        return sortie
    for liv in livraisons or []:
        for visite in liv.get("MonitoredStopVisit", []) or []:
            mvj = visite.get("MonitoredVehicleJourney") or {}
            appel = mvj.get("MonitoredCall") or {}
            prevu = _date_siri(appel.get("ExpectedDepartureTime")) or _date_siri(appel.get("ExpectedArrivalTime"))
            theorique = _date_siri(appel.get("AimedDepartureTime")) or _date_siri(appel.get("AimedArrivalTime"))
            depart = prevu or theorique
            if depart is None:
                continue
            statut = appel.get("DepartureStatus") or appel.get("ArrivalStatus")
            sortie.append(
                {
                    "ligne": code_ligne(_valeur(mvj.get("LineRef"))),
                    "destination": _valeur(appel.get("DestinationDisplay")) or _valeur(mvj.get("DestinationName")),
                    "direction": _valeur(mvj.get("DirectionName")),
                    "depart": depart,
                    "theorique": theorique,
                    "temps_reel": prevu is not None,
                    "quai": _valeur(appel.get("DeparturePlatformName")) or _valeur(appel.get("ArrivalPlatformName")),
                    "a_quai": bool(appel.get("VehicleAtStop")),
                    "supprime": isinstance(statut, str) and statut.lower() == "cancelled",
                    "mission": _valeur(mvj.get("JourneyNote")),
                    "voyage": _valeur(mvj.get("FramedVehicleJourneyRef", {}).get("DatedVehicleJourneyRef"))
                    if isinstance(mvj.get("FramedVehicleJourneyRef"), dict)
                    else None,
                    "arret": _valeur(visite.get("MonitoringRef")),
                }
            )
    sortie.sort(key=lambda p: p["depart"])
    return sortie


def _commune(objet: dict[str, Any]) -> str | None:
    for region in objet.get("administrative_regions", []) or []:
        if region.get("level") == 8 or region.get("name"):
            return region.get("name")
    return None


def analyser_lieux(donnees: Any) -> list[dict[str, Any]]:
    sortie: list[dict[str, Any]] = []
    for lieu in (donnees or {}).get("places", []) or []:
        type_ = lieu.get("embedded_type")
        objet = lieu.get(type_) or {}
        coord = objet.get("coord") or {}
        try:
            lat, lon = float(coord["lat"]), float(coord["lon"])
        except (KeyError, TypeError, ValueError):
            lat = lon = None
        sortie.append(
            {
                "id": lieu.get("id"),
                "nom": objet.get("name") or lieu.get("name"),
                "libelle": lieu.get("name"),
                "type": type_,
                "lat": lat,
                "lon": lon,
                "commune": _commune(objet),
            }
        )
    return [s for s in sortie if s["id"]]


def _couleur(c: Any) -> str | None:
    return f"#{c}" if isinstance(c, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", c) else None


def analyser_lignes(donnees: Any) -> list[dict[str, Any]]:
    sortie = []
    for ligne in (donnees or {}).get("lines", []) or []:
        modes = ligne.get("physical_modes") or []
        sortie.append(
            {
                "id": ligne.get("id"),
                "code": code_ligne(ligne.get("id")),
                "nom": ligne.get("code") or ligne.get("name"),
                "nom_long": ligne.get("name"),
                "mode": (ligne.get("commercial_mode") or {}).get("name") or (modes[0].get("name") if modes else None),
                "mode_physique": modes[0].get("id") if modes else None,
                "couleur": _couleur(ligne.get("color")),
                "couleur_texte": _couleur(ligne.get("text_color")),
            }
        )
    ordre = {"RER": 0, "Train": 1, "Transilien": 1, "Métro": 2, "Metro": 2, "Tramway": 3, "Bus": 4}
    sortie.sort(key=lambda l: (ordre.get(l["mode"] or "", 9), _cle_naturelle(l["nom"] or "")))
    return [s for s in sortie if s["id"]]


def _cle_naturelle(texte: str) -> list[Any]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", texte)]


def date_navitia(x: Any) -> datetime | None:
    """« 20261102T223000 » (heure locale de la couverture) → datetime naïf."""
    if not isinstance(x, str):
        return None
    try:
        return datetime.strptime(x, "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def _zone_lieu(lieu: Any) -> str | None:
    """Zone d'arrêt (stop_area) d'un lieu Navitia, pour relier un trajet aux arrêts suivis."""
    if not isinstance(lieu, dict):
        return None
    if lieu.get("embedded_type") == "stop_area":
        return lieu.get("id")
    return ((lieu.get("stop_point") or {}).get("stop_area") or {}).get("id")


def _coord_lieu(lieu: Any) -> list[float] | None:
    """[lat, lon] d'un lieu Navitia (adresse, arrêt, lieu…)."""
    if not isinstance(lieu, dict):
        return None
    objet = lieu.get(lieu.get("embedded_type") or "") or lieu
    coord = objet.get("coord") or lieu.get("coord") or {}
    try:
        return [float(coord["lat"]), float(coord["lon"])]
    except (KeyError, TypeError, ValueError):
        return None


def _point_lieu(lieu: Any) -> str | None:
    if not isinstance(lieu, dict):
        return None
    if lieu.get("embedded_type") == "stop_point":
        return lieu.get("id")
    return None


def lignes_touchees(donnees: Any) -> dict[str, set[str]]:
    """line_reports → {id de ligne : ids des perturbations qui la touchent}."""
    sortie: dict[str, set[str]] = {}

    def liens(objet: Any) -> set[str]:
        return {l.get("id") for l in (objet or {}).get("links", []) or [] if l.get("type") == "disruption" and l.get("id")}

    for rapport in (donnees or {}).get("line_reports", []) or []:
        ligne = rapport.get("line") or {}
        if not ligne.get("id"):
            continue
        ids = liens(ligne)
        for po in rapport.get("pt_objects", []) or []:
            ids |= liens(po) | liens(po.get(po.get("embedded_type") or "") or {})
        sortie.setdefault(ligne["id"], set()).update(ids)
    for d in (donnees or {}).get("disruptions", []) or []:
        for o in d.get("impacted_objects", []) or []:
            po = o.get("pt_object") or {}
            if po.get("embedded_type") == "line" and po.get("id") and d.get("id"):
                sortie.setdefault(po["id"], set()).add(d["id"])
    return sortie


def niveau_perturbation(p: dict[str, Any]) -> str | None:
    """interrompu / perturbe / travaux, ou None pour une simple information."""
    effet = p.get("effet") or "UNKNOWN_EFFECT"
    travaux = "travaux" in f"{p.get('cause') or ''} {p.get('titre') or ''}".lower() or "works" in (p.get("cause") or "").lower()
    if effet == "NO_SERVICE" or (p.get("gravite") or "").lower().startswith("bloquant"):
        return "interrompu"
    if effet not in EFFETS_INFO:
        return "perturbe"
    return "travaux" if travaux else None


ORDRE_NIVEAUX = {"interrompu": 3, "perturbe": 2, "travaux": 1, None: 0, "normal": 0}


def pire_niveau(niveaux: Any) -> str:
    pire = max(niveaux, key=lambda n: ORDRE_NIVEAUX.get(n, 0), default=None)
    return pire if pire in ("interrompu", "perturbe", "travaux") else "normal"


def heure_navitia(x: Any) -> str | None:
    """« 20261103T080700 » → « 8 h 07 »."""
    d = date_navitia(x)
    return f"{d.hour} h {d.minute:02d}" if d else None


def _minutes(secondes: Any) -> int:
    return max(1, round(secondes / 60)) if isinstance(secondes, (int, float)) and secondes > 0 else 0


def analyser_itineraires(donnees: Any) -> list[dict[str, Any]]:
    """Itinéraires Navitia → résumé, lignes, et étapes détaillées (horaires, arrêts, marche)."""
    sortie = []
    for j in (donnees or {}).get("journeys", []) or []:
        sections, etapes = [], []
        for s in j.get("sections", []) or []:
            type_ = s.get("type")
            vers = (s.get("to") or {}).get("name")
            if type_ == "public_transport":
                info = s.get("display_informations") or {}
                ligne_id = next((l.get("id") for l in s.get("links", []) or [] if l.get("type") == "line"), None)
                section = {
                    "ligne_id": ligne_id,
                    "code": code_ligne(ligne_id),
                    "nom": info.get("code") or info.get("label") or info.get("name"),
                    "mode": info.get("commercial_mode") or info.get("physical_mode"),
                    "couleur": _couleur(info.get("color")),
                    "couleur_texte": _couleur(info.get("text_color")),
                    "direction": info.get("direction"),
                    "de": (s.get("from") or {}).get("name"),
                    "vers": vers,
                    "de_id": _zone_lieu(s.get("from")),
                    "de_point": _point_lieu(s.get("from")),
                    "vers_point": _point_lieu(s.get("to")),
                    "route_id": next((l.get("id") for l in s.get("links", []) or [] if l.get("type") == "route"), None),
                    "depart": s.get("departure_date_time"),
                }
                sections.append(section)
                etapes.append(
                    {
                        "type": "transport",
                        **{k: section[k] for k in ("nom", "code", "mode", "couleur", "couleur_texte", "direction", "de", "vers", "ligne_id", "de_point", "vers_point", "route_id")},
                        "depart_brut": s.get("departure_date_time"),
                        "depart": heure_navitia(s.get("departure_date_time")),
                        "arrivee": heure_navitia(s.get("arrival_date_time")),
                        "temps_reel": (s.get("data_freshness") or "realtime") == "realtime",
                    }
                )
            elif type_ in ("street_network", "transfer", "crow_fly") and (s.get("mode") in (None, "walking")):
                duree = _minutes(s.get("duration"))
                if not duree:
                    continue
                rues = [p.get("name") for p in s.get("path", []) or [] if p.get("name")][:3]
                if etapes and etapes[-1]["type"] == "marche":
                    etapes[-1]["duree"] += duree
                    etapes[-1]["vers"] = vers
                else:
                    etapes.append({"type": "marche", "duree": duree, "vers": vers})
                    if rues:
                        etapes[-1]["rues"] = rues
                if coord := _coord_lieu(s.get("to")):
                    etapes[-1]["vers_coord"] = coord
        for n, e in enumerate(etapes[:-1]):
            if e["type"] == "marche" and not e.get("vers") and etapes[n + 1]["type"] == "transport":
                e["vers"] = etapes[n + 1].get("de")
        # La dernière marche mène à la destination : son nom n'apporte rien.
        if etapes and etapes[-1]["type"] == "marche" and len(etapes) > 1:
            etapes[-1]["vers"] = None
        sortie.append(
            {
                "duree": j.get("duration"),
                "correspondances": j.get("nb_transfers", max(len(sections) - 1, 0)),
                "depart": j.get("departure_date_time"),
                "arrivee": j.get("arrival_date_time"),
                "depart_h": heure_navitia(j.get("departure_date_time")),
                "arrivee_h": heure_navitia(j.get("arrival_date_time")),
                "marche": (j.get("durations") or {}).get("walking"),
                "sections": sections,
                "etapes": etapes,
                "resume": resume_itineraire(sections, j.get("duration"), j.get("nb_transfers")),
                "texte": texte_itineraire(etapes, j.get("departure_date_time"), j.get("arrival_date_time")),
            }
        )
    return sortie


def texte_itineraire(etapes: list[dict[str, Any]], depart: Any = None, arrivee: Any = None) -> str:
    """Version texte des étapes, pour les notifications (une étape par ligne)."""
    lignes = []
    if heure_navitia(depart):
        lignes.append(f"Départ {heure_navitia(depart)}")
    for e in etapes:
        if e["type"] == "marche":
            lignes.append(f"🚶 {e['duree']} min" + (f" jusqu'à {e['vers']}" if e.get("vers") else ""))
        else:
            ligne = f"{e.get('mode') or ''} {e.get('nom') or ''}".strip()
            lignes.append(f"{e.get('depart') or '?'} {ligne} à {e.get('de')} → {e.get('vers')} ({e.get('arrivee') or '?'})")
    if heure_navitia(arrivee):
        lignes.append(f"Arrivée {heure_navitia(arrivee)}")
    return "\n".join(lignes)


def resume_itineraire(sections: list[dict[str, Any]], duree: Any = None, corresp: Any = None) -> str:
    if not sections:
        texte = "À pied"
    else:
        texte = " → ".join(f"{s.get('mode') or ''} {s.get('nom') or ''}".strip() for s in sections)
    infos = []
    if isinstance(duree, int):
        infos.append(f"{round(duree / 60)} min")
    if isinstance(corresp, int):
        infos.append("direct" if corresp == 0 else f"{corresp} corresp.")
    return texte + (" · " + " · ".join(infos) if infos else "")


_BALISES = re.compile(r"<[^>]+>")


def _texte(x: Any) -> str:
    t = html.unescape(_BALISES.sub(" ", x or ""))
    return re.sub(r"\s+", " ", t).strip()


def analyser_perturbations(donnees: Any, maintenant: datetime | None = None) -> list[dict[str, Any]]:
    """Perturbations actives maintenant (heure locale naïve, comme Navitia)."""
    maintenant = maintenant or datetime.now()
    sortie = []
    for d in (donnees or {}).get("disruptions", []) or []:
        if d.get("status") not in (None, "active"):
            continue
        periodes = d.get("application_periods") or []
        if periodes and not any(
            (date_navitia(p.get("begin")) or datetime.min) <= maintenant <= (date_navitia(p.get("end")) or datetime.max)
            for p in periodes
        ):
            continue
        messages = d.get("messages") or []
        titre = next(
            (
                _texte(m.get("text"))
                for m in messages
                if "title" in ((m.get("channel") or {}).get("types") or [])
                or "titre" in ((m.get("channel") or {}).get("name") or "").lower()
            ),
            None,
        )
        corps = max((_texte(m.get("text")) for m in messages), key=len, default="")
        gravite = d.get("severity") or {}
        lignes = [
            o.get("pt_object", {}).get("id")
            for o in d.get("impacted_objects", []) or []
            if (o.get("pt_object") or {}).get("embedded_type") == "line"
        ]
        sortie.append(
            {
                "id": d.get("id") or d.get("disruption_id"),
                "titre": titre or corps[:120],
                "message": corps,
                "effet": gravite.get("effect") or "UNKNOWN_EFFECT",
                "gravite": gravite.get("name"),
                "cause": d.get("cause"),
                "information": (gravite.get("effect") or "UNKNOWN_EFFECT") in EFFETS_INFO,
                "lignes": [l for l in lignes if l],
                "fin": next((p.get("end") for p in periodes if p.get("end")), None),
            }
        )
    return sortie


def estimation_marche(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Estimation de secours (secondes) : vol d'oiseau × détour, à 4,3 km/h."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    distance = 2 * r * math.asin(math.sqrt(a))
    return int(distance * DETOUR / VITESSE_MARCHE)


def prochain_jour_ouvre(depuis: datetime, heure: str) -> datetime:
    """Prochaine date (lun–ven) à l'heure « HH:MM[:SS] », pour proposer des itinéraires."""
    h, m, *_ = (int(x) for x in heure.split(":"))
    d = depuis.replace(hour=h, minute=m, second=0, microsecond=0)
    if d <= depuis:
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d
