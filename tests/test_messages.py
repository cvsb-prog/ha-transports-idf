"""0.9.0 : messages trafic filtrés (portion empruntée, heure), dédoublonnés ; formulaires ; jours fériés."""

from datetime import date, datetime
import re

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.transports_idf.api import (
    analyser_itineraires,
    analyser_perturbations,
    icone_lieu,
    messages_affiches,
    pertinente,
    perimee_selon_texte,
)
from custom_components.transports_idf.config_flow import choisir_ligne
from custom_components.transports_idf.const import DOMAIN
from custom_components.transports_idf.coordinator import Itineraires
from custom_components.transports_idf.trajets import jour_du_trajet

from . import echantillons as E
from .test_integration import brancher

MAINTENANT = datetime(2026, 11, 6, 1, 20)


def arret(nom, ident):
    return {"id": f"stop_area:IDFM:{ident}", "name": nom, "embedded_type": "stop_area", "stop_area": {"id": f"stop_area:IDFM:{ident}", "name": nom}}


def point(nom, ident, zone):
    return {"id": f"stop_point:IDFM:{ident}", "name": f"{nom} (Paris)", "embedded_type": "stop_point",
            "stop_point": {"id": f"stop_point:IDFM:{ident}", "name": nom, "stop_area": {"id": f"stop_area:IDFM:{zone}", "name": nom}}}


def perturbation(ident, titre, objets, periodes=(("20260101T000000", "20271231T235900"),), effet="NO_SERVICE", texte=None):
    return {
        "id": ident, "status": "active", "severity": {"name": "bloquante", "effect": effet},
        "messages": [{"text": titre, "channel": {"types": ["title"]}}, {"text": texte or f"{titre} — détail", "channel": {"types": ["web"]}}],
        "application_periods": [{"begin": a, "end": b} for a, b in periodes],
        "impacted_objects": objets,
    }


LIGNE_13 = {"pt_object": {"id": "line:IDFM:C01383", "embedded_type": "line"}}

# Métro 13 : Invalides → Saint-Lazare (par Champs-Élysées, Miromesnil) ; Duroc est ailleurs sur la ligne.
TRAJET = {"journeys": [{"duration": 600, "sections": [{
    "type": "public_transport", "display_informations": {"code": "13", "commercial_mode": "Métro"},
    "links": [{"type": "line", "id": "line:IDFM:C01383"}],
    "from": point("Invalides", "1", "10"), "to": point("Saint-Lazare", "4", "40"),
    "departure_date_time": "20261106T082700", "arrival_date_time": "20261106T083700",
    "stop_date_times": [{"stop_point": point(n, i, z)} for n, i, z in
                        (("Invalides", "1", "10"), ("Champs-Élysées - Clemenceau", "2", "20"), ("Miromesnil", "3", "30"), ("Saint-Lazare", "4", "40"))],
}]}]}


def _perts(*disruptions, line_reports=()):
    return analyser_perturbations({"disruptions": list(disruptions), "line_reports": list(line_reports)}, MAINTENANT, horizon=__import__("datetime").timedelta(hours=36))


async def test_portee_des_messages():
    etape = analyser_itineraires(TRAJET)[0]["etapes"][0]
    assert "stop_area:IDFM:30" in etape["arrets"] and "stop_area:IDFM:30" not in etape["bouts"] and "n:invalides" in etape["bouts"]
    station_fermee = perturbation("a", "Métro 13 : Duroc non desservi", [LIGNE_13, {"pt_object": arret("Duroc", "99")}])
    montee = perturbation("b", "Métro 13 : Invalides, accès fermé", [{"pt_object": arret("Invalides", "10")}])
    traversee = perturbation("c", "Métro 13 : Miromesnil non desservi", [{"pt_object": arret("Miromesnil", "30")}])
    troncon = perturbation("d", "Métro 13 : trafic interrompu", [{**LIGNE_13, "impacted_section": {"from": arret("Champs-Élysées - Clemenceau", "20"), "to": arret("Place de Clichy", "50")}}])
    toute = perturbation("e", "Métro 13 : trafic ralenti", [LIGNE_13], effet="SIGNIFICANT_DELAYS")
    # Rattachée à la ligne, mais publiée sur un seul arrêt dans line_reports
    rapportee = perturbation("f", "Métro 13 : Duroc, sortie fermée", [LIGNE_13])
    rapports = [{"line": {"id": "line:IDFM:C01383", "links": []}, "pt_objects": [{**arret("Duroc", "99"), "links": [{"type": "disruption", "id": "f"}]}]}]
    perts = {p["id"]: p for p in _perts(station_fermee, montee, traversee, troncon, toute, rapportee, line_reports=rapports)}
    assert (perts["a"]["portee"], perts["d"]["portee"], perts["e"]["portee"], perts["f"]["portee"]) == ("arrets", "troncon", "ligne", "arrets")
    quand = datetime(2026, 11, 6, 8, 27)
    concernees = sorted(i for i, p in perts.items() if pertinente(p, etape, quand))
    # a, f : autre station ; c : on passe à Miromesnil sans y descendre
    assert concernees == ["b", "d", "e"]
    # Ancien trajet sans liste d'arrêts : on garde tout
    assert pertinente(perts["a"], {"type": "transport"}, quand)


