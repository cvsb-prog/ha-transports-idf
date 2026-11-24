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


async def test_trajet_a_etapes_en_retard(hass: HomeAssistant):
    """Trajet à étapes, borne intenable : le titre donne l'écart au point concerné, pas « arrivée à <départ> impossible »."""
    hass.states.async_set("person.alex", "home", {"device_trackers": []})
    hass.states.async_set("binary_sensor.workday_sensor", "on")
    appels = async_mock_service(hass, "persistent_notification", "create")
    config = await hass.async_add_executor_job(yaml_util.load_yaml, AUTO)
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    hass.bus.async_fire("transports_idf_alerte", {**DONNEES, "type": "depart_anticipe", "type_heure": "depart", "heure": "21:44:00",
        "depart_habituel": "21:44:00", "nouvelles": [], "perturbations": [], "ecarts": ["École 22 h 05 (+5 min)"],
        "conseil": {"depart_h": "21 h 46", "impossible": True, "texte": "Départ 21 h 46"}})
    await hass.async_block_till_done()
    assert appels[0].data["title"] == "⛔ Travail : en retard — École 22 h 05 (+5 min)"
    assert appels[0].data["message"].startswith("Au mieux, en partant à 21 h 46 : École 22 h 05 (+5 min).\nDépart 21 h 46")


async def test_rappel_dix_minutes_avant(hass: HomeAssistant):
    hass.states.async_set("person.alex", "home", {"device_trackers": []})
    hass.states.async_set("binary_sensor.workday_sensor", "on")
    appels = async_mock_service(hass, "persistent_notification", "create")
    config = await hass.async_add_executor_job(yaml_util.load_yaml, AUTO)
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    hass.bus.async_fire("transports_idf_alerte", {**DONNEES, "type": "rappel_depart", "type_heure": "arrivee", "heure": "09:00:00",
        "depart_habituel": "08:20:00", "nouvelles": [], "perturbations": [],
        "conseil": {"depart_h": "8 h 20", "resume": "Bus 192 → RER B", "texte": "Départ 8 h 20\n🚶 5 min jusqu'à Les Blagis"}})
    await hass.async_block_till_done()
    assert appels[0].data["title"] == "🚶 Travail : pars à 8 h 20"
    assert appels[0].data["message"].startswith("Bus 192 → RER B\nDépart 8 h 20")


async def test_retard_ou_arrivee_prevue_dans_toutes_les_alertes(hass: HomeAssistant):
    """Chaque alerte finit par l'arrivée prévue, ou le retard prévu (destination, étapes)."""
    hass.states.async_set("person.alex", "home", {"device_trackers": []})
    hass.states.async_set("binary_sensor.workday_sensor", "on")
    appels = async_mock_service(hass, "persistent_notification", "create")
    config = await hass.async_add_executor_job(yaml_util.load_yaml, AUTO)
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    commun = {**DONNEES, "type_heure": "arrivee", "heure": "09:00:00", "depart_habituel": "08:20:00", "nouvelles": [], "perturbations": []}
    hass.bus.async_fire("transports_idf_alerte", {**commun, "type": "rappel_depart", "arrivee_prevue": "8 h 52", "ecarts": [],
        "conseil": {"depart_h": "8 h 20", "resume": "Bus 192 → RER B", "texte": "Départ 8 h 20"}})
    await hass.async_block_till_done()
    assert "\nArrivée prévue 8 h 52 (à l'heure).\n" in appels[0].data["message"]
    p = {"id": "p1", "titre": "RER B : trafic interrompu", "lignes_trajet": ["RER B"]}
    hass.bus.async_fire("transports_idf_alerte", {**commun, "type": "perturbation", "nouvelles": [p], "perturbations": [p],
        "arrivee_prevue": "9 h 12", "ecarts": ["arrivée 9 h 12 (+12 min)"], "conseil": {"depart_h": "8 h 05", "impossible": True}})
    await hass.async_block_till_done()
    assert "\nRetard prévu : arrivée 9 h 12 (+12 min).\n" in appels[1].data["message"]


async def test_en_route(hass: HomeAssistant):
    """En route : pas de « pars à … », et chaque nouvelle prévision est annoncée."""
    hass.states.async_set("person.alex", "home", {"device_trackers": []})
    hass.states.async_set("binary_sensor.workday_sensor", "on")
    appels = async_mock_service(hass, "persistent_notification", "create")
    config = await hass.async_add_executor_job(yaml_util.load_yaml, AUTO)
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    commun = {**DONNEES, "type_heure": "arrivee", "heure": "09:00:00", "depart_habituel": "08:20:00", "nouvelles": [], "perturbations": [], "en_route": True}
    p = {"id": "p1", "titre": "RER B : trafic ralenti", "lignes_trajet": ["RER B"]}
    hass.bus.async_fire("transports_idf_alerte", {**commun, "type": "perturbation", "nouvelles": [p], "perturbations": [p],
        "arrivee_prevue": "9 h 12", "ecarts": ["arrivée 9 h 12 (+12 min)"],
        "conseil": {"depart_h": "8 h 20", "resume": "Bus 192 → RER B", "en_route": True}})
    hass.bus.async_fire("transports_idf_alerte", {**commun, "type": "prevision", "arrivee_prevue": "9 h 05", "ecarts": ["arrivée 9 h 05 (+5 min)"],
        "conseil": {"depart_h": "8 h 20", "resume": "Bus 192 → RER B", "en_route": True}})
    hass.bus.async_fire("transports_idf_alerte", {**commun, "type": "prevision", "ecarts": [],
        "conseil": {"depart_h": "8 h 20", "en_route": True, "impossible": True}})
    await hass.async_block_till_done()
    assert "pars à" not in appels[0].data["message"]
    assert "\nRetard prévu : arrivée 9 h 12 (+12 min).\n" in appels[0].data["message"]
    assert appels[1].data["title"] == "🔄 Travail (arrivée 9 h 00) : nouvelle prévision"
    assert appels[1].data["message"].startswith("Bus 192 → RER B\nRetard prévu : arrivée 9 h 05 (+5 min).\n")
    assert appels[2].data["message"].startswith("Aucun itinéraire ne permet plus d'aller au bout du trajet.")
