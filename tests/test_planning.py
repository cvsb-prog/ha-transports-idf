"""Horaires d'un trajet à étapes (durées de tronçon connues)."""

from datetime import date

from custom_components.transports_idf.planning import hhmm, planifier, points_du_jour
from custom_components.transports_idf.points import fenetre, point


def heures(p):
    return [(hhmm(h.arrivee), hhmm(h.depart)) for h in p.horaires]


def test_partir_au_plus_tard_puis_enchainer():
    """Maison → école (arrivée entre 8 h 20 et 8 h 30, 5 min) → boulangerie (3 min) → bureau avant 9 h."""
    pts = [point("Maison"), point("École", sejour=5, arrivee=fenetre("entre", "08:20", "08:30")),
           point("Boulangerie", sejour=3), point("Bureau", arrivee=fenetre("avant", fin="09:00"))]
    p = planifier(pts, [0, 10, 5, 25])
    assert p.possible and not p.libre
    assert heures(p) == [("—", "8 h 12"), ("8 h 22", "8 h 27"), ("8 h 32", "8 h 35"), ("9 h 00", "—")]


def test_a_partir_de_tenu_par_une_attente():
    """Crèche à partir de 8 h 30 : en partant plus tôt on attendrait ; au plus tard, on y est pile à l'ouverture."""
    pts = [point("Maison", depart=fenetre("apres", "07:30")), point("Crèche", sejour=5, arrivee=fenetre("apres", "08:30")),
           point("Bureau", arrivee=fenetre("avant", fin="09:30"))]
    p = planifier(pts, [0, 10, 25])
    # Au plus tard : arrivée au bureau 9 h 30 → crèche quittée 9 h 05 → arrivée 9 h 00 → départ 8 h 50.
    assert p.possible and heures(p) == [("—", "8 h 50"), ("9 h 00", "9 h 05"), ("9 h 30", "—")]


def test_impossible_point_bloquant_et_retard_minimal():
    """Crèche à partir de 8 h 40, 5 min sur place, 25 min de trajet : bureau avant 9 h intenable (+10 min)."""
    pts = [point("Maison"), point("Crèche", sejour=5, arrivee=fenetre("apres", "08:40")),
           point("Bureau", arrivee=fenetre("avant", fin="09:00"))]
    p = planifier(pts, [0, 10, 25])
    assert not p.possible and p.bloquant == 2
    assert heures(p) == [("—", "8 h 30"), ("8 h 40", "8 h 45"), ("9 h 10", "—")]  # au plus juste avant, au plus tôt après
    assert p.horaires[2].retard_arrivee == 10


def test_retard_intermediaire_reporte_puis_absorbe():
    """Départ pas avant 8 h 25, école avant 8 h 30 à 10 min (+5) ; la suite au plus tôt, l'attente de la piscine absorbe."""
    pts = [point("Maison", depart=fenetre("apres", "08:25")), point("École", sejour=2, arrivee=fenetre("avant", fin="08:30")),
           point("Piscine", sejour=45, arrivee=fenetre("apres", "09:00")), point("Bureau", arrivee=fenetre("avant", fin="10:30"))]
    p = planifier(pts, [0, 10, 15, 20])
    assert not p.possible and p.bloquant == 1
    assert p.horaires[1].retard_arrivee == 5
    assert heures(p) == [("—", "8 h 25"), ("8 h 35", "8 h 37"), ("8 h 52", "9 h 45"), ("10 h 05", "—")]
    assert p.horaires[2].attente == 8 and p.horaires[3].retard_arrivee == 0


def test_cinq_points_et_jours_de_l_etape():
    """Le mercredi, pas d'école : le trajet est calculé sans elle."""
    pts = [point("Maison"), point("École", sejour=5, jours=["0", "1", "3", "4"], arrivee=fenetre("entre", "08:20", "08:30")),
           point("Pressing", sejour=5, depart=fenetre("avant", fin="08:50")), point("Café", sejour=10),
           point("Bureau", arrivee=fenetre("entre", "09:15", "09:30"))]
    lundi = points_du_jour(pts, date(2026, 11, 16))
    p = planifier(lundi, [0, 10, 8, 6, 12])
    assert p.possible and heures(p) == [("—", "8 h 20"), ("8 h 30", "8 h 35"), ("8 h 43", "8 h 48"), ("8 h 54", "9 h 04"), ("9 h 16", "—")]
    mercredi = points_du_jour(pts, date(2026, 11, 18))
    assert [x["nom"] for x in mercredi] == ["Maison", "Pressing", "Café", "Bureau"]
    p = planifier(mercredi, [0, 15, 6, 12])
    assert p.possible and heures(p) == [("—", "8 h 30"), ("8 h 45", "8 h 50"), ("8 h 56", "9 h 06"), ("9 h 18", "—")]


def test_sans_aucune_borne():
    pts = [point("Maison"), point("Parc", sejour=30), point("Maison")]
    p = planifier(pts, [0, 10, 10])
    assert p.libre and p.horaires == []
    p = planifier(pts, [0, 10, 10], depart_libre=14 * 60)
    assert heures(p) == [("—", "14 h 00"), ("14 h 10", "14 h 40"), ("14 h 50", "—")]


def test_au_depart_de_maintenant():
    """Départ imposé (maintenant) : on enchaîne au plus tôt, le retard se constate au premier point concerné."""
    pts = [point("Maison"), point("École", sejour=5, arrivee=fenetre("entre", "08:20", "08:30")),
           point("Bureau", arrivee=fenetre("avant", fin="09:00"))]
    p = planifier(pts, [0, 10, 25], partir_a=8 * 60 + 25)
    assert not p.possible and p.bloquant == 1
    assert heures(p) == [("—", "8 h 25"), ("8 h 35", "8 h 40"), ("9 h 05", "—")]
    p = planifier(pts, [0, 10, 25], partir_a=8 * 60)  # tôt : attente à l'école jusqu'à 8 h 20
    assert p.possible and heures(p) == [("—", "8 h 00"), ("8 h 10", "8 h 25"), ("8 h 50", "—")] and p.horaires[1].attente == 10
