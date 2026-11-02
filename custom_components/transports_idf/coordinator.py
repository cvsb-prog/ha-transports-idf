"""Coordinateurs : prochains passages (arrêts) et info trafic (trajets)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ClientPrim, ErreurJeton, ErreurPrim, date_navitia
from .const import (
    A_LIGNES,
    A_LIGNES_SUIVIES,
    A_MARCHE,
    A_REFS,
    CONF_ZONE,
    DEFAUTS,
    DOMAIN,
    EFFETS_BLOQUANTS,
    ETAT_ATTEIGNABLE,
    ETAT_PRESSE,
    ETAT_TROP_TARD,
    EVENEMENT_ALERTE,
    OPT_DEBUT_NUIT,
    OPT_FIN_NUIT,
    OPT_IGNORER_INFO,
    OPT_INTERVALLE_JOUR,
    OPT_INTERVALLE_NUIT,
    OPT_MARGE,
    SOUS_ARRET,
    SOUS_TRAJET,
    T_AVANCE,
    T_DEST_ID,
    T_DEPART_HABITUEL,
    T_DUREE,
    T_HEURE,
    T_TYPE_HEURE,
    ECART_MINUTES,
    T_NOM,
    T_ORIGINE,
    T_PERSONNE,
    T_RESUME,
    T_SECTIONS,
)

_LOGGER = logging.getLogger(__name__)

HORIZON = timedelta(minutes=120)  # passages gardés au-delà de maintenant
MAX_PASSAGES = 40
TRAFIC_VEILLE = timedelta(minutes=2)
TRAFIC_REPOS = timedelta(minutes=20)
APRES_DEPART = timedelta(minutes=10)  # la veille se prolonge un peu après l'heure prévue
VALIDITE_RECHANGE = timedelta(minutes=10)
GAIN_MINIMUM = timedelta(minutes=3)  # en dessous, le trajet habituel reste conseillé


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
    rechange: dict[str, Any] | None = None
    reference: dict[str, Any] | None = None
    plus_rapides: list[dict[str, Any]] = field(default_factory=list)
    calcule_le: datetime | None = None
    conseil: dict[str, Any] | None = None  # mode « arrivée » : itinéraire à prendre au plus tard
    depart_notifie: datetime | None = None
    en_veille: bool = False
    notifiees: set[str] = field(default_factory=set)


class TraficCoordinator(DataUpdateCoordinator[dict[str, EtatTrajet]]):
    """Info trafic des lignes des trajets, et alertes pendant la veille de chaque trajet."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ClientPrim) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} trafic", update_interval=TRAFIC_REPOS)
        self.client = client
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
            for lid, sec in noms.items():
                for p in par_ligne.get(lid, []):
                    if ignorer_info and p["information"]:
                        continue
                    d = perts.setdefault(p["id"], {**p, "lignes_trajet": [], "_ids": []})
                    nom = f"{sec.get('mode') or ''} {sec.get('nom') or ''}".strip()
                    if nom not in d["lignes_trajet"]:
                        d["lignes_trajet"].append(nom)
                        d["_ids"].append(lid)
            # Itinéraire de rechange si une ligne du trajet est bloquée ou très perturbée.
            bloquees = sorted({lid for p in perts.values() if p["effet"] in EFFETS_BLOQUANTS for lid in p["_ids"]})
            etat.perturbations = [{k: v for k, v in p.items() if k != "_ids"} for p in perts.values()]
            etat.en_veille = self.fenetre(depart_habituel(t.data), int(t.data.get(T_AVANCE, 60)), maintenant)
            une_veille |= etat.en_veille
            # Itinéraires en direct : à chaque rafraîchissement pendant la veille,
            # toutes les 10 min hors veille si une ligne du trajet est bloquée.
            if etat.en_veille or (bloquees and (etat.calcule_le is None or maintenant - etat.calcule_le > VALIDITE_RECHANGE)):
                await self._comparer(t, etat, bloquees)
                etat.calcule_le = maintenant
            elif not etat.en_veille and not bloquees:
                etat.rechange, etat.reference, etat.plus_rapides, etat.calcule_le, etat.conseil = None, None, [], None, None
            self._alerter(t, etat)

        for sid in list(self.etats):
            if sid not in {t.subentry_id for t in trajets}:
                del self.etats[sid]
        self.update_interval = TRAFIC_VEILLE if une_veille else TRAFIC_REPOS
        return dict(self.etats)

    async def _comparer(self, trajet, etat: EtatTrajet, bloquees: list[str]) -> None:
        """Trajet habituel recalculé maintenant, itinéraires plus intéressants, conseil de départ.

        Mode « départ » : itinéraires au départ de maintenant, comparés sur l'heure d'arrivée.
        Mode « arrivée » : itinéraires arrivant au plus tard à l'heure cible, comparés sur l'heure
        de départ (plus tard = mieux) ; le conseil est le départ le plus tardif qui arrive à l'heure.
        """
        d = trajet.data
        arrivee_cible = d.get(T_TYPE_HEURE) == "arrivee"
        lat, lon = coordonnees_zone(self.hass, d.get(T_ORIGINE) or self.config_entry.data.get(CONF_ZONE))
        depuis, vers = f"{lon};{lat}", d[T_DEST_ID]
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
        if lignes_ref and not set(lignes_ref) & set(bloquees):
            appels.append(self.client.itineraires(depuis, vers, quand, autorises=lignes_ref, represente=represente))
        resultats = await asyncio.gather(*appels, return_exceptions=True)
        for r in resultats:
            if isinstance(r, Exception):
                _LOGGER.debug("Itinéraires du trajet %s : %s", trajet.title, r)
        options = resultats[0] if isinstance(resultats[0], list) else []
        refs = resultats[1] if len(resultats) > 1 and isinstance(resultats[1], list) else []

        def lignes(o):
            return [s["ligne_id"] for s in o["sections"]]

        def depart(o):
            return date_navitia(o.get("depart"))

        def arrivee(o):
            return date_navitia(o.get("arrivee"))

        habituels = [o for o in refs + options if lignes(o) == lignes_ref]
        if arrivee_cible:
            # Le plus tardif qui arrive à l'heure.
            habituels = [o for o in habituels if arrivee(o) and arrivee(o) <= quand]
            etat.reference = max(habituels, key=depart, default=None)
        else:
            etat.reference = min(habituels, key=lambda o: arrivee(o) or datetime.max, default=None)
        autres = [o for o in options if lignes(o) != lignes_ref and not set(lignes(o)) & set(bloquees) and o["sections"]]
        if arrivee_cible:
            autres = [o for o in autres if arrivee(o) and arrivee(o) <= quand]
            autres.sort(key=depart, reverse=True)
        else:
            autres.sort(key=lambda o: arrivee(o) or datetime.max)

        if bloquees:
            etat.rechange = autres[0] if autres else {"resume": "Aucun itinéraire de rechange trouvé", "etapes": [], "texte": ""}
        else:
            etat.rechange = None

        etat.plus_rapides = []
        ref = etat.reference
        if ref:
            for o in autres:
                if arrivee_cible:
                    gain = (depart(o) - depart(ref)).total_seconds() / 60 if depart(o) and depart(ref) else 0
                else:
                    gain = (arrivee(ref) - arrivee(o)).total_seconds() / 60 if arrivee(o) and arrivee(ref) else 0
                if gain >= ECART_MINUTES:
                    etat.plus_rapides.append({**o, "gain": round(gain)})
            etat.plus_rapides = etat.plus_rapides[:2]

        etat.conseil = None
        if arrivee_cible:
            candidats = ([ref] if ref else []) + autres
            meilleur = max(candidats, key=depart, default=None)
            if meilleur:
                habituel = date_navitia(quand.strftime("%Y%m%dT") + (d.get(T_DEPART_HABITUEL) or "00:00:00").replace(":", ""))
                avance = round((habituel - depart(meilleur)).total_seconds() / 60) if d.get(T_DEPART_HABITUEL) and depart(meilleur) else 0
                etat.conseil = {
                    **meilleur,
                    "habituel": meilleur is ref,
                    "arrivee_cible": f"{quand.hour} h {quand.minute:02d}",
                    "avance": avance,  # minutes plus tôt que le départ habituel (négatif = plus tard)
                }
            else:
                etat.conseil = {"resume": "Aucun itinéraire n'arrive à l'heure", "etapes": [], "texte": "", "arrivee_cible": f"{quand.hour} h {quand.minute:02d}", "avance": None, "impossible": True}

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
            dep = date_navitia(c.get("depart"))
            if c.get("impossible"):
                dep = datetime.min
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
            "plus_rapides": etat.plus_rapides,
            "sous_entree": trajet.subentry_id,
        }
        self.hass.bus.async_fire(EVENEMENT_ALERTE, donnees)
