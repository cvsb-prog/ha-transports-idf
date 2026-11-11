"""Tests de bout en bout avec PRIM simulé (aioclient_mock)."""

from datetime import datetime, timedelta
import re

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.transports_idf.api import (
    analyser_passages,
    analyser_perturbations,
    estimation_marche,
    prochain_jour_ouvre,
    refs_candidates,
)
from custom_components.transports_idf.const import DOMAIN
from custom_components.transports_idf.coordinator import est_la_nuit, etat_passage
from custom_components.transports_idf.trajets import TraficCoordinator

from . import echantillons as E

BASE = "https://prim.iledefrance-mobilites.fr/marketplace"


def reseau(aioclient_mock, perturbe=False):
    aioclient_mock.get(re.compile(r"/navitia/lines\?"), json=E.RESEAU)
    aioclient_mock.get(re.compile(r"/line_reports/line_reports"), json=E.rapports_reseau(perturbe))
    aioclient_mock.get(re.compile(r"/departures"), json=E.DEPARTS)


def brancher(aioclient_mock, maintenant, perturbe=False):
    reseau(aioclient_mock, perturbe)
    aioclient_mock.get(re.compile(r"/v2/navitia/places"), json=E.LIEUX)
    aioclient_mock.get(re.compile(r"/stop_areas/.*/lines"), json=E.LIGNES)
    aioclient_mock.get(re.compile(r"/stop_areas/.*/stop_points"), json=E.POINTS)
    aioclient_mock.get(re.compile(r"/line_reports/lines/line(%3A|:)IDFM(%3A|:)C01743/"), json=E.perturbation() if perturbe else {"disruptions": []})
    aioclient_mock.get(re.compile(r"/line_reports/lines/"), json={"disruptions": []})
    aioclient_mock.get(re.compile(r"/v2/navitia/journeys\?.*direct_path=only"), json=E.MARCHE)
    aioclient_mock.get(re.compile(r"/v2/navitia/journeys"), json=E.ITINERAIRES)
    aioclient_mock.get(
        re.compile(r"/stop-monitoring\?MonitoringRef=STIF(%3A|:)StopArea(%3A|:)SP(%3A|:)71946"),
        json=E.siri(maintenant, [("C01743", "Aéroport CDG 2", 3, True), ("C01743", "Robinson", 9, True), ("C01192", "Robinson", 14, False), ("C01192", "Porte d'Orléans", 30, True)]),
    )
    aioclient_mock.get(re.compile(r"/stop-monitoring"), json=E.siri(maintenant, []))


# ---- Fonctions pures ---------------------------------------------------------
async def test_analyse_et_etats():
    m = dt_util.utcnow()
    p = analyser_passages(E.siri(m, [("C01743", "Aéroport CDG 2", 5, True), ("C01192", "Robinson", 2, False)]))
    assert [x["ligne"] for x in p] == ["C01192", "C01743"]
    assert p[0]["temps_reel"] is False and p[1]["temps_reel"] is True
    assert etat_passage(10, 7, 2) == "atteignable"
    assert etat_passage(8, 7, 2) == "presse"
    assert etat_passage(6, 7, 2) == "trop_tard"
    assert refs_candidates("stop_area:IDFM:71946", ["stop_point:IDFM:41087", "stop_point:IDFM:monomodalStopPlace:43135"]) == [
        "STIF:StopArea:SP:71946:", "STIF:StopPoint:Q:41087:", "STIF:StopArea:SP:43135:"
    ]
    assert 300 < estimation_marche(48.7802, 2.3125, 48.7835, 2.3125) < 400
    assert prochain_jour_ouvre(datetime(2026, 11, 6, 9, 0), "08:00:00") == datetime(2026, 11, 9, 8, 0)  # ven → lun
    assert est_la_nuit(datetime(2026, 1, 1, 2, 0), "01:30:00", "05:00:00")
    assert not est_la_nuit(datetime(2026, 1, 1, 12, 0), "01:30:00", "05:00:00")
    assert est_la_nuit(datetime(2026, 1, 1, 0, 30), "23:30:00", "05:00:00")
    perts = analyser_perturbations(E.perturbation(), datetime(2026, 11, 3, 8, 0))
    assert perts[0]["titre"] == "RER B : trafic interrompu" and "Bourg-la-Reine" in perts[0]["message"] and "<" not in perts[0]["message"]
    assert analyser_perturbations(E.perturbation(debut="20990101T000000"), datetime(2026, 11, 3)) == []
    assert TraficCoordinator.fenetre("08:00:00", 60, datetime(2026, 11, 3, 7, 30))
    assert not TraficCoordinator.fenetre("08:00:00", 60, datetime(2026, 11, 3, 6, 30))
    assert TraficCoordinator.fenetre("00:20:00", 60, datetime(2026, 11, 3, 23, 50))


