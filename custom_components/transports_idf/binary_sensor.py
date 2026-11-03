"""Un capteur binaire par trajet : « perturbé » si une de ses lignes l'est."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import TransportsConfigEntry
from .api import pire_niveau
from .const import (
    SOUS_TRAJET,
    T_AVANCE,
    T_DEST_NOM,
    T_HEURE,
    T_NOM,
    T_ORIGINE,
    T_ORIGINE_NOM,
    T_PERSONNE,
    T_RESUME,
    T_SECTIONS,
    T_TYPE_HEURE,
)
from .coordinator import (
    DUREE_MAINTENANT,
    EtatTrajet,
    TraficCoordinator,
    depart_habituel,
)
from .sensor import appareil


async def async_setup_entry(
    hass: HomeAssistant, entry: TransportsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coord = entry.runtime_data.trafic
    for sous in entry.subentries.values():
        if sous.subentry_type == SOUS_TRAJET:
            async_add_entities([TrajetSensor(coord, sous)], config_subentry_id=sous.subentry_id)


class TrajetSensor(CoordinatorEntity[TraficCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "perturbation"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _unrecorded_attributes = frozenset({"perturbations", "rechange", "sections", "reference", "alternatives", "conseil", "maintenant", "etapes_habituelles"})

    def __init__(self, coord: TraficCoordinator, sous: ConfigSubentry) -> None:
        super().__init__(coord)
        self._sous_id = sous.subentry_id
        self._attr_unique_id = f"{sous.subentry_id}_perturbation"
        self._attr_device_info = appareil(sous, "Trajet")

    @property
    def _etat(self) -> EtatTrajet:
        return self.coordinator.etats.get(self._sous_id) or EtatTrajet()

    @property
    def is_on(self) -> bool:
        return bool(self._etat.perturbations)

    @property
    def icon(self) -> str:
        return "mdi:alert-octagon" if self.is_on else "mdi:transit-connection-variant"

    def _nom_zone(self, zone: str | None) -> str | None:
        etat = self.hass.states.get(zone) if zone else None
        return etat.name if etat else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.config_entry.subentries[self._sous_id].data
        e = self._etat
        return {
            "type_transports_idf": "trajet",
            "trajet": d.get(T_NOM),
            "personne": d.get(T_PERSONNE),
            "origine": d.get(T_ORIGINE),
            "destination": d.get(T_DEST_NOM),
            "type_heure": d.get(T_TYPE_HEURE, "depart"),
            "heure": d.get(T_HEURE),
            "depart_habituel": depart_habituel(d),
            "conseil": e.conseil,
            "maintenant": e.maintenant
            if e.maintenant and dt_util.utcnow() - dt_util.parse_datetime(e.maintenant["le"]) < DUREE_MAINTENANT
            else None,
            "avance": d.get(T_AVANCE),
            "en_veille": e.en_veille,
            "itineraire": d.get(T_RESUME),
            "sections": d.get(T_SECTIONS, []),
            "perturbations": e.perturbations,
            "rechange": e.rechange,
            "reference": e.reference,
            "alternatives": e.alternatives,
            "origine_nom": d.get(T_ORIGINE_NOM) or self._nom_zone(d.get(T_ORIGINE)),
            "etapes_habituelles": self.coordinator.etapes_habituelles(self._sous_id),
            "alerte": pire_niveau(e.niveaux.values()),
        }
