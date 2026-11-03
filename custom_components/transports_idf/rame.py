"""Position dans la rame (métro, quelques RER) pour descendre face à sa correspondance ou sa sortie.

Données (Licence Ouverte 2.0) : « Positionnement dans la rame » (RATP / Île-de-France Mobilités) et
« Référentiel des arrêts : Accès » (Île-de-France Mobilités : numéro, nom et position de chaque sortie).
Une copie est livrée avec l'intégration ; une version plus récente est téléchargée une fois par mois.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import math
import re
import time
import unicodedata
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

DOSSIER = Path(__file__).parent / "donnees"
EXPORT = "https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/{}/exports/csv?delimiter=%3B"
VALIDITE = timedelta(days=30)
COLONNES = {"from_id", "line_id", "to_type", "to_id", "to_name", "position", "position_max"}
_NUM = re.compile(r"(\d+)$")


def _norm(texte: Any) -> str:
    t = unicodedata.normalize("NFD", str(texte or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", t)


def numero(ident: str | None) -> str | None:
    """« stop_point:IDFM:22194 » → « 22194 »."""
    m = _NUM.search((ident or "").rstrip(":"))
    return m.group(1) if m else None


def zone(position: int, total: int) -> str:
    if total <= 1:
        return "milieu"
    rang = (position - 1) / (total - 1)
    return "avant" if rang < 0.34 else "arriere" if rang > 0.66 else "milieu"


def analyser(texte: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """CSV → {(quai d'arrivée, code ligne) : [destinations possibles avec la position]}."""
    lecteur = csv.DictReader(io.StringIO(texte.lstrip("﻿")), delimiter=";")
    if not COLONNES <= set(lecteur.fieldnames or []):
        raise ValueError(f"colonnes inattendues : {lecteur.fieldnames}")
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in lecteur:
        try:
            pos, total = int(r["position"]), int(r["position_max"])
        except (TypeError, ValueError):
            continue
        index.setdefault((r["from_id"], r["line_id"]), []).append(
            {
                "type": "correspondance" if r["to_type"] == "stop_point" else "sortie",
                "vers_id": r["to_id"],
                "vers": r["to_name"],
                "voiture": pos,
                "voitures": total,
                "zone": zone(pos, total),
                "equipement": (r.get("equipment_type") or "").strip() or None,
            }
        )
    return index


def analyser_acces(texte: str) -> dict[str, dict[str, Any]]:
    """Accès : format d'origine d'IDFM (AccId, AccName, AccShortName, AccGeopoint) ou copie compacte livrée."""
    lecteur = csv.DictReader(io.StringIO(texte.lstrip("\ufeff")), delimiter=";")
    champs = set(lecteur.fieldnames or [])
    origine = {"AccId", "AccName", "AccShortName"} <= champs
    if not origine and not {"id", "nom", "numero"} <= champs:
        raise ValueError(f"colonnes inattendues : {lecteur.fieldnames}")
    index = {}
    for r in lecteur:
        if origine:
            ident, nom, num = r["AccId"], r["AccName"], r["AccShortName"]
            try:
                lat, lon = (float(x) for x in (r.get("AccGeopoint") or "").split(","))
            except ValueError:
                lat = lon = None
        else:
            ident, nom, num = r["id"], r["nom"], r["numero"]
            try:
                lat, lon = float(r["lat"]), float(r["lon"])
            except (TypeError, ValueError):
                lat = lon = None
        index[ident] = {"nom": nom or None, "numero": (num or "").strip() or None, "lat": lat, "lon": lon}
    return index


def distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    dy = (lat2 - lat1) * 110_540
    return math.hypot(dx, dy)


ORDRE_EQUIPEMENT = {"Escalator": 0, None: 1, "Escalier": 2, "Ascenseur": 3}


def _prefere(choix: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Plusieurs positions pour la même destination (escalator, escalier, ascenseur…) : on garde la plus pratique."""
    return min(choix, key=lambda l: ORDRE_EQUIPEMENT.get(l.get("equipement"), 1), default=None)


class Jeu:
    """Un fichier de données : copie livrée, cache local, mise à jour mensuelle."""

    def __init__(self, hass: HomeAssistant, nom: str, livre: str, dataset: str, lire) -> None:
        self.hass, self.nom, self.dataset, self._lire = hass, nom, dataset, lire
        self.livre = DOSSIER / livre
        self.cache = Path(hass.config.path(".storage", f"transports_idf_{nom}.csv"))
        self.index: dict = {}

    def _ouvrir(self) -> tuple[str, float | None]:
        if self.cache.exists():
            return self.cache.read_text(encoding="utf-8"), self.cache.stat().st_mtime
        with gzip.open(self.livre, "rt", encoding="utf-8-sig") as f:
            return f.read(), None

    async def async_charger(self) -> None:
        try:
            texte, date = await self.hass.async_add_executor_job(self._ouvrir)
            self.index = self._lire(texte)
        except (OSError, ValueError) as err:
            _LOGGER.warning("Données « %s » illisibles : %s", self.nom, err)
            return
        if date is None or time.time() - date > VALIDITE.total_seconds():
            self.hass.async_create_background_task(self._mettre_a_jour(), f"transports_idf {self.nom}")

    async def _mettre_a_jour(self) -> None:
        try:
            async with async_get_clientsession(self.hass).get(EXPORT.format(self.dataset), timeout=60) as rep:
                rep.raise_for_status()
                texte = await rep.text(encoding="utf-8")
            index = self._lire(texte)
        except Exception as err:  # noqa: BLE001 — la copie livrée reste utilisée
            _LOGGER.debug("Mise à jour de « %s » impossible : %s", self.nom, err)
            return
        if len(index) < 100:
            return
        self.index = index
        await self.hass.async_add_executor_job(self._ecrire, texte)

    def _ecrire(self, texte: str) -> None:
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(texte, encoding="utf-8")


class PositionRame:
    def __init__(self, hass: HomeAssistant) -> None:
        self.positions = Jeu(hass, "rame", "positionnement-dans-la-rame.csv.gz", "positionnement-dans-la-rame", analyser)
        self.acces = Jeu(hass, "acces", "acces.csv.gz", "acces", analyser_acces)

    @property
    def index(self) -> dict:
        return self.positions.index

    async def async_charger(self) -> None:
        await self.positions.async_charger()
        await self.acces.async_charger()

    def conseil(self, etape: dict[str, Any], suivante: dict[str, Any] | None, marche: dict[str, Any] | None) -> dict[str, Any] | None:
        """Où monter pour l'étape en transport, selon ce qui suit (correspondance ou sortie)."""
        quai, ligne = numero(etape.get("vers_point")), etape.get("code")
        lignes = self.index.get((quai or "", ligne or ""))
        if not lignes:
            return None
        if suivante and suivante.get("type") == "transport":
            cible = numero(suivante.get("de_point"))
            choix = _prefere([l for l in lignes if l["type"] == "correspondance" and l["vers_id"] == cible])
            if choix:
                return {**choix, "ligne_suivante": suivante.get("nom"), "mode_suivant": suivante.get("mode")}
            if not marche:
                return None
            # Pas de correspondance interne (changement de station à pied) : on sort, au plus près de la suite.
        # Sortie : la plus proche de la fin du trajet à pied, sinon celle qui donne sur sa première rue.
        sorties = [{**l, **{k: v for k, v in (self.acces.index.get(l["vers_id"]) or {}).items() if v is not None}} for l in lignes if l["type"] == "sortie"]
        if not sorties:
            return None
        fin = (marche or {}).get("vers_coord")
        avec_gps = [l for l in sorties if l.get("lat") is not None]
        if fin and avec_gps:
            proche = min(avec_gps, key=lambda l: distance(fin[0], fin[1], l["lat"], l["lon"]))
            choix = _prefere([l for l in avec_gps if l["vers_id"] == proche["vers_id"]])
            return {k: v for k, v in choix.items() if k not in ("lat", "lon")}
        rues = [_norm(r) for r in (marche or {}).get("rues", []) if r]
        for rue in rues:
            for l in sorties:
                if len(rue) >= 6 and (rue in _norm(l["vers"]) or rue in _norm(l.get("nom"))):
                    return {k: v for k, v in l.items() if k not in ("lat", "lon")}
        return None