async def test_periodes_et_messages_perimes():
    nuit = perturbation("n", "RER C : travaux de nuit", [{"pt_object": {"id": "line:IDFM:C01727", "embedded_type": "line"}}],
                        periodes=(("20261105T223000", "20261106T050000"), ("20261106T223000", "20261107T050000")), effet="REDUCED_SERVICE")
    vieux = perturbation("v", "Bus 39-14 : travaux du 06/04 au 07/04", [{"pt_object": {"id": "line:IDFM:C02000", "embedded_type": "line"}}])
    perts = _perts(nuit, vieux)
    assert [p["id"] for p in perts] == ["n"]
    etape = {"bouts": ["x"], "arrets": ["x"]}
    assert not pertinente(perts[0], etape, datetime(2026, 11, 6, 8, 27)) and pertinente(perts[0], etape, datetime(2026, 11, 6, 23, 0))
    assert perimee_selon_texte("du 06/04 au 07/04", date(2026, 11, 6)) and not perimee_selon_texte("du 24/08 au 6/11", date(2026, 11, 6))
    assert not perimee_selon_texte("du 15/12 au 05/01", date(2026, 11, 6))


async def test_dedoublonnage_et_equipements():
    asc = [perturbation(f"a{n}", "Panne d'un ascenseur", [{"pt_object": arret("Invalides", "10")}], effet="OTHER_EFFECT",
                        texte="INVALIDES Panne de l'ascenseur situé Rue <> Quai 1") for n in range(3)]
    bus = [perturbation(f"b{n}", "Bus 39-14 : arrêts non desservis", [LIGNE_13]) for n in range(4)]
    msgs = messages_affiches(_perts(*asc, *bus))
    assert [(m["niveau"], m["titre"]) for m in msgs] == [("interrompu", "Bus 39-14 : arrêts non desservis"), ("equipement", "Panne d'un ascenseur")]


async def test_alertes_par_etape(hass: HomeAssistant):
    its = analyser_itineraires(TRAJET)
    asc = perturbation("a", "Panne d'un ascenseur", [{"pt_object": arret("Saint-Lazare", "40")}], effet="OTHER_EFFECT",
                       texte="SAINT-LAZARE Panne de l'ascenseur situé Quai <> Salle d'échanges")
    ailleurs = perturbation("b", "Métro 13 : Duroc non desservi", [{"pt_object": arret("Duroc", "99")}])
    calcul = Itineraires(hass, None)
    calcul.perturbations_info = lambda _ligne: _perts(asc, ailleurs)
    await calcul.alertes(its)
    e = its[0]["etapes"][0]
    assert [m["niveau"] for m in e["messages"]] == ["equipement"] and e["alerte"] is None and its[0]["alerte"] == "normal"


async def test_icones_et_lignes():
    assert icone_lieu({"type": "stop_area", "modes": ["RER", "Métro", "Bus"]}) == "[RER] [M] [BUS]"
    assert icone_lieu({"type": "stop_area", "modes": []}) == "🚏" and icone_lieu({"type": "address"}) == "📍"
    lignes = [
        {"id": "m13", "nom": "13", "mode": "Métro"}, {"id": "b13", "nom": "13", "mode": "Bus"},
        {"id": "rerc", "nom": "C", "mode": "RER"}, {"id": "t6", "nom": "T6", "mode": "Tramway"},
    ]
    assert [choisir_ligne(t, lignes)["id"] for t in ("13", "bus 13", "M13", "RER C", "T6", "t 6")] == ["m13", "b13", "m13", "rerc", "t6", "t6"]
    assert choisir_ligne("A", lignes) is None


async def test_jours_feries():
    d = {"jours": ["0", "1", "2", "3", "4"]}
    armistice = date(2026, 11, 11)  # mercredi
    assert not jour_du_trajet(d, armistice, {armistice}) and jour_du_trajet(d, date(2026, 11, 12), {armistice})
    assert jour_du_trajet({"jours": ["2", "feries"]}, armistice, {armistice})


