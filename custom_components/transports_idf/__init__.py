"""Transports IDF : prochains passages, temps de marche et alertes trafic (PRIM)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import ClientPrim
from .const import CONF_JETON, DOMAIN, VERSION
from .coordinator import PassagesCoordinator, TraficCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
URL_CARTE = f"/{DOMAIN}/transports-card.js"


@dataclass
class Donnees:
    client: ClientPrim
    passages: PassagesCoordinator
    trafic: TraficCoordinator


type TransportsConfigEntry = ConfigEntry[Donnees]


SCHEMA_PARTIR = vol.Schema({vol.Required("trajet"): cv.entity_id})


async def _partir_maintenant(appel: ServiceCall) -> ServiceResponse:
    """Itinéraires au départ de maintenant pour le trajet dont on donne le capteur « perturbation »."""
    hass = appel.hass
    entite = er.async_get(hass).async_get(appel.data["trajet"])
    entree = hass.config_entries.async_get_entry(entite.config_entry_id) if entite else None
    if entite is None or entree is None or entite.platform != DOMAIN or not entite.config_subentry_id:
        raise ServiceValidationError(f"{appel.data['trajet']} n'est pas un trajet Transports IDF")
    if entree.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("L'intégration Transports IDF n'est pas chargée")
    resultat = await entree.runtime_data.trafic.partir_maintenant(entite.config_subentry_id)
    return resultat if appel.return_response else None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Action « Partir maintenant » et carte du tableau de bord (servie sans ressource à déclarer)."""
    hass.services.async_register(
        DOMAIN, "partir_maintenant", _partir_maintenant, schema=SCHEMA_PARTIR, supports_response=SupportsResponse.OPTIONAL
    )
    if "frontend" not in hass.config.components or getattr(hass, "http", None) is None:
        return True
    dossier = Path(__file__).parent
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_CARTE, str(dossier / "www" / "transports-card.js"), False)]
    )
    add_extra_js_url(hass, f"{URL_CARTE}?v={VERSION}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TransportsConfigEntry) -> bool:
    client = ClientPrim(async_get_clientsession(hass), entry.data[CONF_JETON])
    passages = PassagesCoordinator(hass, entry, client)
    trafic = TraficCoordinator(hass, entry, client)
    await passages.async_config_entry_first_refresh()
    await trafic.async_config_entry_first_refresh()
    entry.runtime_data = Donnees(client, passages, trafic)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Ajout, modification ou retrait d'un arrêt ou d'un trajet, options : on recharge.
    entry.async_on_unload(entry.add_update_listener(_recharger))
    return True


async def _recharger(hass: HomeAssistant, entry: TransportsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TransportsConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
