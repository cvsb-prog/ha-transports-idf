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
    T_DEST_NOM,
    T_HEURE,
    T_NOM,
    T_ORIGINE,
    T_ORIGINE_NOM,
    T_PERSONNE,
    T_RESUME,
    T_SECTIONS,
    T_TYPE_HEURE,
    VEILLE_MINUTES,
)
from .sensor import appareil
from .trajets import (
    DUREE_DIRECT,
    EtatTrajet,
    TraficCoordinator,
    aussi_feries,
    heure_depart_maison,
    jours_trajet,
)


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
    _unrecorded_attributes = frozenset({"perturbations", "rechange", "sections", "nominal", "direct", "rappel", "etapes_habituelles", "detour"})

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
            "depart_habituel": heure_depart_maison(d),
            "jours": sorted(jours_trajet(d)),
            "jours_feries": aussi_feries(d),
            "avance": VEILLE_MINUTES,
            "en_veille": e.en_veille,
            "itineraire": d.get(T_RESUME),
            "sections": d.get(T_SECTIONS, []),
            "perturbations": e.perturbations,
            "nominal": e.nominal,
            "direct": e.direct if e.direct_le and (e.en_veille or dt_util.now().replace(tzinfo=None) - e.direct_le < DUREE_DIRECT) else [],
            "direct_le": dt_util.as_utc(e.direct_le.replace(tzinfo=dt_util.get_default_time_zone())).isoformat() if e.direct_le else None,
            "rappel": e.rappel,
            "rechange": e.rechange,
            "detour": {k[7:]: v for k, v in d.items() if k.startswith("detour_")} or None,
            "origine_nom": d.get(T_ORIGINE_NOM) or self._nom_zone(d.get(T_ORIGINE)),
            "etapes_habituelles": self.coordinator.etapes_habituelles(self._sous_id),
            "alerte": pire_niveau(e.niveaux.values()),
        }
