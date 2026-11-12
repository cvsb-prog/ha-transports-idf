"""Trajets préférés 0.8.0 : calcul au départ de maintenant, nominal, rappel, détour, alertes."""

import copy
from datetime import timedelta
import re

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.transports_idf.api import analyser_itineraires
from custom_components.transports_idf.const import DOMAIN, EVENEMENT_ALERTE

from . import echantillons as E
from .test_integration import reseau

TOUS_LES_JOURS = ["0", "1", "2", "3", "4", "5", "6"]
SECTIONS = [{"ligne_id": "line:IDFM:C01192", "nom": "192", "mode": "Bus"}, {"ligne_id": "line:IDFM:C01743", "nom": "B", "mode": "RER"}]


def itin(m, dep_ref, arr_ref, dep_t1, arr_t1):
    """Deux itinéraires (bus 192 + RER B, ou tram T6) aux heures données (minutes après m)."""
    f = lambda x: (m + timedelta(minutes=x)).strftime("%Y%m%dT%H%M%S")  # noqa: E731
    j = copy.deepcopy(E.ITINERAIRES)
    j["journeys"][0]["departure_date_time"], j["journeys"][0]["arrival_date_time"] = f(dep_ref), f(arr_ref)
    j["journeys"][1]["departure_date_time"], j["journeys"][1]["arrival_date_time"] = f(dep_t1), f(arr_t1)
    return j


def entree(hass, **trajet):
    data = {"nom": "Travail", "personne": "person.alex", "origine": "zone.home", "avance": 60, "jours": TOUS_LES_JOURS,
            "destination_id": "stop_area:IDFM:70000", "destination_nom": "Denfert-Rochereau", "sections": SECTIONS,
            "resume": "Bus 192 → RER B", **trajet}
    e = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={"jeton": "abc", "zone": "zone.home"},
                        subentries_data=[ConfigSubentryData(subentry_type="trajet", title="Travail", unique_id=None, data=data)])
    e.add_to_hass(hass)
    return e


def mocks(aioclient_mock, journeys, perturbe=False):
    reseau(aioclient_mock, perturbe)
    aioclient_mock.get(re.compile(r"/line_reports/lines/line(%3A|:)IDFM(%3A|:)C01743/"), json=E.perturbation() if perturbe else {"disruptions": []})
    aioclient_mock.get(re.compile(r"/line_reports/lines/"), json={"disruptions": []})
    aioclient_mock.get(re.compile(r"/v2/navitia/journeys"), json=journeys)
    aioclient_mock.get(re.compile(r"/stop-monitoring"), json=E.siri(dt_util.utcnow(), []))


def capteur(hass):
    return next(s for s in hass.states.async_all("binary_sensor"))


async def test_analyse_etapes():
    it = analyser_itineraires(E.ITINERAIRES)[0]
    assert [e["type"] for e in it["etapes"]] == ["marche", "transport", "marche", "transport"]
    assert it["sections"][0]["de_id"] == "stop_area:IDFM:99999"
    assert it["etapes"][1]["depart"] == "8 h 07" and it["etapes"][1]["de"] == "Les Blagis"


async def test_direct_nominal_et_alertes(hass: HomeAssistant, aioclient_mock, freezer):
    # Heure fixe (7 h à Paris, un vendredi ouvré) : sinon, le soir, le trajet testé passe minuit.
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to("2026-11-06 06:00:00+00:00")
    hass.states.async_set("zone.home", "0", {"latitude": 48.7800, "longitude": 2.3170})
    m = dt_util.now().replace(tzinfo=None, second=0, microsecond=0)
    mocks(aioclient_mock, itin(m, 10, 42, 12, 50), perturbe=True)
    e = entree(hass, type_heure="depart", heure_depart=(m + timedelta(minutes=30)).strftime("%H:%M:00"))
    evenements = async_capture_events(hass, EVENEMENT_ALERTE)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    a = capteur(hass).attributes
    assert capteur(hass).state == "on" and a["en_veille"] is True
    # Au départ de maintenant : le préféré d'abord (même perturbé, avec sa pastille), puis l'autre
    assert [(o["prefere"], o["resume"]) for o in a["direct"]] == [(True, "Bus 192 → RER B · 32 min · 1 corresp."), (False, "Tramway T6 · 38 min · direct")]
    assert a["direct"][0]["etapes"][3]["alerte"] == "interrompu" and a["direct"][1]["gain"] == -8
    assert a["direct"][0]["etapes"][1]["suivants"] == [8, 14]
    # Nominal : préféré pour la prochaine occurrence (aujourd'hui)
    assert a["nominal"]["jour"] == (m + timedelta(minutes=30)).date().isoformat() and a["nominal"]["resume"].startswith("Bus 192")
    # Alerte, avec en rechange l'itinéraire qui évite la ligne interrompue
    assert [ev.data["type"] for ev in evenements] == ["perturbation"]
    assert evenements[0].data["rechange"]["resume"].startswith("Tramway T6")
    # Calcul à la demande (dépliage dans la carte) : réponse immédiate, pas de nouvel appel dans la minute
    appels = len(aioclient_mock.mock_calls)
    rep = await hass.services.async_call(DOMAIN, "partir_maintenant", {"trajet": capteur(hass).entity_id}, blocking=True, return_response=True)
    assert rep["itineraires"][0]["prefere"] is True and len(aioclient_mock.mock_calls) == appels
    # Retour à la normale
    aioclient_mock.clear_requests()
    mocks(aioclient_mock, itin(m, 10, 42, 12, 50), perturbe=False)
    await e.runtime_data.trafic.async_refresh()
    await hass.async_block_till_done()
    assert [ev.data["type"] for ev in evenements] == ["perturbation", "retour_normal"]


