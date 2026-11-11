"""Trajets à étapes : une suite de points (départ → étapes → destination).

Chaque point :
    id, zone, nom, lat, lon   lieu Navitia (id) ou zone de Home Assistant (zone)
    mode                      façon d'y arriver : transports, marche, velo, voiture (sans objet pour le départ)
    duree                     minutes pour y arriver hors transports (estimation enregistrée)
    sejour                    minutes minimum sur place (étapes seulement)
    jours                     jours où l'étape a lieu (None : tous les jours du trajet)
    arrivee, depart           fenêtres horaires {type, debut, fin}
    sections, resume          itinéraire habituel du tronçon qui arrive à ce point

Fenêtres : « aucune », « apres » (à partir de debut), « avant » (au plus tard fin),
« entre » (debut … fin). Le départ n'a qu'une fenêtre de départ, la destination
qu'une fenêtre d'arrivée.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .const import (
    JOURS_DEFAUT,
    T_DEST_ID,
    T_DEST_NOM,
    T_DET_DUREE,
    T_DET_HEURE,
    T_DET_ID,
    T_DET_JOURS,
    T_DET_MODE,
    T_DET_NOM,
    T_DET_PAUSE,
    T_HEURE,
    T_ORIGINE,
    T_ORIGINE_ID,
    T_ORIGINE_NOM,
    T_RESUME,
    T_SECTIONS,
    T_TYPE_HEURE,
)

T_POINTS = "points"

AUCUNE, APRES, AVANT, ENTRE = "aucune", "apres", "avant", "entre"
TYPES_FENETRE = (AUCUNE, APRES, AVANT, ENTRE)
ICONES_MODE = {"transports": "🚌", "marche": "🚶", "velo": "🚲", "voiture": "🚗"}
NOMS_JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]


def fenetre(type_: str = AUCUNE, debut: str | None = None, fin: str | None = None) -> dict[str, Any]:
    if type_ not in TYPES_FENETRE:
        type_ = AUCUNE
    return {
        "type": type_,
        "debut": debut if type_ in (APRES, ENTRE) else None,
        "fin": fin if type_ in (AVANT, ENTRE) else None,
    }


def point(nom: str, *, id: str | None = None, zone: str | None = None, lat: float | None = None, lon: float | None = None,
          mode: str = "transports", duree: int | None = None, sejour: int = 0, jours: list[str] | None = None,
          arrivee: dict | None = None, depart: dict | None = None) -> dict[str, Any]:
    return {
        "id": id, "zone": zone, "nom": nom, "lat": lat, "lon": lon,
        "mode": mode, "duree": duree, "sejour": sejour, "jours": jours,
        "arrivee": arrivee or fenetre(), "depart": depart or fenetre(),
        "sections": [], "resume": "",
    }


def _plus(heure: str, minutes: float) -> str:
    h, m, *s = (int(x) for x in heure.split(":"))
    t = datetime(2000, 1, 1, h, m, s[0] if s else 0) + timedelta(minutes=minutes)
    return t.strftime("%H:%M:%S")


# ---- Migration des anciens trajets (origine → détour éventuel → destination) ----------
def points_depuis_ancien(d: dict[str, Any], nom_zone: str | None = None) -> list[dict[str, Any]]:
    """Points équivalents à un trajet enregistré avant les étapes multiples (nom_zone : nom de la zone de départ)."""
    heure, arrivee = d.get(T_HEURE), d.get(T_TYPE_HEURE) == "arrivee"
    depart = point(
        d.get(T_ORIGINE_NOM) or nom_zone or "Maison", id=d.get(T_ORIGINE_ID), zone=d.get(T_ORIGINE),
        depart=fenetre(APRES, heure) if heure and not arrivee else None,
    )
    sortie = [depart]
    if d.get(T_DET_ID):
        h = d.get(T_DET_HEURE)
        sortie.append(point(
            d.get(T_DET_NOM) or "Étape", id=d[T_DET_ID], mode=d.get(T_DET_MODE) or "marche",
            duree=None if d.get(T_DET_MODE) == "transports" else int(float(d.get(T_DET_DUREE) or 0)),
            sejour=int(float(d.get(T_DET_PAUSE) or 0)), jours=d.get(T_DET_JOURS),
            arrivee=fenetre(ENTRE, h, h) if h else None,  # ancienne « heure imposée » : on y est à cette heure-là
        ))
    destination = point(d.get(T_DEST_NOM) or "Destination", id=d.get(T_DEST_ID), arrivee=fenetre(AVANT, fin=heure) if heure and arrivee else None)
    destination["sections"], destination["resume"] = list(d.get(T_SECTIONS) or []), d.get(T_RESUME) or ""
    sortie.append(destination)
    return sortie


def points_du_trajet(d: dict[str, Any], nom_zone: str | None = None) -> list[dict[str, Any]]:
    return [dict(p) for p in d[T_POINTS]] if d.get(T_POINTS) else points_depuis_ancien(d, nom_zone)


def cles_anciennes(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Réglages « à l'ancienne » (au plus une étape), lus par le calcul tant qu'il ne gère pas plusieurs étapes.

    None si le trajet a plusieurs étapes.
    """
    if len(points) < 2 or len(points) > 3:
        return None
    o, dest = points[0], points[-1]
    sortie: dict[str, Any] = {
        T_ORIGINE_ID: o.get("id"), T_ORIGINE_NOM: o.get("nom") if o.get("id") else None,
        T_DEST_ID: dest.get("id"), T_DEST_NOM: dest.get("nom"),
        T_SECTIONS: dest.get("sections") or [], T_RESUME: dest.get("resume") or "",
    }
    if o.get("zone"):
        sortie[T_ORIGINE] = o["zone"]  # aussi quand le départ est un lieu : coordonnées de secours
    fa, fd = dest.get("arrivee") or fenetre(), o.get("depart") or fenetre()
    if fa["type"] in (AVANT, ENTRE):
        sortie[T_TYPE_HEURE], sortie[T_HEURE] = "arrivee", fa["fin"]
    elif fd["type"] in (APRES, ENTRE):
        sortie[T_TYPE_HEURE], sortie[T_HEURE] = "depart", fd["debut"]
    if len(points) == 3:
        e = points[1]
        sortie.update({
            T_DET_ID: e.get("id"), T_DET_NOM: e.get("nom"), T_DET_MODE: e.get("mode") or "marche",
            T_DET_DUREE: e.get("duree") or 0, T_DET_PAUSE: e.get("sejour") or 0,
            T_DET_JOURS: e.get("jours") if e.get("jours") is not None else list(JOURS_DEFAUT),
        })
        fe = e.get("arrivee") or fenetre()
        if fe["type"] in (APRES, ENTRE):
            sortie[T_DET_HEURE] = fe["debut"]
    return sortie


