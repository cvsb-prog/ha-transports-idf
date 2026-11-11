"""Trajets à étapes : modèle de points, migration des anciens trajets, résumé du formulaire."""

from homeassistant.core import HomeAssistant

from custom_components.transports_idf.points import (
    T_POINTS,
    cles_anciennes,
    fenetre,
    point,
    points_depuis_ancien,
    resume_trajet,
)

from .test_trajets import SECTIONS, entree

ANCIEN = {
    "nom": "Travail", "origine": "zone.home", "type_heure": "arrivee", "heure_depart": "09:00:00",
    "destination_id": "stop_area:IDFM:70000", "destination_nom": "Denfert-Rochereau",
    "sections": SECTIONS, "resume": "Bus 192 → RER B",
    "detour_id": "poi:ecole", "detour_nom": "École Jules Ferry", "detour_mode": "marche",
    "detour_duree": 6, "detour_pause": 5, "detour_heure": "08:20:00", "detour_jours": ["0", "1", "3", "4"],
}


def test_migration_detour_devient_etape_1():
    p = points_depuis_ancien(ANCIEN, "Maison")
    assert [x["nom"] for x in p] == ["Maison", "École Jules Ferry", "Denfert-Rochereau"]
    assert p[0]["zone"] == "zone.home" and p[0]["depart"]["type"] == "aucune"
    e = p[1]
    assert (e["mode"], e["duree"], e["sejour"], e["jours"]) == ("marche", 6, 5, ["0", "1", "3", "4"])
    assert e["arrivee"] == {"type": "entre", "debut": "08:20:00", "fin": "08:20:00"}
    assert p[2]["arrivee"] == {"type": "avant", "debut": None, "fin": "09:00:00"}
    assert p[2]["sections"] == SECTIONS and p[2]["resume"] == "Bus 192 → RER B"
    # Aller-retour : le calcul actuel relit les mêmes réglages.
    retour = cles_anciennes(p)
    for k in ("type_heure", "heure_depart", "destination_id", "detour_id", "detour_mode", "detour_duree", "detour_pause", "detour_heure", "detour_jours", "sections"):
        assert retour[k] == ANCIEN[k], k


def test_migration_sans_detour_heure_de_depart():
    d = {k: v for k, v in ANCIEN.items() if not k.startswith("detour_")} | {"type_heure": "depart", "heure_depart": "07:40:00"}
    p = points_depuis_ancien(d)
    assert len(p) == 2 and p[0]["depart"] == {"type": "apres", "debut": "07:40:00", "fin": None}
    assert p[1]["arrivee"]["type"] == "aucune"
    assert cles_anciennes(p)["type_heure"] == "depart" and "detour_id" not in cles_anciennes(p)


def test_plusieurs_etapes_pas_de_cles_anciennes():
    p = [point("Maison", zone="zone.home"), point("A", id="a"), point("B", id="b"), point("Bureau", id="c")]
    assert cles_anciennes(p) is None


def test_resume():
    p = points_depuis_ancien(ANCIEN, "Maison")
    p.insert(2, point("Boulangerie", id="poi:pain", mode="velo", duree=4, sejour=3, jours=["0", "1", "2", "3", "4"],
                      arrivee=fenetre("apres", "08:30:00"), depart=fenetre("entre", "08:35:00", "08:40:00")))
    assert resume_trajet(p).splitlines() == [
        "1. **Départ** · Maison",
        "2. **Étape 1** · École Jules Ferry — 🚶 6 min · arrivée à 8 h 20 · 5 min sur place · lun, mar, jeu, ven",
        "3. **Étape 2** · Boulangerie — 🚲 4 min · arrivée à partir de 8 h 30 · 3 min sur place · départ entre 8 h 35 et 8 h 40 · en semaine",
        "4. **Destination** · Denfert-Rochereau — 🚌 Bus 192 → RER B · arrivée avant 9 h 00",
    ]


async def test_migration_au_demarrage(hass: HomeAssistant, aioclient_mock):
    from .test_trajets import mocks
    hass.states.async_set("zone.home", "0", {"latitude": 48.78, "longitude": 2.317, "friendly_name": "Maison"})
    mocks(aioclient_mock, [])
    e = entree(hass, type_heure="arrivee", heure_depart="09:00:00", detour_id="poi:ecole", detour_nom="École", detour_mode="transports", detour_pause=2)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    sous = next(iter(e.subentries.values()))
    p = sous.data[T_POINTS]
    assert [x["nom"] for x in p] == ["Maison", "École", "Denfert-Rochereau"]
    assert p[1]["mode"] == "transports" and p[1]["duree"] is None and p[1]["sejour"] == 2
    assert sous.data["detour_id"] == "poi:ecole"  # anciens réglages conservés pour le calcul