async def test_rappel_arrivee(hass: HomeAssistant, aioclient_mock, freezer):
    # Heure fixe (7 h à Paris, un vendredi ouvré) : sinon, le soir, le trajet testé passe minuit.
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to("2026-11-06 06:00:00+00:00")
    hass.states.async_set("zone.home", "0", {"latitude": 48.7800, "longitude": 2.3170})
    m = dt_util.now().replace(tzinfo=None, second=0, microsecond=0)
    mocks(aioclient_mock, itin(m, 10, 42, 15, 50))
    e = entree(hass, type_heure="arrivee", heure_depart=(m + timedelta(minutes=55)).strftime("%H:%M:00"),
               depart_habituel=(m + timedelta(minutes=20)).strftime("%H:%M:00"), duree=1920)
    evenements = async_capture_events(hass, EVENEMENT_ALERTE)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    r = capteur(hass).attributes["rappel"]
    # Le plus tardif qui arrive avant l'heure cible : le tram de +15 min → 5 min plus tôt que d'habitude
    assert r["resume"].startswith("Tramway T6") and r["avance"] == 5 and r["retard"] is False and r["depart_iso"]
    assert [ev.data["type"] for ev in evenements] == ["depart_anticipe"]
    await e.runtime_data.trafic.async_refresh()
    assert len(evenements) == 1
    # Ça se dégrade : le tram arrive trop tard, reste le bus de +6 min → 14 min plus tôt → nouvelle alerte
    aioclient_mock.clear_requests()
    mocks(aioclient_mock, itin(m, 6, 42, 11, 58))
    await e.runtime_data.trafic.async_refresh()
    assert [ev.data["type"] for ev in evenements] == ["depart_anticipe", "depart_anticipe"]
    assert evenements[1].data["conseil"]["avance"] == 14


async def test_detour(hass: HomeAssistant, aioclient_mock, freezer):
    """École à 6 min à pied, arrivée imposée, 5 min sur place, puis les transports depuis l'école."""
    # Heure fixe (7 h à Paris, un vendredi) : le trajet ne doit pas passer minuit selon l'heure du test.
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to("2026-11-06 06:00:00+00:00")
    hass.states.async_set("zone.home", "0", {"latitude": 48.7800, "longitude": 2.3170})
    m = dt_util.now().replace(tzinfo=None, second=0, microsecond=0)
    # Tracé à pied maison → école (pour la carte du trajet), demandé avant les itinéraires en transports.
    aioclient_mock.get(re.compile(r"/v2/navitia/journeys\?.*direct_path=only"), json={"journeys": [{"duration": 380, "sections": [
        {"type": "street_network", "mode": "walking", "geojson": {"type": "LineString", "coordinates": [[2.3170, 48.7800], [2.3180, 48.7810], [2.3195, 48.7822]]}}]}]})
    mocks(aioclient_mock, itin(m, 50, 80, 52, 95))
    heure_ecole = m + timedelta(minutes=40)
    e = entree(hass, type_heure="arrivee", heure_depart=(m + timedelta(minutes=90)).strftime("%H:%M:00"),
               depart_habituel=(m + timedelta(minutes=34)).strftime("%H:%M:00"),
               detour_id="poi:ecole", detour_nom="École Jules Ferry", detour_mode="marche", detour_duree=6, detour_pause=5,
               detour_heure=heure_ecole.strftime("%H:%M:00"), detour_jours=TOUS_LES_JOURS)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    a = capteur(hass).attributes
    n = a["nominal"]
    assert [x["type"] for x in n["etapes"][:2]] == ["detour", "pause"]
    # Le détour a son tracé : la carte du trajet le dessine (avant, seule la suite après l'école y figurait).
    assert n["etapes"][0]["trace"][0] == [48.78, 2.317] and n["etapes"][0]["vers_coord"] == [48.7822, 2.3195]
    assert n["etapes"][0]["duree"] == 6 and n["etapes"][1]["reprise"] == f"{(heure_ecole + timedelta(minutes=5)).hour} h {(heure_ecole + timedelta(minutes=5)).minute:02d}"
    # On quitte la maison 6 min avant l'heure de l'école
    assert n["depart_h"] == f"{(heure_ecole - timedelta(minutes=6)).hour} h {(heure_ecole - timedelta(minutes=6)).minute:02d}"
    assert a["detour"]["nom"] == "École Jules Ferry" and n["detour"] is True
    # Les transports partent bien de l'école
    assert any("from=poi" in str(u) for _, u, *_ in aioclient_mock.mock_calls)
    # Le rappel tient compte du détour
    assert a["rappel"]["depart_h"] == n["depart_h"]
    # Jour sans détour : trajet direct
    sous = next(iter(e.subentries.values()))
    hass.config_entries.async_update_subentry(e, sous, data={**sous.data, "detour_jours": []})
    await hass.async_block_till_done()
    assert capteur(hass).attributes["nominal"]["etapes"][0]["type"] != "detour"