async def test_formulaire_trajet(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set("zone.home", "0", {"latitude": 48.85, "longitude": 2.30, "friendly_name": "Maison"})
    hass.states.async_set("zone.bureau", "0", {"latitude": 48.87, "longitude": 2.33, "friendly_name": "Bureau"})
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    brancher(aioclient_mock, dt_util.utcnow())
    aioclient_mock.get(re.compile(r"/pt_objects"), json={"pt_objects": [{"embedded_type": "line", "line": l} for l in E.LIGNES["lines"]]})
    entree = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={"jeton": "abc", "zone": "zone.home"})
    entree.add_to_hass(hass)
    await hass.config_entries.async_setup(entree.entry_id)
    await hass.async_block_till_done()

    r = await hass.config_entries.subentries.async_init((entree.entry_id, "trajet"), context={"source": "user"})
    champs = [str(k) for k in r["data_schema"].schema]
    assert "avance" not in champs and "origine" not in champs and "type_heure" not in champs
    assert r["data_schema"].schema["jours"].config["options"][-1] == {"value": "feries", "label": "inclure les jours fériés"}
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {
        "nom": "Cours", "personne": "person.alex", "recherche_depart": "gare", "recherche": "denfert", "jours": ["0", "2", "feries"]})
    assert r["step_id"] == "depart"
    labels = [o["label"] for o in r["data_schema"].schema["depart"].config["options"]]
    assert labels[:2] == ["🏠 Maison", "📌 Bureau"] and labels[2].startswith("[")  or labels[2].startswith("🚏 ")
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"depart": "zone|zone.bureau"})
    assert r["step_id"] == "destination"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"destination": "stop_area:IDFM:70000"})
    # Destination : arrivée, sans séjour ni fenêtre de départ
    assert r["step_id"] == "point_destination"
    champs = [str(k) for k in r["data_schema"].schema]
    assert "arrivee_type" in champs and "sejour" not in champs and "depart_type" not in champs
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"mode": "transports", "arrivee_type": "entre", "arrivee_debut": "08:45:00"})
    assert r["errors"] == {"arrivee_fin": "heure_manquante"}
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"mode": "transports", "arrivee_type": "entre", "arrivee_debut": "09:00:00", "arrivee_fin": "08:45:00"})
    assert r["errors"] == {"arrivee_fin": "fenetre_inversee"}
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"mode": "transports", "arrivee_type": "entre", "arrivee_debut": "08:45:00", "arrivee_fin": "09:00:00"})
    assert r["type"] is FlowResultType.MENU and r["menu_options"] == ["ajouter", "modifier", "general", "terminer"]
    assert r["description_placeholders"]["resume"].splitlines() == [
        "1. **Départ** · Bureau", "2. **Destination** · Denfert-Rochereau (Paris) — 🚌 au plus rapide · arrivée entre 8 h 45 et 9 h 00"]

    # Une étape à vélo entre les deux
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "ajouter"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"recherche": "bourg", "position": "0"})
    assert r["step_id"] == "lieu"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"lieu": "stop_area:IDFM:71946"})
    assert r["step_id"] == "point" and r["description_placeholders"]["point"] == "Étape 1"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {
        "mode": "velo", "duree": 7, "arrivee_type": "apres", "arrivee_debut": "08:10:00", "sejour": 5,
        "depart_type": "aucune", "jours": ["0"]})
    assert r["type"] is FlowResultType.MENU and r["menu_options"] == ["ajouter", "modifier", "supprimer", "general", "terminer"]
    lignes = r["description_placeholders"]["resume"].splitlines()
    assert lignes[1].startswith("2. **Étape 1** · ") and lignes[1].endswith(" — 🚲 7 min · arrivée à partir de 8 h 10 · 5 min sur place · lun")

    # Terminer : itinéraire habituel du seul tronçon en transports (étape → destination)
    flow = r["flow_id"]
    r = await hass.config_entries.subentries.async_configure(flow, {"next_step_id": "terminer"})
    assert r["step_id"] == "itineraire" and r["description_placeholders"]["troncon"] == "Destination"
    options = r["data_schema"].schema["itineraire"].config["options"]
    assert [o["value"] for o in options[-2:]] == ["libre", "autre"]
    assert any("from=stop_area" in str(u) for _, u, *_ in aioclient_mock.mock_calls if "journeys" in str(u))
    r = await hass.config_entries.subentries.async_configure(flow, {"itineraire": "autre"})
    assert r["step_id"] == "itineraire_autre"
    r = await hass.config_entries.subentries.async_configure(flow, {"lignes": "RER Z"})
    assert r["errors"] == {"lignes": "ligne_inconnue"}
    r = await hass.config_entries.subentries.async_configure(flow, {"lignes": "192, RER B"})
    assert r["step_id"] == "itineraire"
    assert [o["value"] for o in r["data_schema"].schema["itineraire"].config["options"]] == ["0", "libre", "autre"]
    appels = [str(u) for _, u, *_ in aioclient_mock.mock_calls if "allowed_id" in str(u)]
    assert appels and "C01192" in appels[-1] and "C01743" in appels[-1]
    r = await hass.config_entries.subentries.async_configure(flow, {"itineraire": "0"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    d = next(s for s in entree.subentries.values() if s.subentry_type == "trajet").data
    assert d["jours"] == ["0", "2", "feries"] and d["origine"] == "zone.bureau" and d["origine_id"] is None and "avance" not in d
    p = d["points"]
    assert [x["mode"] for x in p[1:]] == ["velo", "transports"] and p[1]["jours"] == ["0"] and p[1]["sejour"] == 5
    assert p[2]["resume"] and p[2]["habituel"]["etapes"][1]["bouts"] and "trace" not in p[2]["habituel"]["etapes"][0]
    # Réglages du calcul actuel : l'étape y est le détour
    assert (d["detour_mode"], d["detour_duree"], d["detour_heure"], d["type_heure"], d["heure_depart"]) == ("velo", 7, "08:10:00", "arrivee", "09:00:00")


async def test_calendrier_sans_workday(hass: HomeAssistant):
    from custom_components.transports_idf.trajets import jours_feries

    from custom_components.transports_idf.trajets import feries_france

    feries = await jours_feries(hass, date(2026, 11, 9))
    assert date(2026, 11, 11) in feries and date(2026, 11, 10) not in feries
    # Pâques 2027 : 28 mars → lundi 29 mars, Ascension 6 mai, Pentecôte lundi 17 mai
    assert {date(2027, 3, 29), date(2027, 5, 6), date(2027, 5, 17)} <= feries_france(2027)


async def test_modifier_le_depart(hass: HomeAssistant, aioclient_mock):
    from homeassistant.config_entries import ConfigSubentryData

    hass.states.async_set("zone.home", "0", {"latitude": 48.85, "longitude": 2.30, "friendly_name": "Maison"})
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    brancher(aioclient_mock, dt_util.utcnow())
    data = {"nom": "Cours", "personne": "person.alex", "origine": "zone.home", "jours": ["0", "1"], "type_heure": "depart", "heure_depart": "08:00:00",
            "destination_id": "stop_area:IDFM:70000", "destination_nom": "Denfert-Rochereau", "sections": [], "resume": "x", "avance": 45}
    entree = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={"jeton": "abc", "zone": "zone.home"},
                             subentries_data=[ConfigSubentryData(subentry_type="trajet", title="Cours", unique_id=None, data=data)])
    entree.add_to_hass(hass)
    await hass.config_entries.async_setup(entree.entry_id)
    await hass.async_block_till_done()
    sous = next(iter(entree.subentries.values()))
    r = await entree.start_subentry_reconfigure_flow(hass, sous.subentry_id)
    # Le menu s'ouvre sur le résumé du trajet existant
    assert r["type"] is FlowResultType.MENU and r["description_placeholders"]["resume"].splitlines() == [
        "1. **Départ** · Maison — départ à partir de 8 h 00", "2. **Destination** · Denfert-Rochereau — 🚌 x"]
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "modifier"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"point": "0"})
    assert r["step_id"] == "point_depart"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"depart_type": "apres", "depart_debut": "08:00:00", "recherche": "gare"})
    assert r["step_id"] == "depart"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"depart": "stop_area:IDFM:71946"})
    assert r["type"] is FlowResultType.MENU
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "general"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"nom": "Cours", "personne": "person.alex", "jours": ["0", "1", "feries"]})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "terminer"})
    assert r["step_id"] == "itineraire"  # nouveau départ : itinéraire habituel à rechoisir
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"itineraire": "0"})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "reconfigure_successful"
    d = entree.subentries[sous.subentry_id].data
    assert d["origine_id"] == "stop_area:IDFM:71946" and d["jours"] == ["0", "1", "feries"] and "avance" not in d and d["sections"]
    assert d["points"][0]["depart"]["debut"] == "08:00:00" and d["heure_depart"] == "08:00:00" and d["type_heure"] == "depart"


