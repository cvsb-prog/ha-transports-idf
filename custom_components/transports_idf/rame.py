"""Position dans la rame (métro, quelques RER) pour descendre face à sa correspondance ou sa sortie.

Données : « Positionnement dans la rame », RATP / Île-de-France Mobilités, Licence Ouverte 2.0.
Une copie est livrée avec l'intégration ; une version plus récente est téléchargée une fois par mois.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import re
import time
import unicodedata
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

FICHIER_LIVRE = Path(__file__).parent / "donnees" / "positionnement-dans-la-rame.csv.gz"
URL = (
    "https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/"
    "positionnement-dans-la-rame/exports/csv?delimiter=%3B"
)
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


class PositionRame:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.index: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._cache = Path(hass.config.path(".storage", "transports_idf_rame.csv"))

    def _lire(self) -> tuple[str, float | None]:
        if self._cache.exists():
            return self._cache.read_text(encoding="utf-8"), self._cache.stat().st_mtime
        with gzip.open(FICHIER_LIVRE, "rt", encoding="utf-8-sig") as f:
            return f.read(), None

    async def async_charger(self) -> None:
        try:
            texte, date = await self.hass.async_add_executor_job(self._lire)
            self.index = analyser(texte)
        except (OSError, ValueError) as err:
            _LOGGER.warning("Positions dans la rame illisibles : %s", err)
            return
        if date is None or time.time() - date > VALIDITE.total_seconds():
            self.hass.async_create_background_task(self._mettre_a_jour(), "transports_idf rame")

    async def _mettre_a_jour(self) -> None:
        try:
            async with async_get_clientsession(self.hass).get(URL, timeout=60) as rep:
                rep.raise_for_status()
                texte = await rep.text(encoding="utf-8")
            index = analyser(texte)
        except Exception as err:  # noqa: BLE001 — la copie livrée reste utilisée
            _LOGGER.debug("Mise à jour des positions dans la rame impossible : %s", err)
            return
        if len(index) < 100:
            return
        self.index = index
        await self.hass.async_add_executor_job(self._ecrire, texte)

    def _ecrire(self, texte: str) -> None:
        self._cache.parent.mkdir(parents=True, exist_ok=True)
        self._cache.write_text(texte, encoding="utf-8")

    def conseil(self, etape: dict[str, Any], suivante: dict[str, Any] | None, marche: dict[str, Any] | None) -> dict[str, Any] | None:
        """Où monter pour l'étape en transport, selon ce qui suit (correspondance ou sortie)."""
        quai, ligne = numero(etape.get("vers_point")), etape.get("code")
        lignes = self.index.get((quai or "", ligne or ""))
        if not lignes:
            return None
        if suivante and suivante.get("type") == "transport":
            cible = numero(suivante.get("de_point"))
            choix = next((l for l in lignes if l["type"] == "correspondance" and l["vers_id"] == cible), None)
            if choix:
                return {**choix, "ligne_suivante": suivante.get("nom"), "mode_suivant": suivante.get("mode")}
            return None
        # Sortie : celle qui donne sur la première rue du trajet à pied.
        rues = [_norm(r) for r in (marche or {}).get("rues", []) if r]
        sorties = [l for l in lignes if l["type"] == "sortie"]
        for rue in rues:
            for l in sorties:
                if len(rue) >= 6 and rue in _norm(l["vers"]):
                    return l
        return None