async def test_creation_avec_detour(hass: HomeAssistant, aioclient_mock):
    from homeassistant.data_entry_flow import FlowResultType
    hass.states.async_set("zone.home", "0", {"latitude": 48.7800, "longitude": 2.3170})
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    m = dt_util.now().replace(tzinfo=None, second=0, microsecond=0)
    reseau(aioclient_mock)
    aioclient_mock.get(re.compile(r"/v2/navitia/places"), json=E.LIEUX)
    aioclient_mock.get(re.compile(r"/line_reports/lines/"), json={"disruptions": []})
    aioclient_mock.get(re.compile(r"/v2/navitia/journeys\?.*direct_path=only"), json={"journeys": [{"duration": 400}]})
    aioclient_mock.get(re.compile(r"/v2/navitia/journeys"), json=itin(m, 10, 42, 12, 50))
    e = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={"jeton": "abc", "zone": "zone.home"})
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    r = await hass.config_entries.subentries.async_init((e.entry_id, "trajet"), context={"source": "user"})
    f = r["flow_id"]
    etape = lambda donnees: hass.config_entries.subentries.async_configure(f, donnees)  # noqa: E731
    r = await etape({"nom": "Travail", "personne": "person.alex", "recherche": "denfert", "jours": ["0", "1", "2", "3", "4"]})
    r = await etape({"destination": "stop_area:IDFM:70000"})
    r = await etape({"mode": "transports", "arrivee_type": "avant", "arrivee_fin": "09:00:00"})
    # Étape 1 à vélo : durée vide → calculée par PRIM (400 s → 7 min)
    r = await etape({"next_step_id": "ajouter"})
    r = await etape({"recherche": "ecole", "position": "0"})
    r = await etape({"lieu": "stop_area:IDFM:71946"})
    assert r["step_id"] == "point" and r["description_placeholders"]["depuis"]
    r = await etape({"mode": "velo", "arrivee_type": "entre", "arrivee_debut": "08:20:00", "arrivee_fin": "08:20:00", "sejour": 5, "depart_type": "aucune", "jours": ["0", "1", "3"]})
    assert any("direct_path_mode%5B%5D=bike" in str(u) or "direct_path_mode[]=bike" in str(u) for _, u, *_ in aioclient_mock.mock_calls)
    assert "🚲 7 min · arrivée à 8 h 20 · 5 min sur place · lun, mar, jeu" in r["description_placeholders"]["resume"]
    # Étape 2 en transports, ajoutée après le départ puis déplacée après l'étape 1
    r = await etape({"next_step_id": "ajouter"})
    r = await etape({"recherche": "ecole", "position": "0"})
    r = await etape({"lieu": "stop_area:IDFM:70000"})
    r = await etape({"mode": "transports", "arrivee_type": "aucune", "sejour": 10, "depart_type": "avant", "depart_fin": "08:40:00", "jours": ["0", "1", "2", "3", "4"]})
    assert r["menu_options"][-3:] == ["deplacer", "general", "terminer"]
    r = await etape({"next_step_id": "deplacer"})
    r = await etape({"point": "1", "sens": "descendre"})
    noms = [l.split(" — ")[0] for l in r["description_placeholders"]["resume"].splitlines()]
    assert [n.split(" · ")[0] for n in noms] == ["1. **Départ**", "2. **Étape 1**", "3. **Étape 2**", "4. **Destination**"]
    assert "2. **Étape 1** · Bourg-la-Reine" in noms[1] and "en semaine" not in r["description_placeholders"]["resume"].splitlines()[2]
    # Puis supprimée : on retrouve départ → école → destination
    r = await etape({"next_step_id": "supprimer"})
    r = await etape({"point": "2"})
    assert len(r["description_placeholders"]["resume"].splitlines()) == 3 and "deplacer" not in r["menu_options"]
    r = await etape({"next_step_id": "terminer"})
    assert r["step_id"] == "itineraire" and r["description_placeholders"]["destination"] == "Denfert-Rochereau (Paris)"
    r = await etape({"itineraire": "0"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    d = next(s for s in e.subentries.values() if s.subentry_type == "trajet").data
    assert (d["detour_mode"], d["detour_duree"], d["detour_heure"], d["detour_jours"], d["jours"]) == ("velo", 7, "08:20:00", ["0", "1", "3"], ["0", "1", "2", "3", "4"])
    assert d["depart_habituel"] == "08:13:00"  # 8 h 20 − 7 min de vélo