async def test_rappel_pas_en_retard_si_un_depart_arrive_a_l_heure(hass: HomeAssistant):
    """0.8 : « en retard » affiché alors qu'un départ maintenant arrive avant l'heure cible."""
    from types import SimpleNamespace

    from custom_components.transports_idf.trajets import EtatTrajet, TraficCoordinator

    entree = MockConfigEntry(domain=DOMAIN, data={"jeton": "abc", "zone": "zone.home"})
    entree.add_to_hass(hass)
    coord = TraficCoordinator(hass, entree, None, None)
    maintenant = datetime(2026, 11, 6, 8, 0)

    def option(dep, arr):
        return {"depart": f"20261106T{dep}00", "arrivee": f"20261106T{arr}00", "etapes": [], "sections": []}

    async def plans(*_a, **_k):
        return [option("0750", "0825")]  # seul plan « arrivée 9 h » trouvé, déjà parti (ligne jugée bloquée, etc.)

    async def rien(*_a, **_k):
        return None

    coord._plans, coord._enrichir = plans, rien
    coord._preferees = lambda _t: []
    t = SimpleNamespace(data={"type_heure": "arrivee", "heure_depart": "09:00:00", "depart_habituel": "08:00:00", "jours": ["4"]})
    etat = EtatTrajet()
    etat.direct = [option("0805", "0836"), option("0812", "0850")]
    await coord._rappel(t, etat, maintenant)
    assert etat.rappel["depart"] == "20261106T081200" and not etat.rappel["retard"]
    etat.direct = [option("0805", "0912")]
    await coord._rappel(t, etat, maintenant)
    assert etat.rappel["retard"] and etat.rappel["arrivee"] == "20261106T091200"


