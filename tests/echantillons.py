"""Réponses PRIM réalistes (formats SIRI Lite et Navitia)."""
from datetime import datetime, timedelta, timezone


def siri(maintenant: datetime, passages):
    visites = []
    for n, (ligne, dest, minutes, reel) in enumerate(passages):
        t = (maintenant + timedelta(minutes=minutes)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        appel = {"StopPointName": [{"value": "Bourg-la-Reine"}], "DestinationDisplay": [{"value": dest}], "AimedDepartureTime": t, "DepartureStatus": "onTime", "VehicleAtStop": False}
        if reel:
            appel["ExpectedDepartureTime"] = t
        visites.append({
            "RecordedAtTime": t, "ItemIdentifier": f"i{n}",
            "MonitoringRef": {"value": "STIF:StopPoint:Q:41087:"},
            "MonitoredVehicleJourney": {
                "LineRef": {"value": f"STIF:Line::{ligne}:"},
                "FramedVehicleJourneyRef": {"DataFrameRef": {"value": "any"}, "DatedVehicleJourneyRef": f"v{n}"},
                "DestinationName": [{"value": dest}],
                "DirectionName": [{"value": dest}],
                "MonitoredCall": appel,
            },
        })
    return {"Siri": {"ServiceDelivery": {"ResponseTimestamp": "x", "StopMonitoringDelivery": [{"Version": "2.0", "Status": "true", "MonitoredStopVisit": visites}]}}}


LIEUX = {"places": [
    {"id": "stop_area:IDFM:71946", "name": "Bourg-la-Reine (Bourg-la-Reine)", "embedded_type": "stop_area", "quality": 90,
     "stop_area": {"id": "stop_area:IDFM:71946", "name": "Bourg-la-Reine", "coord": {"lon": "2.3125", "lat": "48.7802"},
                   "administrative_regions": [{"name": "Bourg-la-Reine", "level": 8}]}},
    {"id": "stop_area:IDFM:70000", "name": "Denfert-Rochereau (Paris)", "embedded_type": "stop_area",
     "stop_area": {"id": "stop_area:IDFM:70000", "name": "Denfert-Rochereau", "coord": {"lon": "2.3323", "lat": "48.8336"},
                   "administrative_regions": [{"name": "Paris", "level": 8}]}},
]}

LIGNES = {"lines": [
    {"id": "line:IDFM:C01743", "code": "B", "name": "RER B", "color": "5291CE", "text_color": "FFFFFF", "commercial_mode": {"name": "RER"}},
    {"id": "line:IDFM:C01192", "code": "192", "name": "Porte d'Orléans - Robinson", "color": "00814F", "text_color": "FFFFFF", "commercial_mode": {"name": "Bus"}},
    {"id": "line:IDFM:C00001", "code": "T6", "name": "T6", "color": "0064B0", "text_color": "FFFFFF", "commercial_mode": {"name": "Tramway"}},
]}

POINTS = {"stop_points": [{"id": "stop_point:IDFM:41087"}, {"id": "stop_point:IDFM:monomodalStopPlace:43135"}]}

MARCHE = {"journeys": [{"duration": 412, "type": "best", "sections": [{"type": "street_network", "mode": "walking", "duration": 412}]}]}

ITINERAIRES = {"journeys": [
    {"duration": 1920, "nb_transfers": 1, "departure_date_time": "20261103T080200", "arrival_date_time": "20261103T083400",
     "durations": {"walking": 420},
     "sections": [
        {"type": "street_network", "mode": "walking", "duration": 300},
        {"type": "public_transport", "display_informations": {"code": "192", "commercial_mode": "Bus", "color": "00814F", "text_color": "FFFFFF", "direction": "Robinson"},
         "links": [{"type": "line", "id": "line:IDFM:C01192"}, {"type": "route", "id": "route:IDFM:C01192:1"}], "from": {"id": "stop_point:IDFM:41087", "name": "Les Blagis", "embedded_type": "stop_point", "stop_point": {"stop_area": {"id": "stop_area:IDFM:99999"}}}, "to": {"name": "Bourg-la-Reine RER"}, "departure_date_time": "20261103T080700", "arrival_date_time": "20261103T081200"},
        {"type": "transfer", "duration": 120},
        {"type": "public_transport", "display_informations": {"code": "B", "commercial_mode": "RER", "color": "5291CE", "direction": "Aéroport CDG 2"},
         "links": [{"type": "line", "id": "line:IDFM:C01743"}, {"type": "route", "id": "route:IDFM:C01743:2"}], "from": {"id": "stop_point:IDFM:43135", "name": "Bourg-la-Reine", "embedded_type": "stop_point"}, "to": {"name": "Denfert-Rochereau"}, "departure_date_time": "20261103T081500", "arrival_date_time": "20261103T083100"},
     ]},
    {"duration": 2400, "nb_transfers": 0, "departure_date_time": "20261103T080500", "arrival_date_time": "20261103T084500",
     "sections": [{"type": "public_transport", "display_informations": {"code": "T6", "commercial_mode": "Tramway", "color": "0064B0"},
                   "links": [{"type": "line", "id": "line:IDFM:C00001"}], "from": {"name": "Châtillon"}, "to": {"name": "Vélizy"}}]},
]}


def perturbation(effet="NO_SERVICE", ident="d1", debut="20200101T000000", fin="20991231T235900"):
    return {"disruptions": [{
        "id": ident, "status": "active",
        "severity": {"name": "bloquante", "effect": effet, "priority": 10},
        "messages": [{"text": "RER B : trafic interrompu", "channel": {"name": "titre", "types": ["title"]}},
                     {"text": "<p>Le trafic est interrompu entre <b>Bourg-la-Reine</b> et Denfert-Rochereau.</p>", "channel": {"name": "moteur", "types": ["web"]}}],
        "application_periods": [{"begin": debut, "end": fin}],
        "cause": "perturbation",
        "impacted_objects": [{"pt_object": {"id": "line:IDFM:C01743", "embedded_type": "line"}}],
    }], "line_reports": []}


RESEAU = {"lines": [
    {"id": "line:IDFM:C01743", "code": "B", "name": "RER B", "color": "5291CE", "text_color": "FFFFFF", "commercial_mode": {"name": "RER"}, "physical_modes": [{"id": "physical_mode:RapidTransit", "name": "RER"}]},
    {"id": "line:IDFM:C01742", "code": "A", "name": "RER A", "color": "E2231A", "text_color": "FFFFFF", "commercial_mode": {"name": "RER"}, "physical_modes": [{"id": "physical_mode:RapidTransit"}]},
    {"id": "line:IDFM:C01381", "code": "6", "name": "Métro 6", "color": "704B1C", "text_color": "FFFFFF", "commercial_mode": {"name": "Métro"}, "physical_modes": [{"id": "physical_mode:Metro"}]},
    {"id": "line:IDFM:C01383", "code": "7bis", "name": "Métro 7bis", "color": "6EC4E8", "text_color": "000000", "commercial_mode": {"name": "Métro"}, "physical_modes": [{"id": "physical_mode:Metro"}]},
    {"id": "line:IDFM:C00001", "code": "T6", "name": "T6", "color": "0064B0", "text_color": "FFFFFF", "commercial_mode": {"name": "Tramway"}, "physical_modes": [{"id": "physical_mode:Tramway"}]},
    {"id": "line:IDFM:C01737", "code": "J", "name": "Transilien J", "color": "D5C900", "text_color": "000000", "commercial_mode": {"name": "Train Transilien"}, "physical_modes": [{"id": "physical_mode:LocalTrain"}]},
    {"id": "line:IDFM:TER1", "code": "TER", "name": "TER", "commercial_mode": {"name": "TER"}, "physical_modes": [{"id": "physical_mode:LocalTrain"}]},
]}


def rapports_reseau(perturbe=True):
    d = perturbation() if perturbe else {"disruptions": []}
    travaux = {
        "id": "w1", "status": "active", "cause": "travaux",
        "severity": {"name": "information", "effect": "OTHER_EFFECT"},
        "messages": [{"text": "Métro 6 : travaux de rénovation", "channel": {"types": ["title"]}}],
        "application_periods": [{"begin": "20200101T000000", "end": "20991231T000000"}],
        "impacted_objects": [],
    }
    return {
        "disruptions": d["disruptions"] + [travaux],
        "line_reports": [{"line": {"id": "line:IDFM:C01381", "links": [{"type": "disruption", "id": "w1"}]}, "pt_objects": []}],
        "pagination": {"total_result": 2, "items_per_page": 100, "start_page": 0},
    }


DEPARTS = {"departures": [
    {"stop_date_time": {"departure_date_time": "20261103T081500"}},
    {"stop_date_time": {"departure_date_time": "20261103T082100"}},
    {"stop_date_time": {"departure_date_time": "20261103T083000"}},
]}
