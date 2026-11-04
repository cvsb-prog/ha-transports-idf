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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
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
    heure_navitia,
    niveau_perturbation,
    pire_niveau,
    resume_itineraire,
    texte_itineraire,
)
from .const import (
    DOMAIN,
    ECART_MINUTES,
    EFFETS_BLOQUANTS,
    EVENEMENT_ALERTE,
    JOURS_DEFAUT,
    OPT_IGNORER_INFO,
    SOUS_TRAJET,
    T_AVANCE,
    T_DEST_ID,
    T_DET_DUREE,
    T_DET_HEURE,
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
)
from .coordinator import Itineraires, _heure, option, origine_navitia

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


def jours_trajet(d: dict[str, Any]) -> set[int]:
    return {int(j) for j in (d.get(T_JOURS) or JOURS_DEFAUT)}


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


class TraficCoordinator(DataUpdateCoordinator[dict[str, EtatTrajet]]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ClientPrim, calcul: Itineraires) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} trajets", update_interval=REPOS)
        self.client = client
        self.calcul = calcul
        self.etats: dict[str, EtatTrajet] = {}

    def _trajets(self):
        return [s for s in self.config_entry.subentries.values() if s.subentry_type == SOUS_TRAJET]

    # ---- Calendrier ---------------------------------------------------------
    @staticmethod
    def fenetre(heure: str, avance: int, maintenant: datetime, jours: set[int] | None = None) -> bool:
        """Vrai si maintenant est dans [départ − avance ; départ + 10 min] (heure locale), un jour du trajet."""
        base = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
        h = _heure(heure)
        for decalage in (-1, 0, 1):
            d = base + timedelta(days=decalage, hours=h.hour, minutes=h.minute)
            if jours is not None and d.weekday() not in jours:
                continue
            if d - timedelta(minutes=avance) <= maintenant <= d + APRES_DEPART:
                return True
        return False

    def prochaine_occurrence(self, d: dict[str, Any], maintenant: datetime) -> date:
        """Jour de la prochaine occurrence du trajet (aujourd'hui tant que le départ habituel n'est pas passé)."""
        h = _heure(heure_depart_maison(d))
        jours = jours_trajet(d)
        for n in range(8):
            jour = (maintenant + timedelta(days=n)).date()
            if jour.weekday() not in jours:
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

    async def _plans(self, t, jour: date, quand: datetime | None, lignes: list[str] | None, bloquees: list[str], nombre: int = 1) -> list[dict[str, Any]]:
        """Itinéraires complets (détour compris) pour un jour donné.

        quand=None : on suit l'heure du trajet (arrivée cible ou départ) — nominal et rappel.
        quand=datetime : départ de la maison à cette heure — calcul au départ de maintenant.
        """
        d = t.data
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
    async def _async_update_data(self) -> dict[str, EtatTrajet]:
        trajets = self._trajets()
        maintenant = dt_util.now().replace(tzinfo=None)
        lignes = sorted({sec["ligne_id"] for t in trajets for sec in t.data.get(T_SECTIONS, []) if sec.get("ligne_id")})
        resultats = await asyncio.gather(*(self.client.perturbations_ligne(l) for l in lignes), return_exceptions=True)
        if any(isinstance(r, ErreurJeton) for r in resultats):
            raise ConfigEntryAuthFailed("Jeton PRIM refusé")
        if lignes and all(isinstance(r, Exception) for r in resultats):
            raise UpdateFailed(f"Info trafic indisponible : {resultats[0]}")
        par_ligne = {l: r for l, r in zip(lignes, resultats, strict=True) if isinstance(r, list)}

        ignorer_info = option(self.config_entry, OPT_IGNORER_INFO)
        une_veille = False
        for t in trajets:
            etat = self.etats.setdefault(t.subentry_id, EtatTrajet())
            self._perturbations(t, etat, par_ligne, ignorer_info)
            etat.en_veille = self.fenetre(heure_depart_maison(t.data), int(t.data.get(T_AVANCE, 60)), maintenant, jours_trajet(t.data))
            une_veille |= etat.en_veille
            await self._nominal(t, etat, maintenant)
            if etat.en_veille:
                await self._direct(t, etat, maintenant)
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

    def _perturbations(self, t, etat: EtatTrajet, par_ligne: dict, ignorer_info: bool) -> None:
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
        etat.bloquees = sorted({lid for p in perts.values() if p["effet"] in EFFETS_BLOQUANTS for lid in p["_ids"]})
        etat.perturbations = [{k: v for k, v in p.items() if k != "_ids"} for p in perts.values()]
        etat.niveaux = {lid: pire_niveau(n) for lid, n in niveaux.items()}

    def _preferees(self, t) -> list[str]:
        return [s["ligne_id"] for s in t.data.get(T_SECTIONS, []) if s.get("ligne_id")]

    async def _enrichir(self, its: list[dict[str, Any]], etat: EtatTrajet) -> None:
        self.calcul.alertes(its, etat.niveaux)
        await self.calcul.suivants(its)

    async def _nominal(self, t, etat: EtatTrajet, maintenant: datetime) -> None:
        """Préféré pour la prochaine occurrence : une fois par jour, et à chaque mise à jour pendant la veille."""
        jour = self.prochaine_occurrence(t.data, maintenant)
        if not etat.en_veille and etat.nominal_jour == jour and etat.nominal_le and maintenant - etat.nominal_le < timedelta(hours=12):
            self.calcul.alertes([etat.nominal] if etat.nominal else [], etat.niveaux)
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
            self._plans(t, jour, maintenant, self._preferees(t), [], 1),
            self._plans(t, jour, maintenant, None, etat.bloquees, NB_DIRECT),
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
        if jour.weekday() not in jours_trajet(d):
            etat.rappel = None
            return
        plans = await self._plans(t, jour, None, None, etat.bloquees, 3)
        if d.get(T_TYPE_HEURE) == "arrivee":
            meilleur = max(plans, key=_dep, default=None)
        else:
            meilleur = min(plans, key=lambda o: _arr(o) or datetime.max, default=None)
        cible = datetime.combine(jour, _heure(d[T_HEURE]))
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
            "depart_iso": dt_util.as_utc(depart.replace(tzinfo=dt_util.get_default_time_zone())).isoformat(),
            # > 0 : partir plus tôt que d'habitude (seulement pour une heure d'arrivée ou un détour à heure imposée)
            "avance": round((habituel - depart).total_seconds() / 60)
            if d.get(T_TYPE_HEURE) == "arrivee" or (avec_detour(d, jour) and d.get(T_DET_HEURE))
            else 0,
            "retard": d.get(T_TYPE_HEURE) == "arrivee" and bool(_arr(meilleur) and _arr(meilleur) > cible),
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
            self.async_update_listeners()
        return {"le": dt_util.utcnow().isoformat(), "itineraires": etat.direct}

    # ---- Alertes -------------------------------------------------------------
    def _alerter(self, t, etat: EtatTrajet) -> None:
        if not etat.en_veille:
            etat.notifiees = set()
            etat.depart_notifie = None
            return
        ids = {p["id"] for p in etat.perturbations}
        nouvelles = ids - etat.notifiees
        type_ = "perturbation" if nouvelles else "retour_normal" if not ids and etat.notifiees else None
        etat.notifiees = ids
        if type_:
            self._envoyer(t, etat, type_, nouvelles)
        r = etat.rappel
        if r and (r.get("impossible") or r.get("retard") or (r.get("avance") or 0) >= ECART_MINUTES):
            dep = datetime.min if (r.get("impossible") or r.get("retard")) else date_navitia(r.get("depart"))
            if etat.depart_notifie is None or (dep and dep <= etat.depart_notifie - timedelta(minutes=ECART_MINUTES)):
                etat.depart_notifie = dep
                self._envoyer(t, etat, "depart_anticipe", set())

    def _envoyer(self, t, etat: EtatTrajet, type_: str, nouvelles: set[str]) -> None:
        r = etat.rappel or {}
        conseil = {**r, "impossible": bool(r.get("impossible") or r.get("retard"))} if r else None
        self.hass.bus.async_fire(
            EVENEMENT_ALERTE,
            {
                "type": type_,
                "trajet": t.data.get(T_NOM) or t.title,
                "personne": t.data.get(T_PERSONNE),
                "heure": t.data.get(T_HEURE),
                "type_heure": t.data.get(T_TYPE_HEURE, "depart"),
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
        etapes = [dict(e) for e in sous.data.get(T_ETAPES) or []]
        if not etapes:
            etapes = [{"type": "transport", **{k: s.get(k) for k in ("nom", "mode", "couleur", "couleur_texte", "ligne_id", "de", "vers", "direction")}} for s in sous.data.get(T_SECTIONS, [])]
        for e in etapes:
            if e.get("type") == "transport":
                e["alerte"] = etat.niveaux.get(e.get("ligne_id")) or self.calcul.niveau_ligne(e.get("ligne_id"))
        return etapes