async def test_sens_de_la_ligne():
    """Arrêts non desservis « direction X » : l'autre sens n'est pas concerné (quais et parcours propres à un sens)."""
    etape = analyser_itineraires(TRAJET)[0]["etapes"][0]
    etape["route_id"] = "route:IDFM:aller"
    quai_retour = {"pt_object": {"id": "stop_point:IDFM:91", "name": "Invalides", "embedded_type": "stop_point",
                                 "stop_point": {"id": "stop_point:IDFM:91", "name": "Invalides", "stop_area": {"id": "stop_area:IDFM:10", "name": "Invalides"}}}}
    quai_aller = {"pt_object": point("Invalides", "1", "10")}
    troncon_retour = {**LIGNE_13, "impacted_section": {"from": arret("Miromesnil", "30"), "to": arret("Invalides", "10"), "routes": [{"id": "route:IDFM:retour"}]}}
    perts = {p["id"]: p for p in _perts(
        perturbation("r", "Invalides non desservi direction Châtillon", [quai_retour]),
        perturbation("a", "Invalides non desservi direction Saint-Denis", [quai_aller]),
        perturbation("t", "Travaux direction Châtillon", [troncon_retour]),
    )}
    quand = datetime(2026, 11, 6, 8, 27)
    assert sorted(i for i, p in perts.items() if pertinente(p, etape, quand)) == ["a"]


async def test_messages_d_autres_lignes_et_sens():
    """line_reports d'une ligne renvoie les messages des lignes qui partagent ses arrêts ; « direction X » = un seul sens."""
    from custom_components.transports_idf.api import concerne_ligne

    autre = {"lignes": ["line:IDFM:C02000"]}
    assert not concerne_ligne(autre, "line:IDFM:C01383") and concerne_ligne({"lignes": []}, "line:IDFM:C01383")
    etape = analyser_itineraires(TRAJET)[0]["etapes"][0]
    etape["direction"] = "Saint-Denis Université (Saint-Denis)"
    sens = perturbation("s", "Arrêts non desservis", [LIGNE_13], texte="Les arrêts entre Invalides et Miromesnil ne sont plus desservis en direction de Châtillon - Montrouge. Reprise estimée : 18 h.")
    bon = perturbation("b", "Arrêts non desservis direction Saint-Denis Université", [LIGNE_13])
    deux = perturbation("d", "Trafic ralenti dans les deux sens", [LIGNE_13], effet="SIGNIFICANT_DELAYS")
    perts = {p["id"]: p for p in _perts(sens, bon, deux)}
    quand = datetime(2026, 11, 6, 8, 27)
    assert sorted(i for i, p in perts.items() if pertinente(p, etape, quand)) == ["b", "d"]


