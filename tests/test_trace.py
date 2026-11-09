"""Recalage du tracé d'une section sur les arrêts desservis (jamais de ligne droite)."""

from custom_components.transports_idf.api import _longueur, caler_trace, chemin_graphe, reseau_ligne, sans_boucles

ARRET_1 = [48.7800, 2.3100]
ARRET_2 = [48.7840, 2.2850]
AUTRE_BRANCHE = [48.8120, 2.2610]
ARRET_3 = [48.7620, 2.2540]
ARRET_4 = [48.7780, 2.2300]


def entre(a, b, n=10, decale=0.0):
    """Points de a vers b, avec une courbe (decale) pour imiter des voies."""
    return [
        [a[0] + (b[0] - a[0]) * k / n + decale * (k * (n - k)) / (n * n), a[1] + (b[1] - a[1]) * k / n]
        for k in range(n)
    ]


def courbe_en(trace, a, b):
    """Le tracé garde des points hors de la droite a-b (il suit les voies)."""
    return any(abs(p[0] - (a[0] + (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]))) > 0.001 for p in trace if min(a[1], b[1]) < p[1] < max(a[1], b[1]))


def test_aller_retour_sur_une_autre_branche_coupe_sans_ligne_droite():
    # 2 → autre branche → retour à 2 (presque mêmes voies) → 3 en courbe → 4
    retour = [[p[0] + 0.0002, p[1]] for p in entre(AUTRE_BRANCHE, ARRET_2)]
    forme = entre(ARRET_1, ARRET_2) + entre(ARRET_2, AUTRE_BRANCHE) + retour + entre(ARRET_2, ARRET_3, 10, 0.02) + entre(ARRET_3, ARRET_4) + [ARRET_4]
    trace, douteux = caler_trace(forme, [ARRET_1, ARRET_2, ARRET_3, ARRET_4])
    assert not douteux
    assert max(p[0] for p in trace) < 48.79  # plus de détour par l'autre branche
    assert courbe_en(trace, ARRET_2, ARRET_3)  # la courbe des voies est gardée
    assert _longueur(trace) < 1.5 * _longueur([ARRET_1, ARRET_2, ARRET_3, ARRET_4])


def test_trace_plausible_inchange():
    forme = entre(ARRET_1, ARRET_2) + entre(ARRET_2, ARRET_3, 10, 0.01) + [ARRET_3]
    trace, douteux = caler_trace(forme, [ARRET_1, ARRET_2, ARRET_3])
    assert not douteux and trace == forme


def test_forme_a_l_envers():
    forme = (entre(ARRET_1, ARRET_2) + entre(ARRET_2, ARRET_3) + [ARRET_3])[::-1]
    trace, douteux = caler_trace(forme, [ARRET_1, ARRET_2, ARRET_3])
    assert not douteux and trace[0] == ARRET_1 and trace[-1] == ARRET_3


def test_sans_trace_douteux_pour_reprendre_le_plan():
    assert caler_trace([], [ARRET_1, ARRET_2]) == ([], True)


def test_sans_boucles_garde_un_trace_simple():
    forme = entre(ARRET_1, ARRET_2, 20, 0.01)
    assert sans_boucles(forme) == forme


def test_reseau_sans_doublons_ni_detours_et_chemin():
    """Formes superposées (une par variante), dont une avec un détour : un tronçon par paire d'arrêts voisins."""
    tronc = entre(ARRET_1, ARRET_2)
    vers_sud = tronc + entre(ARRET_2, ARRET_3, 10, 0.02) + entre(ARRET_3, ARRET_4) + [ARRET_4]
    vers_nord = tronc + entre(ARRET_2, AUTRE_BRANCHE) + [AUTRE_BRANCHE]
    decalee = [[p[0] + 0.0003, p[1]] for p in vers_sud]  # même parcours, tracé légèrement décalé
    retour = [[p[0] + 0.0001, p[1]] for p in entre(AUTRE_BRANCHE, ARRET_2)]
    detour = entre(ARRET_1, ARRET_2) + entre(ARRET_2, AUTRE_BRANCHE) + retour + entre(ARRET_2, ARRET_3, 10, 0.02) + [ARRET_3]
    arrets = [ARRET_1, ARRET_2, ARRET_3, ARRET_4, AUTRE_BRANCHE]
    troncons = reseau_ligne([vers_sud, vers_nord, decalee, detour], arrets)
    assert sorted(troncons) == [(0, 1), (1, 2), (1, 4), (2, 3)]
    assert courbe_en(troncons[(1, 2)], ARRET_2, ARRET_3)
    graphe = {"noeuds": arrets, "troncons": [[a, b, t] for (a, b), t in troncons.items()]}
    chemin = chemin_graphe(graphe, [ARRET_1, ARRET_4])  # train direct : les arrêts sautés sont suivis par le plan
    assert chemin and max(p[0] for p in chemin) < 48.785 and _longueur(chemin) < 1.3 * _longueur([ARRET_1, ARRET_2, ARRET_3, ARRET_4])
    assert chemin_graphe(graphe, [ARRET_1, [48.9, 2.5]]) is None


def pas_a_pas(a, b, pas_m=500, depart=0.0):
    """Points espacés d'environ pas_m, en commençant à depart × pas (tracé PRIM peu dense)."""
    from custom_components.transports_idf.api import _metres

    n = max(1, round(_metres(a, b) / pas_m))
    return [[a[0] + (b[0] - a[0]) * (k + depart) / n, a[1] + (b[1] - a[1]) * (k + depart) / n] for k in range(n)]


def test_aller_retour_peu_dense_et_decale():
    # Points tous les 500 m ; au retour, ils ne tombent pas sur ceux de l'aller (décalés d'un demi-pas, voie d'à côté).
    retour = [[p[0] + 0.0001, p[1]] for p in pas_a_pas(AUTRE_BRANCHE, ARRET_2, depart=0.5)]
    forme = pas_a_pas(ARRET_1, ARRET_2) + pas_a_pas(ARRET_2, AUTRE_BRANCHE) + retour + pas_a_pas(ARRET_2, ARRET_3) + pas_a_pas(ARRET_3, ARRET_4) + [ARRET_4]
    for arrets in ([ARRET_1, ARRET_2, ARRET_3, ARRET_4], [ARRET_1, ARRET_4]):
        trace, douteux = caler_trace(forme, arrets)
        assert max(p[0] for p in trace) < 48.785, arrets  # plus le moindre bout de l'autre branche
        assert not douteux


def test_forme_reelle_partant_sur_une_autre_ligne():
    """Forme réelle d'une section de RER (déplacée et retournée) : part sur une autre ligne, revient au départ, puis dessert les arrêts."""
    import json
    from pathlib import Path

    donnees = json.loads((Path(__file__).parent / "forme_rer_cassee.json").read_text())
    forme, arrets = donnees["forme"], donnees["arrets"]
    assert _longueur(forme) > 20_000  # ce que PRIM renvoie
    trace, douteux = caler_trace(forme, arrets)
    assert not douteux
    assert _longueur(trace) < 8_000
    assert max(p[0] for p in trace) < max(a[0] for a in arrets) + 0.002  # plus de grand détour vers le nord