# ---- Configuration -----------------------------------------------------------
async def test_parcours_complet(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set("zone.home", "0", {"latitude": 48.7800, "longitude": 2.3170})
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    brancher(aioclient_mock, dt_util.utcnow())

    # 1. Entrée principale
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"jeton": " abc ", "zone": "zone.home"})
    assert r["type"] is FlowResultType.CREATE_ENTRY and r["data"]["jeton"] == "abc"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.async_block_till_done()
    assert all(h and h.get("apikey") == "abc" for *_, h in aioclient_mock.mock_calls)

    # 2. Arrêt
    r = await hass.config_entries.subentries.async_init((entry.entry_id, "arret"), context={"source": "user"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"recherche": "bourg la reine"})
    assert r["step_id"] == "choix"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"arret": "stop_area:IDFM:71946"})
    assert r["step_id"] == "lignes"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"lignes": ["line:IDFM:C01743", "line:IDFM:C01192"]})
    assert r["step_id"] == "marche" and "calculé" in r["description_placeholders"]["calcul"]
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"temps_marche": 7})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    sous = next(s for s in entry.subentries.values() if s.subentry_type == "arret")
    assert sous.data["refs"] == ["STIF:StopArea:SP:71946:"]
    assert sous.data["temps_marche_calcule"] == 6.9
    await hass.async_block_till_done()

    etat = next(s for s in hass.states.async_all("sensor") if s.attributes.get("type_transports_idf") == "arret")
    passages = etat.attributes["passages"]
    assert [p["etat"] for p in passages] == ["trop_tard", "presse", "atteignable", "atteignable"]
    assert passages[0]["ligne"] == "B" and passages[0]["couleur"] == "#5291CE"
    assert passages[2]["temps_reel"] is False
    # Prochain départ atteignable = bus de +14 min
    assert abs((dt_util.parse_datetime(etat.state) - dt_util.utcnow()).total_seconds() / 60 - 14) < 1

    # Même arrêt une 2e fois : refusé
    r = await hass.config_entries.subentries.async_init((entry.entry_id, "arret"), context={"source": "user"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"recherche": "bourg"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"arret": "stop_area:IDFM:71946"})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "deja_suivi"

    # 3. Trajet
    r = await hass.config_entries.subentries.async_init((entry.entry_id, "trajet"), context={"source": "user"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"nom": "Travail", "personne": "person.alex", "recherche": "denfert"})
    assert r["step_id"] == "destination"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"destination": "stop_area:IDFM:70000"})
    assert r["step_id"] == "point_destination"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"mode": "transports", "arrivee_type": "avant", "arrivee_fin": "09:00:00"})
    assert r["type"] is FlowResultType.MENU and "avant 9 h 00" in r["description_placeholders"]["resume"]
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "terminer"})
    assert r["step_id"] == "itineraire"
    assert "Bus 192 → RER B · 32 min · 1 corresp." in r["data_schema"].schema["itineraire"].config["options"][0]["label"]
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"itineraire": "0"})
    assert r["type"] is FlowResultType.CREATE_ENTRY and r["title"] == "Travail (Alex)"
    await hass.async_block_till_done()
    trajet = next(s for s in hass.states.async_all("binary_sensor") if s.attributes.get("type_transports_idf") == "trajet")
    assert trajet.state == "off" and trajet.attributes["personne"] == "person.alex"

    # 4. Reconfiguration d'un arrêt
    r = await hass.config_entries.subentries.async_init((entry.entry_id, "arret"), context={"source": "reconfigure", "subentry_id": sous.subentry_id})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"lignes": ["line:IDFM:C01192"], "temps_marche": 5, "recalculer": False})
    assert r["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    etat = next(s for s in hass.states.async_all("sensor") if s.attributes.get("type_transports_idf") == "arret")
    assert {p["ligne"] for p in etat.attributes["passages"]} == {"192"}

    # 5. Options
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "reglages"
    r = await hass.config_entries.options.async_configure(
        r["flow_id"],
        {"zone": "zone.home", "marge": 3, "nb_passages": 8, "intervalle_jour": 60, "intervalle_nuit": 600, "debut_nuit": "01:30:00", "fin_nuit": "05:00:00", "ignorer_information": True},
    )
    assert r["type"] is FlowResultType.CREATE_ENTRY and entry.options["marge"] == 3
    await hass.async_block_till_done()


def _itin(maintenant, dep_ref, arr_ref, dep_t1, arr_t1):
    f = lambda m: (maintenant + timedelta(minutes=m)).strftime("%Y%m%dT%H%M%S")  # noqa: E731
    import copy
    j = copy.deepcopy(E.ITINERAIRES)
    j["journeys"][0]["departure_date_time"], j["journeys"][0]["arrival_date_time"] = f(dep_ref), f(arr_ref)
    j["journeys"][1]["departure_date_time"], j["journeys"][1]["arrival_date_time"] = f(dep_t1), f(arr_t1)
    return j


async def test_info_trafic_et_recherche(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set("zone.home", "0", {"latitude": 48.7800, "longitude": 2.3170})
    reseau(aioclient_mock, perturbe=True)
    aioclient_mock.get(re.compile(r"/line_reports/lines/"), json={"disruptions": []})
    aioclient_mock.get(re.compile(r"/pt_objects"), json={"pt_objects": [{"embedded_type": "line", "line": E.LIGNES["lines"][1]}]})
    aioclient_mock.get(re.compile(r"/v2/navitia/places"), json=E.LIEUX)
    aioclient_mock.get(re.compile(r"/v2/navitia/journeys"), json=E.ITINERAIRES)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={"jeton": "abc", "zone": "zone.home"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    info = next(s for s in hass.states.async_all("sensor") if s.attributes.get("type_transports_idf") == "info_trafic")
    lignes = {(l["groupe"], l["nom"]): l for l in info.attributes["lignes"]}
    assert list(lignes) == [("RER", "A"), ("RER", "B"), ("Train", "J"), ("Métro", "6"), ("Métro", "7bis"), ("Tram", "T6")]
    assert lignes[("RER", "B")]["etat"] == "interrompu" and lignes[("Métro", "6")]["etat"] == "travaux"
    assert lignes[("RER", "A")]["etat"] == "normal" and info.state == "2"
    assert "Bourg-la-Reine" in lignes[("RER", "B")]["perturbations"][0]["message"]

    # Ligne d'intérêt : bus 192, ajoutée comme un arrêt (sous-entrée « ligne »)
    r = await hass.config_entries.subentries.async_init((entry.entry_id, "ligne"), context={"source": "user"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"recherche": "192"})
    assert r["step_id"] == "choix" and r["data_schema"].schema["ligne"].config["options"][0]["label"].startswith("[BUS] ")
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"ligne": "line:IDFM:C01192"})
    assert r["type"] is FlowResultType.CREATE_ENTRY and r["title"] == "Bus 192"
    await hass.async_block_till_done()
    info = next(s for s in hass.states.async_all("sensor") if s.attributes.get("type_transports_idf") == "info_trafic")
    bus = info.attributes["lignes"][-1]
    assert bus["nom"] == "192" and bus["groupe"] == "Bus" and bus["interet"] is True and bus["etat"] == "normal"
    r = await hass.config_entries.subentries.async_init((entry.entry_id, "ligne"), context={"source": "user"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"recherche": "192"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"ligne": "line:IDFM:C01192"})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "deja_suivie"

    # Recherche de lieu et itinéraire (utilisés par la carte)
    rep = await hass.services.async_call(DOMAIN, "rechercher_lieu", {"texte": "denfert"}, blocking=True, return_response=True)
    assert rep["lieux"][1]["id"] == "stop_area:IDFM:70000"
    rep = await hass.services.async_call(DOMAIN, "itineraire", {"arrivee": "stop_area:IDFM:70000", "heure": "09:00", "type_heure": "arrivee"}, blocking=True, return_response=True)
    assert rep["itineraires"][0]["etapes"][1]["suivants"] == [8, 14]
    assert rep["itineraires"][0]["etapes"][3]["alerte"] == "interrompu" and rep["itineraires"][0]["alerte"] == "interrompu"
    assert any("datetime_represents=arrival" in str(u) for _, u, *_ in aioclient_mock.mock_calls)


async def test_position_rame(hass: HomeAssistant):
    from custom_components.transports_idf.api import analyser_itineraires
    from custom_components.transports_idf.coordinator import Itineraires
    from custom_components.transports_idf.rame import PositionRame

    rame = PositionRame(hass)
    for jeu in (rame.positions, rame.acces):
        jeu._mettre_a_jour = lambda: __import__("asyncio").sleep(0)  # pas de réseau en test
    await rame.async_charger()
    assert rame.acces.index["50147791"]["numero"] == "12"
    assert len(rame.index) > 500

    def pt(nom, ligne, de, vers, de_id, vers_id):
        return {"type": "public_transport", "display_informations": {"code": nom, "commercial_mode": "Métro"},
                "links": [{"type": "line", "id": f"line:IDFM:{ligne}"}],
                "from": {"id": f"stop_point:IDFM:{de_id}", "name": de, "embedded_type": "stop_point"},
                "to": {"id": f"stop_point:IDFM:{vers_id}", "name": vers, "embedded_type": "stop_point"},
                "departure_date_time": "20261104T080000", "arrival_date_time": "20261104T081000"}

    j = {"journeys": [{"duration": 1500, "nb_transfers": 1, "sections": [
        pt("12", "C01382", "Volontaires", "Pasteur", "462964", "22039"),
        {"type": "transfer", "mode": "walking", "duration": 120, "to": {"name": "Pasteur"}},
        pt("6", "C01376", "Pasteur", "Raspail", "22166", "22155"),
        {"type": "street_network", "mode": "walking", "duration": 300, "path": [{"name": "Boulevard Edgar Quinet"}],
         "to": {"embedded_type": "address", "address": {"coord": {"lat": "48.8390", "lon": "2.3303"}}}},
    ]}]}
    its = analyser_itineraires(j)
    calcul = Itineraires(hass, None)
    calcul.rame = rame
    calcul.perturbations_info = lambda _ligne: []
    await calcul.alertes(its)
    m12, m6 = its[0]["etapes"][0], its[0]["etapes"][2]
    assert m12["rame"]["type"] == "correspondance" and (m12["rame"]["voiture"], m12["rame"]["voitures"], m12["rame"]["zone"]) == (5, 5, "arriere")
    # Sortie la plus proche de la destination : n° 1 « bd Edgar Quinet »
    assert m6["rame"]["type"] == "sortie" and (m6["rame"]["numero"], m6["rame"]["nom"]) == ("1", "bd Edgar Quinet") and m6["rame"]["zone"] == "avant"


async def test_parcours_avec_marche(hass: HomeAssistant, aioclient_mock):
    """Métro 4 jusqu'à Mouton-Duvernet, à pied jusqu'à l'arrêt du bus 38, bus jusqu'à Porte d'Orléans.

    Vraies positions RATP et vrais accès IDFM.
    """
    from custom_components.transports_idf.api import analyser_itineraires
    from custom_components.transports_idf.coordinator import Itineraires
    from custom_components.transports_idf.rame import PositionRame

    rame = PositionRame(hass)
    for jeu in (rame.positions, rame.acces):
        jeu._mettre_a_jour = lambda: __import__("asyncio").sleep(0)
    await rame.async_charger()

    def ligne(code, mode, ident, de, vers, de_id, vers_id, dep, arr):
        return {"type": "public_transport", "display_informations": {"code": code, "commercial_mode": mode, "headsign": vers},
                "links": [{"type": "line", "id": f"line:IDFM:{ident}"}],
                "from": {"id": f"stop_point:IDFM:{de_id}", "name": de, "embedded_type": "stop_point"},
                "to": {"id": f"stop_point:IDFM:{vers_id}", "name": vers, "embedded_type": "stop_point"},
                "departure_date_time": dep, "arrival_date_time": arr}

    def lieu(nom, lat, lon):
        return {"name": nom, "embedded_type": "stop_point", "stop_point": {"coord": {"lat": str(lat), "lon": str(lon)}}}

    j = {"journeys": [{"duration": 1500, "nb_transfers": 1, "departure_date_time": "20261104T080000", "arrival_date_time": "20261104T082500", "sections": [
        {"type": "street_network", "mode": "walking", "duration": 180, "from": lieu("Maison", 48.8338, 2.3325), "to": lieu("Denfert-Rochereau", 48.8336, 2.3323)},
        ligne("4", "Métro", "C01374", "Denfert-Rochereau", "Mouton-Duvernet", "463104", "22144", "20261104T080400", "20261104T080600"),
        {"type": "transfer", "mode": "walking", "duration": 120, "path": [{"name": "Rue Mouton-Duvernet"}],
         "from": lieu("Mouton-Duvernet", 48.8314, 2.3299), "to": lieu("Mouton-Duvernet - Général Leclerc", 48.8317, 2.3300)},
        ligne("38", "Bus", "C01038", "Mouton-Duvernet - Général Leclerc", "Porte d'Orléans", "400001", "400002", "20261104T081000", "20261104T081800"),
        {"type": "street_network", "mode": "walking", "duration": 240, "path": [{"name": "Rue Friant"}],
         "from": lieu("Porte d'Orléans", 48.8232, 2.3254), "to": {"name": "12 Rue Friant (Paris)", "embedded_type": "address", "address": {"coord": {"lat": "48.8255", "lon": "2.3267"}}}},
    ]}]}
    its = analyser_itineraires(j)
    calcul = Itineraires(hass, None)
    calcul.rame = rame
    calcul.perturbations_info = lambda _ligne: []
    await calcul.alertes(its)
    etapes = its[0]["etapes"]
    m4, bus = (e for e in etapes if e["type"] == "transport")
    # Mouton-Duvernet : pas de correspondance interne, on sort au plus près de l'arrêt du bus → sortie 2 « r. Mouton-Duvernet »
    assert m4["rame"]["type"] == "sortie" and (m4["rame"]["numero"], m4["rame"]["nom"]) == ("2", "r. Mouton-Duvernet")
    assert bus["rame"] is None  # pas de données pour les bus
    # Les marches gardent leurs deux extrémités (vue carte au clic) et le nom de la destination finale
    assert etapes[2]["de_coord"] and etapes[2]["vers_coord"] and etapes[-1]["vers"] == "12 Rue Friant (Paris)"


async def test_voie_et_mission(hass: HomeAssistant, aioclient_mock):
    """RER : voie et nom du train lus dans les prochains passages de la gare de départ."""
    from datetime import datetime as dt
    from custom_components.transports_idf.api import ClientPrim
    from custom_components.transports_idf.coordinator import Itineraires
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    depart = dt(2026, 11, 4, 8, 15, tzinfo=dt_util.get_default_time_zone())
    siri = E.siri(dt_util.utcnow(), [])
    siri["Siri"]["ServiceDelivery"]["StopMonitoringDelivery"][0]["MonitoredStopVisit"] = [{
        "MonitoringRef": {"value": "STIF:StopPoint:Q:43135:"},
        "MonitoredVehicleJourney": {"LineRef": {"value": "STIF:Line::C01743:"}, "JourneyNote": [{"value": "TOCA"}],
            "MonitoredCall": {"ExpectedDepartureTime": dt_util.as_utc(depart).isoformat(), "DeparturePlatformName": {"value": "2"}}},
    }]
    aioclient_mock.get(re.compile(r"/stop-monitoring"), json=siri)
    it = {"etapes": [{"type": "transport", "code": "C01743", "mode": "RER", "nom": "B", "de_point": "stop_point:IDFM:43135", "depart_brut": "20261104T081500"}]}
    calcul = Itineraires(hass, ClientPrim(async_get_clientsession(hass), "abc"))
    await calcul._voies([it])
    assert (it["etapes"][0]["voie"], it["etapes"][0]["mission"]) == ("2", "TOCA")