async def test_migration_des_lignes_d_interet(hass: HomeAssistant, aioclient_mock):
    """≤ 0.9 : lignes d'intérêt dans les options → sous-entrées « ligne »."""
    hass.states.async_set("zone.home", "0", {"latitude": 48.78, "longitude": 2.317})
    brancher(aioclient_mock, dt_util.utcnow())
    ligne = {"id": "line:IDFM:C01192", "code": "192", "nom": "192", "mode": "Bus", "couleur": "#E3B32A", "couleur_texte": "#000000"}
    entree = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={"jeton": "abc", "zone": "zone.home"}, options={"lignes_interet": [ligne], "marge": 3})
    entree.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entree.entry_id)
    await hass.async_block_till_done()
    sous = [s for s in entree.subentries.values() if s.subentry_type == "ligne"]
    assert [(s.title, s.unique_id) for s in sous] == [("Bus 192", "line:IDFM:C01192")]
    assert entree.options == {"marge": 3}
    info = next(s for s in hass.states.async_all("sensor") if s.attributes.get("type_transports_idf") == "info_trafic")
    assert any(l["nom"] == "192" and l["interet"] for l in info.attributes["lignes"])


async def test_plan_ligne(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set("zone.home", "0", {"latitude": 48.78, "longitude": 2.317})
    brancher(aioclient_mock, dt_util.utcnow())
    aioclient_mock.get(re.compile(r"/lines/line(%3A|:)IDFM(%3A|:)C01743/stop_areas"), json={"stop_areas": [
        {"id": "stop_area:IDFM:1", "name": "Bourg-la-Reine", "coord": {"lat": "48.78", "lon": "2.31"}},
        {"id": "stop_area:IDFM:2", "name": "Denfert-Rochereau", "coord": {"lat": "48.83", "lon": "2.33"}}]})
    aioclient_mock.get(re.compile(r"/lines/line(%3A|:)IDFM(%3A|:)C01743(\?|$)"), json={"lines": [{"color": "5291CE", "geojson": {
        "type": "MultiLineString", "coordinates": [[[2.31, 48.78], [2.32, 48.80], [2.33, 48.83]], [[2.31, 48.78], [2.29, 48.76]]]}}]})
    entree = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={"jeton": "abc", "zone": "zone.home"})
    entree.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entree.entry_id)
    await hass.async_block_till_done()
    rep = await hass.services.async_call(DOMAIN, "plan_ligne", {"ligne_id": "line:IDFM:C01743"}, blocking=True, return_response=True)
    # Un seul tronçon entre les deux arrêts ; le bout qui ne mène à aucun arrêt est écarté.
    assert rep["couleur"] == "#5291CE" and rep["traces"] == [[[48.78, 2.31], [48.8, 2.32], [48.83, 2.33]]]
    assert "graphe" not in rep
    assert [a["nom"] for a in rep["arrets"]] == ["Bourg-la-Reine", "Denfert-Rochereau"]


async def test_plan_ligne_par_les_parcours(hass: HomeAssistant, aioclient_mock):
    """Ligne sans tracé : on dessine ceux de ses parcours."""
    hass.states.async_set("zone.home", "0", {"latitude": 48.78, "longitude": 2.317})
    brancher(aioclient_mock, dt_util.utcnow())
    aioclient_mock.get(re.compile(r"/lines/line(%3A|:)IDFM(%3A|:)C01192/stop_areas"), json={"stop_areas": []})
    aioclient_mock.get(re.compile(r"traces-des-lignes"), json={"total_count": 0, "results": []})
    aioclient_mock.get(re.compile(r"/lines/line(%3A|:)IDFM(%3A|:)C01192/routes"), json={"routes": [
        {"id": "route:1", "geojson": {"type": "MultiLineString", "coordinates": [[[2.31, 48.78], [2.30, 48.79], [2.28, 48.80]]]}},
        {"id": "route:2", "geojson": {"type": "MultiLineString", "coordinates": [[[2.28, 48.80], [2.30, 48.79]]]}}]})
    aioclient_mock.get(re.compile(r"/lines/line(%3A|:)IDFM(%3A|:)C01192(\?|$)"), json={"lines": [{"color": "00814F", "geojson": {"type": "MultiLineString", "coordinates": []}}]})
    entree = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={"jeton": "abc", "zone": "zone.home"})
    entree.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entree.entry_id)
    await hass.async_block_till_done()
    rep = await hass.services.async_call(DOMAIN, "plan_ligne", {"ligne_id": "line:IDFM:C01192"}, blocking=True, return_response=True)
    assert [len(t) for t in rep["traces"]] == [3, 2]


