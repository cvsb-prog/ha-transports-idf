"""Transports IDF : prochains passages, temps de marche et alertes trafic (PRIM)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .api import ClientPrim, ErreurPrim
from .const import CONF_JETON, CONF_ZONE, DOMAIN, VERSION
from .coordinator import (
    InfoTraficCoordinator,
    Itineraires,
    PassagesCoordinator,
    coordonnees_zone,
)
from .rame import PositionRame
from .trajets import TraficCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
URL_CARTE = f"/{DOMAIN}/transports-card.js"


@dataclass
class Donnees:
    client: ClientPrim
    passages: PassagesCoordinator
    trafic: TraficCoordinator
    info: InfoTraficCoordinator
    calcul: Itineraires


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
    resultat = await entree.runtime_data.trafic.calculer_maintenant(entite.config_subentry_id)
    return resultat if appel.return_response else None


SCHEMA_LIEU = vol.Schema({vol.Required("texte"): cv.string})
SCHEMA_ITINERAIRE = vol.Schema(
    {
        vol.Required("arrivee"): cv.string,
        vol.Optional("depart"): cv.string,  # lieu Navitia, zone (zone.xxx) ; défaut : zone de l'intégration
        vol.Optional("heure"): cv.string,  # « HH:MM » ; défaut : maintenant
        vol.Optional("date"): cv.date,  # jour du trajet ; défaut : aujourd'hui (demain si l'heure est passée)
        vol.Optional("type_heure", default="depart"): vol.In(["depart", "arrivee"]),
    }
)


def _entree_chargee(hass: HomeAssistant) -> TransportsConfigEntry:
    for entree in hass.config_entries.async_entries(DOMAIN):
        if entree.state is ConfigEntryState.LOADED:
            return entree
    raise ServiceValidationError("L'intégration Transports IDF n'est pas chargée")


async def _rechercher_lieu(appel: ServiceCall) -> ServiceResponse:
    """Adresses, arrêts et lieux correspondant au texte (pour la recherche de la carte)."""
    entree = _entree_chargee(appel.hass)
    texte = appel.data["texte"].strip()
    if len(texte) < 2:
        return {"lieux": []}
    try:
        lieux = await entree.runtime_data.client.lieux(texte, ["stop_area", "address", "poi", "administrative_region"])
    except ErreurPrim as err:
        raise HomeAssistantError(f"Recherche PRIM impossible : {err}") from err
    return {"lieux": lieux[:8]}


async def _itineraire(appel: ServiceCall) -> ServiceResponse:
    """Itinéraires détaillés (étapes, départs suivants, alertes) entre deux lieux."""
    hass, d = appel.hass, appel.data
    entree = _entree_chargee(hass)
    depart = d.get("depart") or entree.data.get(CONF_ZONE)
    if depart.startswith("zone."):
        lat, lon = coordonnees_zone(hass, depart)
        depart = f"{lon};{lat}"
    maintenant = dt_util.now().replace(tzinfo=None, second=0, microsecond=0)
    quand = maintenant
    if d.get("date") and not d.get("heure"):
        quand = maintenant if d["date"] == maintenant.date() else datetime.combine(d["date"], time(8, 0))
    if d.get("heure"):
        h, m = (int(x) for x in d["heure"].split(":")[:2])
        if d.get("date"):
            quand = datetime.combine(d["date"], time(h, m))
        else:
            cible = maintenant.replace(hour=h, minute=m)
            quand = cible if cible >= maintenant - timedelta(minutes=1) else cible + timedelta(days=1)
    represente = "arrival" if d["type_heure"] == "arrivee" else "departure"
    calcul = entree.runtime_data.calcul
    try:
        its = await entree.runtime_data.client.itineraires(depart, d["arrivee"], quand, represente=represente)
    except ErreurPrim as err:
        raise HomeAssistantError(f"Calculateur PRIM indisponible : {err}") from err
    its = [i for i in its if i["etapes"]][:4]
    try:
        await calcul.alertes(its)
    except ErreurPrim as err:
        raise HomeAssistantError(f"Info trafic PRIM indisponible : {err}") from err
    await calcul.suivants(its)
    return {"itineraires": its}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Actions et carte du tableau de bord (servie sans ressource à déclarer)."""
    hass.services.async_register(
        DOMAIN, "partir_maintenant", _partir_maintenant, schema=SCHEMA_PARTIR, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, "rechercher_lieu", _rechercher_lieu, schema=SCHEMA_LIEU, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "itineraire", _itineraire, schema=SCHEMA_ITINERAIRE, supports_response=SupportsResponse.ONLY)
    if "frontend" not in hass.config.components or getattr(hass, "http", None) is None:
        return True
    dossier = Path(__file__).parent
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(URL_CARTE, str(dossier / "www" / "transports-card.js"), False),
            # Leaflet (cartes des trajets à pied), servi avec la carte : rien à charger depuis Internet.
            StaticPathConfig(f"/{DOMAIN}/leaflet", str(dossier / "www" / "leaflet"), True),
        ]
    )
    add_extra_js_url(hass, f"{URL_CARTE}?v={VERSION}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TransportsConfigEntry) -> bool:
    client = ClientPrim(async_get_clientsession(hass), entry.data[CONF_JETON])
    calcul = Itineraires(hass, client)
    passages = PassagesCoordinator(hass, entry, client)
    info = InfoTraficCoordinator(hass, entry, client)
    trafic = TraficCoordinator(hass, entry, client, calcul)
    calcul.perturbations_info = info.perturbations
    calcul.rame = PositionRame(hass)
    await calcul.rame.async_charger()
    await passages.async_config_entry_first_refresh()
    await info.async_config_entry_first_refresh()
    await trafic.async_config_entry_first_refresh()
    entry.runtime_data = Donnees(client, passages, trafic, info, calcul)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Ajout, modification ou retrait d'un arrêt ou d'un trajet, options : on recharge.
    entry.async_on_unload(entry.add_update_listener(_recharger))
    return True


async def _recharger(hass: HomeAssistant, entry: TransportsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TransportsConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