# ---- Textes (résumé du formulaire) ----------------------------------------------------
def heure_courte(heure: str | None) -> str:
    if not heure:
        return "?"
    h, m, *_ = heure.split(":")
    return f"{int(h)} h {m}"


def texte_fenetre(f: dict[str, Any] | None, verbe: str) -> str:
    """« arrivée avant 8 h 30 », « départ entre 7 h 00 et 7 h 15 », « arrivée à 9 h 00 »…"""
    f = f or fenetre()
    t = f["type"]
    if t == APRES:
        return f"{verbe} à partir de {heure_courte(f['debut'])}"
    if t == AVANT:
        return f"{verbe} avant {heure_courte(f['fin'])}"
    if t == ENTRE:
        if f["debut"] == f["fin"]:
            return f"{verbe} à {heure_courte(f['debut'])}"
        return f"{verbe} entre {heure_courte(f['debut'])} et {heure_courte(f['fin'])}"
    return ""


def texte_jours(jours: list[str] | None) -> str:
    if jours is None:
        return ""
    n = sorted(int(j) for j in jours if j.isdigit())
    if n == list(range(5)):
        return "en semaine"
    if n == list(range(7)):
        return ""
    if n and n == list(range(n[0], n[-1] + 1)) and len(n) > 2:
        return f"du {NOMS_JOURS[n[0]]} au {NOMS_JOURS[n[-1]]}"
    return ", ".join(NOMS_JOURS[i] for i in n) or "jamais"


def ligne_resume(p: dict[str, Any], rang: int, total: int) -> str:
    """Une ligne du résumé de trajet, en Markdown."""
    if rang == 0:
        titre, details = f"**Départ** · {p['nom']}", [texte_fenetre(p.get("depart"), "départ")]
    elif rang == total - 1:
        titre, details = f"**Destination** · {p['nom']}", [texte_fenetre(p.get("arrivee"), "arrivée")]
    else:
        titre = f"**Étape {rang}** · {p['nom']}"
        details = [
            texte_fenetre(p.get("arrivee"), "arrivée"),
            f"{p['sejour']} min sur place" if p.get("sejour") else "",
            texte_fenetre(p.get("depart"), "départ"),
            texte_jours(p.get("jours")),
        ]
    if rang:
        mode = p.get("mode") or "transports"
        trajet = p.get("resume") if mode == "transports" and p.get("resume") else (
            f"{p['duree']} min" if p.get("duree") else ("ligne au choix" if mode == "transports" else "")
        )
        titre = f"{ICONES_MODE.get(mode, '')} {titre}" + (f" ({trajet})" if trajet else "")
    details = [x for x in details if x]
    return f"{rang + 1}. {titre}" + (f" — {', '.join(details)}" if details else "")


def resume_trajet(points: list[dict[str, Any]]) -> str:
    if not points:
        return "_(aucun point)_"
    return "\n".join(ligne_resume(p, i, len(points)) for i, p in enumerate(points))