async def test_plan_ligne_par_l_open_data(hass: HomeAssistant, aioclient_mock):
    """PRIM sans tracé : on prend celui de l'open data IDFM (route_id IDFM:Cxxxxx)."""
    hass.states.async_set("zone.home", "0", {"latitude": 48.78, "longitude": 2.317})
    brancher(aioclient_mock, dt_util.utcnow())
    aioclient_mock.get(re.compile(r"/lines/line(%3A|:)IDFM(%3A|:)C01743/stop_areas"), json={"stop_areas": []})
    aioclient_mock.get(re.compile(r"traces-des-lignes.*C01743"), json={"total_count": 1, "results": [{"route_id": "IDFM:C01743", "shape": {
        "type": "Feature", "properties": {}, "geometry": {"type": "MultiLineString", "coordinates": [[[2.31, 48.78], [2.32, 48.80], [2.33, 48.83]], [[2.31, 48.78], [2.29, 48.76]]]}}}]})
    aioclient_mock.get(re.compile(r"/lines/line(%3A|:)IDFM(%3A|:)C01743(\?|$)"), json={"lines": [{"color": "5291CE", "geojson": {"type": "MultiLineString", "coordinates": []}}]})
    entree = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={"jeton": "abc", "zone": "zone.home"})
    entree.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entree.entry_id)
    await hass.async_block_till_done()
    rep = await hass.services.async_call(DOMAIN, "plan_ligne", {"ligne_id": "line:IDFM:C01743"}, blocking=True, return_response=True)
    assert [len(t) for t in rep["traces"]] == [3, 2] and rep["couleur"] == "#5291CE"


async def test_workday_pas_encore_charge(hass: HomeAssistant):
    """Au démarrage, capteur Workday enregistré mais sans état : pas d'appel (pas d'erreur au journal), calendrier français."""
    from homeassistant.helpers import entity_registry as er

    from custom_components.transports_idf.trajets import jours_feries

    er.async_get(hass).async_get_or_create("binary_sensor", "workday", "jours_ouvres", suggested_object_id="workday_sensor")
    appels = []

    async def check_date(call):
        appels.append(call)
        return {"binary_sensor.workday_sensor": {"workday": True}}

    from homeassistant.core import SupportsResponse

    hass.services.async_register("workday", "check_date", check_date, supports_response=SupportsResponse.ONLY)
    feries = await jours_feries(hass, date(2026, 11, 9))
    assert appels == [] and date(2026, 11, 11) in feries
    # Une fois Workday chargé, il est bien interrogé.
    hass.states.async_set("binary_sensor.workday_sensor", "on", {"workdays": ["mon", "tue", "wed", "thu", "fri"]})
    await jours_feries(hass, date(2026, 11, 9))
    assert appels


def test_arrets_non_desservis_ne_coupent_pas_la_ligne():
    """Arrêts non desservis (quais d'un sens) : portée « arrets » avec leurs noms ; un tronçon coupé : portée « troncon »."""
    from custom_components.transports_idf.api import analyser_perturbations, messages_affiches

    def quai(ident, nom):
        return {"stop_point": {"id": ident, "name": nom}}

    donnees = {"disruptions": [
        {"id": "nd", "status": "active", "severity": {"name": "bloquante", "effect": "NO_SERVICE"},
         "messages": [{"text": "Bus 192 : arrêts non desservis", "channel": {"types": ["title"]}}],
         "impacted_objects": [{"pt_object": {"id": "line:IDFM:C01192", "embedded_type": "line"},
                               "impacted_stops": [quai("stop_point:IDFM:1", "Les Blagis"), quai("stop_point:IDFM:2", "Parc de Sceaux")]}]},
        {"id": "tr", "status": "active", "severity": {"name": "bloquante", "effect": "NO_SERVICE"},
         "messages": [{"text": "RER B : trafic interrompu entre deux gares", "channel": {"types": ["title"]}}],
         "impacted_objects": [{"pt_object": {"id": "line:IDFM:C01743", "embedded_type": "line"},
                               "impacted_section": {"from": {"embedded_type": "stop_area", "stop_area": {"id": "stop_area:IDFM:9", "name": "Bourg-la-Reine"}},
                                                    "to": {"embedded_type": "stop_area", "stop_area": {"id": "stop_area:IDFM:8", "name": "Denfert-Rochereau"}}}}]},
    ]}
    perts = {p["id"]: p for p in analyser_perturbations(donnees)}
    assert perts["nd"]["portee"] == "arrets" and perts["nd"]["noms_arrets"] == ["les blagis", "parc de sceaux"]
    assert perts["tr"]["portee"] == "troncon" and perts["tr"]["noms_troncon"] == ["bourg la reine", "denfert rochereau"]
    msgs = {m["id"]: m for m in messages_affiches(list(perts.values()))}
    # Avant : aucun nom pour des quais → toute la ligne en pointillés. Maintenant : arrêts marqués, ligne pleine.
    assert msgs["nd"]["portee"] == "arrets" and msgs["nd"]["troncon_touche"] == [] and msgs["nd"]["arrets_touches"] == ["les blagis", "parc de sceaux"]
    assert msgs["tr"]["portee"] == "troncon" and msgs["tr"]["troncon_touche"] == ["bourg la reine", "denfert rochereau"]


