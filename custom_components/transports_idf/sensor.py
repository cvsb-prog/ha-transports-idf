"""Un capteur par arrêt : prochain départ atteignable, passages en attributs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import TransportsConfigEntry
from .const import (
    A_COMMUNE,
    A_LAT,
    A_LON,
    CONF_ZONE,
    A_LIGNES,
    A_LIGNES_SUIVIES,
    A_MARCHE,
    A_MARCHE_CALCULE,
    A_NOM,
    DOMAIN,
    ETAT_ATTEIGNABLE,
    NOM,
    OPT_MARGE,
    OPT_NB_PASSAGES,
    SOUS_ARRET,
    VERSION,
)
from .coordinator import PassagesCoordinator, coordonnees_zone, option


async def async_setup_entry(
    hass: HomeAssistant, entry: TransportsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coord = entry.runtime_data.passages
    for sous in entry.subentries.values():
        if sous.subentry_type == SOUS_ARRET:
            async_add_entities([ArretSensor(coord, sous)], config_subentry_id=sous.subentry_id)


def appareil(sous: ConfigSubentry, modele: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, sous.subentry_id)},
        name=sous.title,
        manufacturer=NOM,
        model=modele,
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
    )


class ArretSensor(CoordinatorEntity[PassagesCoordinator], SensorEntity):
    """État : heure du prochain passage atteignable à pied."""

    _attr_has_entity_name = True
    _attr_translation_key = "passages"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bus-clock"
    # Liste volumineuse et changeante : inutile dans l'historique.
    _unrecorded_attributes = frozenset({"passages", "lignes"})

    def __init__(self, coord: PassagesCoordinator, sous: ConfigSubentry) -> None:
        super().__init__(coord)
        self._sous_id = sous.subentry_id
        self._attr_unique_id = f"{sous.subentry_id}_passages"
        self._attr_device_info = appareil(sous, "Arrêt")

    @property
    def _sous(self) -> ConfigSubentry:
        return self.coordinator.config_entry.subentries[self._sous_id]

    @property
    def native_value(self) -> datetime | None:
        for p in self.coordinator.passages_affiches(self._sous_id):
            if p["etat"] == ETAT_ATTEIGNABLE and not p["supprime"]:
                return dt_util.parse_datetime(p["depart"])
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._sous.data
        suivies = set(d.get(A_LIGNES_SUIVIES) or [])
        lignes = [l for l in d.get(A_LIGNES, []) if not suivies or l["id"] in suivies]
        depart = coordonnees_zone(self.hass, self.coordinator.config_entry.data.get(CONF_ZONE))
        return {
            "type_transports_idf": "arret",
            "arret": d.get(A_NOM),
            "commune": d.get(A_COMMUNE),
            "temps_marche": d.get(A_MARCHE),
            "temps_marche_calcule": d.get(A_MARCHE_CALCULE),
            "marge": option(self.coordinator.config_entry, OPT_MARGE),
            "nb_passages": option(self.coordinator.config_entry, OPT_NB_PASSAGES),
            "lignes": lignes,
            "lat": d.get(A_LAT),
            "lon": d.get(A_LON),
            "depart_lat": depart[0],
            "depart_lon": depart[1],
            "mis_a_jour": dt_util.utcnow().isoformat(),
            "passages": self.coordinator.passages_affiches(self._sous_id),
        }
