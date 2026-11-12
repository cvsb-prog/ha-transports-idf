"""Horaires d'un trajet à étapes, à durées de tronçon connues (minutes depuis minuit).

Règles (spécification du 11/11) :
- « à partir de » est toujours tenu : en avance, on attend sur place ;
- « avant » peut être manqué : c'est un retard ;
- par défaut, on part le plus tard possible, puis on enchaîne sans attente inutile ;
- impossible : rien n'est sauté ni raccourci. Avant le point qui bloque, on arrive au plus juste ; à partir de lui,
  tout se fait au plus tôt, si bien que chaque retard est le plus petit possible.

Le calcul : au plus tôt (passe avant), au plus tard (passe arrière), avec des bornes « avant » relâchées à l'heure
au plus tôt là où elles sont intenables. On part à l'heure au plus tard ainsi obtenue et on enchaîne.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .points import APRES, AVANT, ENTRE, fenetre

INF = math.inf


def minutes(heure: str | None) -> float | None:
    """« HH:MM[:SS] » → minutes depuis minuit."""
    if not heure:
        return None
    h, m, *_ = (int(x) for x in heure.split(":"))
    return h * 60 + m


def bornes(f: dict[str, Any] | None) -> tuple[float, float]:
    """Fenêtre → (au plus tôt, au plus tard), -inf / +inf si pas de borne."""
    f = f or fenetre()
    bas = minutes(f.get("debut")) if f.get("type") in (APRES, ENTRE) else None
    haut = minutes(f.get("fin")) if f.get("type") in (AVANT, ENTRE) else None
    return (-INF if bas is None else bas), (INF if haut is None else haut)


def points_du_jour(points: list[dict[str, Any]], jour: date) -> list[dict[str, Any]]:
    """Les étapes qui n'ont pas lieu ce jour-là sont retirées (le départ et la destination restent)."""
    j = str(jour.weekday())
    return [p for i, p in enumerate(points) if i in (0, len(points) - 1) or p.get("jours") is None or j in p["jours"]]


@dataclass
class Horaire:
    arrivee: float | None = None  # minutes depuis minuit (None pour le départ)
    depart: float | None = None  # None pour la destination
    attente: float = 0  # attente sur place au-delà du temps minimum (borne « à partir de »)
    retard_arrivee: float = 0  # minutes après la borne « avant » d'arrivée
    retard_depart: float = 0


@dataclass
class Planning:
    possible: bool
    horaires: list[Horaire] = field(default_factory=list)
    bloquant: int | None = None  # premier point dont une borne « avant » est intenable
    libre: bool = False  # aucune borne : pas d'heure de départ imposée (à fixer par l'appelant)

    @property
    def depart(self) -> float | None:
        return self.horaires[0].depart if self.horaires else None


def planifier(points: list[dict[str, Any]], durees: list[float], depart_libre: float | None = None, partir_a: float | None = None) -> Planning:
    """durees[i] : minutes du tronçon qui arrive au point i (durees[0] ignoré).

    depart_libre : heure de départ à prendre si rien ne la fixe (ni borne au départ, ni borne « avant » nulle part).
    partir_a : départ imposé (au départ de maintenant) ; on enchaîne au plus tôt, les retards se constatent.
    """
    n = len(points)
    if n < 2:
        return Planning(True)
    sej = [0.0] + [float(p.get("sejour") or 0) for p in points[1:-1]] + [0.0]
    a_bas, a_haut = zip(*[bornes(p.get("arrivee")) if i else (-INF, INF) for i, p in enumerate(points)])
    d_bas, d_haut = zip(*[bornes(p.get("depart")) if i < n - 1 else (-INF, INF) for i, p in enumerate(points)])
    d = [0.0] + [float(x) for x in durees[1:n]]

    # Au plus tôt (en partant au plus tôt permis)
    e_arr, e_dep = [-INF] * n, [-INF] * n
    e_dep[0] = d_bas[0]
    for i in range(1, n):
        e_arr[i] = max(e_dep[i - 1] + d[i], a_bas[i])
        e_dep[i] = max(e_arr[i] + sej[i], d_bas[i]) if i < n - 1 else INF

    # Bornes « avant » relâchées là où elles sont intenables : le premier point concerné est le point bloquant.
    bloquant = None
    a_max, d_max = list(a_haut), list(d_haut)
    for i in range(n):
        if e_arr[i] > a_max[i]:
            a_max[i] = e_arr[i]
            bloquant = i if bloquant is None else bloquant
        if i < n - 1 and e_dep[i] > d_max[i]:
            d_max[i] = e_dep[i]
            bloquant = i if bloquant is None else bloquant

    # Au plus tard (sous les bornes relâchées)
    l_arr, l_dep = [INF] * n, [INF] * n
    l_arr[n - 1] = a_max[n - 1]
    for i in range(n - 2, -1, -1):
        l_dep[i] = min(d_max[i], l_arr[i + 1] - d[i + 1])
        l_arr[i] = min(a_max[i], l_dep[i] - sej[i]) if i else -INF

    depart = l_dep[0] if partir_a is None else partir_a
    libre = not math.isfinite(depart)
    if libre:
        depart = d_bas[0] if math.isfinite(d_bas[0]) else depart_libre
    if depart is None or not math.isfinite(depart):
        return Planning(True, [], None, True)
    if partir_a is None:
        depart = max(depart, d_bas[0])

    # On part au plus tard, puis on enchaîne sans attente inutile.
    horaires = [Horaire(depart=depart, retard_depart=max(0.0, depart - d_haut[0]))]
    t = depart
    for i in range(1, n):
        arrivee = t + d[i]
        h = Horaire(arrivee=arrivee, retard_arrivee=max(0.0, arrivee - a_haut[i]))
        if i < n - 1:
            pret = max(arrivee, a_bas[i]) + sej[i]
            t = max(pret, d_bas[i])
            h.attente = t - arrivee - sej[i]
            h.depart = t
            h.retard_depart = max(0.0, t - d_haut[i])
        horaires.append(h)
    possible = not any(h.retard_arrivee or h.retard_depart for h in horaires)
    if not possible and partir_a is not None:
        bloquant = next(i for i, h in enumerate(horaires) if h.retard_arrivee or h.retard_depart)
    return Planning(possible, horaires, None if possible else bloquant, libre)


def besoin_planning(points: list[dict[str, Any]]) -> bool:
    """Le trajet sort-il de l'ancien modèle (au plus une étape, heure d'arrivée ou de départ) ?"""
    if len(points) > 3:
        return True
    if (points[-1].get("arrivee") or {}).get("type") in (APRES, ENTRE):
        return True
    if (points[0].get("depart") or {}).get("type") in (AVANT, ENTRE):
        return True
    return len(points) == 3 and (points[1].get("depart") or {}).get("type", "aucune") != "aucune"


def bornes_point(points: list[dict[str, Any]], i: int) -> tuple[float, float, float, float]:
    """(arrivée au plus tôt, au plus tard, départ au plus tôt, au plus tard) du point i."""
    n = len(points)
    a = bornes(points[i].get("arrivee")) if i else (-INF, INF)
    d = bornes(points[i].get("depart")) if i < n - 1 else (-INF, INF)
    return a[0], a[1], d[0], d[1]


def hhmm(m: float | None) -> str:
    if m is None:
        return "—"
    m = int(round(m))
    return f"{m // 60 % 24} h {m % 60:02d}"
