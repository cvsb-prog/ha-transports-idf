"""L'automatisation d'alerte : bonne personne, bons téléphones, jours travaillés seulement."""

from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

AUTO = Path(__file__).parent.parent / "examples" / "transports-alertes-de-trajet.yaml"

DONNEES = {
    "type": "perturbation",
    "trajet": "Travail",
    "personne": "person.alex",
    "heure": "08:00:00",
    "type_heure": "depart",
    "depart_habituel": "08:00:00",
    "conseil": None,
    "itineraire": "Bus 192 → RER B",
    "nouvelles": [{"id": "d1", "titre": "RER B : trafic interrompu", "lignes_trajet": ["RER B"]}],
    "perturbations": [{"id": "d1", "titre": "RER B : trafic interrompu", "lignes_trajet": ["RER B"]}],
    "rechange": {"resume": "Tramway T6 · 40 min · direct", "texte": "Départ 8 h 05\n8 h 06 Tramway T6 à Châtillon → Vélizy (8 h 40)"},
    "sous_entree": "abc",
}


async def test_automatisation(hass: HomeAssistant):
    entree = MockConfigEntry(domain="mobile_app")
    entree.add_to_hass(hass)
    appareils, entites = dr.async_get(hass), er.async_get(hass)
    for nom, ident in (("Pixel Alex", "p1"), ("iPhone Sam", "p2")):
        app = appareils.async_get_or_create(config_entry_id=entree.entry_id, identifiers={("mobile_app", ident)}, name=nom)
        entites.async_get_or_create("device_tracker", "mobile_app", ident, device_id=app.id, suggested_object_id=ident)
    hass.states.async_set("device_tracker.p1", "home")
    hass.states.async_set("person.alex", "home", {"device_trackers": ["device_tracker.badge", "device_tracker.p1"]})
    hass.states.async_set("binary_sensor.workday_sensor", "on")
    appels = async_mock_service(hass, "notify", "mobile_app_pixel_alex")
    autres = async_mock_service(hass, "notify", "mobile_app_iphone_sam")

    config = await hass.async_add_executor_job(yaml_util.load_yaml, AUTO)
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()

    hass.bus.async_fire("transports_idf_alerte", DONNEES)
    await hass.async_block_till_done()
    assert len(appels) == 1 and not autres
    assert appels[0].data["title"] == "⚠️ Travail (départ 8 h 00) : perturbation"
    assert appels[0].data["message"] == "RER B — RER B : trafic interrompu\nItinéraire de rechange (Tramway T6 · 40 min · direct) :\nDépart 8 h 05\n8 h 06 Tramway T6 à Châtillon → Vélizy (8 h 40)"
    assert appels[0].data["data"]["tag"] == "transports_abc"

    hass.bus.async_fire("transports_idf_alerte", {**DONNEES, "type": "retour_normal", "nouvelles": [], "perturbations": [], "rechange": None})
    await hass.async_block_till_done()
    assert appels[1].data["title"] == "✅ Travail (départ 8 h 00) : trafic normal"
    assert appels[1].data["message"] == "Plus aucune perturbation sur Bus 192 → RER B."

    hass.states.async_set("binary_sensor.workday_sensor", "off")
    hass.bus.async_fire("transports_idf_alerte", DONNEES)
    await hass.async_block_till_done()
    assert len(appels) == 2


async def test_depart_anticipe(hass: HomeAssistant):
    hass.states.async_set("person.alex", "home", {"device_trackers": []})
    hass.states.async_set("binary_sensor.workday_sensor", "on")
    appels = async_mock_service(hass, "persistent_notification", "create")
    config = await hass.async_add_executor_job(yaml_util.load_yaml, AUTO)
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    hass.bus.async_fire("transports_idf_alerte", {**DONNEES, "type": "depart_anticipe", "type_heure": "arrivee", "heure": "09:00:00",
        "depart_habituel": "08:20:00", "nouvelles": [], "perturbations": [],
        "conseil": {"depart_h": "8 h 06", "avance": 14, "resume": "Bus 192 → RER B", "texte": "Départ 8 h 06\n🚶 5 min jusqu'à Les Blagis"}})
    await hass.async_block_till_done()
    assert appels[0].data["title"] == "⏰ Travail : pars à 8 h 06 au plus tard"
    assert appels[0].data["message"].startswith("Pour arriver à 9 h 00, 14 min plus tôt que d'habitude (départ habituel 8 h 20) :\nDépart 8 h 06")