def test_arret_non_desservi_rattache_par_son_quai():
    """Message publié sur un quai (line_reports) : on garde le nom et la zone d'arrêt, pour le marquer sur le plan."""
    from custom_components.transports_idf.api import analyser_perturbations, messages_affiches

    quai = {"id": "stop_point:IDFM:22", "embedded_type": "stop_point",
            "stop_point": {"id": "stop_point:IDFM:22", "name": "Odéon", "links": [{"type": "disruption", "id": "od"}],
                           "stop_area": {"id": "stop_area:IDFM:7", "name": "Odéon"}}}
    donnees = {
        "disruptions": [{"id": "od", "status": "active", "severity": {"name": "bloquante", "effect": "NO_SERVICE"},
                         "messages": [{"text": "Métro 10 : arrêt non desservi", "channel": {"types": ["title"]}}],
                         "impacted_objects": [{"pt_object": {"id": "line:IDFM:C01380", "embedded_type": "line"}}]}],
        "line_reports": [{"pt_objects": [quai]}],
    }
    p = analyser_perturbations(donnees)[0]
    assert p["portee"] == "arrets" and p["arrets"] == ["stop_point:IDFM:22"]
    assert p["noms_arrets"] == ["odeon"] and p["zones_arrets"] == ["stop_area:IDFM:7"]
    m = messages_affiches([p])[0]
    assert m["arrets_touches"] == ["odeon"] and m["zones_touchees"] == ["stop_area:IDFM:7"] and m["zones_troncon"] == []


async def test_ancien_trajet_enregistre_sans_rechoisir(hass: HomeAssistant, aioclient_mock):
    """Ancien trajet ouvert puis enregistré tel quel : départ habituel, durée, arrivée et résumé de la carte conservés."""
    from homeassistant.config_entries import ConfigSubentryData

    hass.states.async_set("zone.home", "0", {"latitude": 48.85, "longitude": 2.30, "friendly_name": "Maison"})
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    brancher(aioclient_mock, dt_util.utcnow())
    etapes = [{"type": "marche", "duree": 4}, {"type": "transport", "ligne_id": "line:IDFM:C01743", "bouts": ["x"]}]
    data = {"nom": "Cours", "personne": "person.alex", "origine": "zone.home", "jours": ["0", "1"], "type_heure": "arrivee", "heure_depart": "09:00:00",
            "destination_id": "stop_area:IDFM:70000", "destination_nom": "Denfert-Rochereau", "sections": [{"ligne_id": "line:IDFM:C01743"}], "resume": "RER B",
            "depart_habituel": "08:21:00", "arrivee_habituelle": "08:55:00", "duree": 2040, "etapes": etapes,
            "detour_id": "poi:ecole", "detour_nom": "École", "detour_mode": "marche", "detour_duree": 6, "detour_pause": 5, "detour_jours": ["0", "1"]}
    entree = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={"jeton": "abc", "zone": "zone.home"},
                             subentries_data=[ConfigSubentryData(subentry_type="trajet", title="Cours", unique_id=None, data=data)])
    entree.add_to_hass(hass)
    await hass.config_entries.async_setup(entree.entry_id)
    await hass.async_block_till_done()
    sous = next(iter(entree.subentries.values()))
    r = await entree.start_subentry_reconfigure_flow(hass, sous.subentry_id)
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "terminer"})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "reconfigure_successful"
    d = entree.subentries[sous.subentry_id].data
    assert (d["depart_habituel"], d["arrivee_habituelle"], d["duree"], d["etapes"]) == ("08:21:00", "08:55:00", 2040, etapes)
    assert (d["detour_id"], d["detour_duree"], d["type_heure"], d["heure_depart"], d["resume"]) == ("poi:ecole", 6, "arrivee", "09:00:00", "RER B")
