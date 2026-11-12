"""Constantes de l'intégration Transports IDF."""

from __future__ import annotations

DOMAIN = "transports_idf"
NOM = "Transports IDF"
VERSION = "1.1.0b2"

BASE_URL = "https://prim.iledefrance-mobilites.fr/marketplace"
NAVITIA = f"{BASE_URL}/v2/navitia"
# Tracés des lignes (formes du GTFS), open data IDFM, sans clé : PRIM ne les fournit pas.
TRACES_OPENDATA = "https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/traces-des-lignes-de-transport-en-commun-idfm/records"

# Entrée principale
CONF_JETON = "jeton"
CONF_ZONE = "zone"

# Options générales
OPT_MARGE = "marge"
OPT_INTERVALLE_JOUR = "intervalle_jour"
OPT_INTERVALLE_NUIT = "intervalle_nuit"
OPT_DEBUT_NUIT = "debut_nuit"
OPT_FIN_NUIT = "fin_nuit"
OPT_NB_PASSAGES = "nb_passages"
OPT_IGNORER_INFO = "ignorer_information"
OPT_CLE_CARTO = "cle_carto"  # clé gratuite CARTO (fond de carte plus sobre) ; vide : OpenStreetMap
OPT_LIGNES_INTERET = "lignes_interet"  # [{id, code, nom, mode, couleur, couleur_texte}]

DEFAUTS = {
    OPT_MARGE: 2,
    OPT_INTERVALLE_JOUR: 30,
    OPT_INTERVALLE_NUIT: 300,
    OPT_DEBUT_NUIT: "01:30:00",
    OPT_FIN_NUIT: "05:00:00",
    OPT_NB_PASSAGES: 6,
    OPT_IGNORER_INFO: True,
    "lignes_interet": [],
}

# Sous-entrées
SOUS_ARRET = "arret"
SOUS_TRAJET = "trajet"
SOUS_LIGNE = "ligne"  # ligne d'intérêt (panneau info trafic)

# Données d'un arrêt
A_ZONE_ID = "zone_id"  # stop_area Navitia
A_NOM = "nom"
A_COMMUNE = "commune"
A_REFS = "refs"  # MonitoringRef SIRI retenues
A_LIGNES = "lignes"  # [{id, code, mode, couleur, couleur_texte, nom}]
A_LIGNES_SUIVIES = "lignes_suivies"  # ids, vide = toutes
A_MARCHE = "temps_marche"  # minutes
A_MARCHE_CALCULE = "temps_marche_calcule"  # minutes, None si estimé
A_LAT = "lat"
A_LON = "lon"

# Données d'un trajet
T_NOM = "nom"
T_PERSONNE = "personne"
T_ORIGINE = "origine"  # entity_id d'une zone
T_DEST_ID = "destination_id"
T_DEST_NOM = "destination_nom"
T_HEURE = "heure_depart"  # heure d'arrivée cible OU de départ, selon T_TYPE_HEURE
T_TYPE_HEURE = "type_heure"  # "arrivee" ou "depart"
T_DEPART_HABITUEL = "depart_habituel"  # « HH:MM:SS », départ de l'itinéraire choisi
T_ARRIVEE_HABITUELLE = "arrivee_habituelle"
T_DUREE = "duree"  # secondes
T_AVANCE = "avance"  # ancien réglage de veille (ignoré : la veille commence VEILLE_MINUTES avant le départ habituel)
VEILLE_MINUTES = 60
T_SECTIONS = "sections"  # [{ligne_id, code, mode, couleur, de, vers, direction}]
T_RESUME = "resume"
T_ETAPES = "etapes"  # étapes de l'itinéraire choisi (résumé replié de la carte)
T_ORIGINE_ID = "origine_id"  # lieu Navitia (adresse, arrêt…) si le départ n'est pas une zone
T_ORIGINE_NOM = "origine_nom"
T_JOURS = "jours"  # jours du trajet ("0" = lundi … "6" = dimanche) ; défaut : lundi–vendredi

# Détour avant les transports (facultatif)
T_DET_ID = "detour_id"
T_DET_NOM = "detour_nom"
T_DET_MODE = "detour_mode"  # marche, velo, voiture, transports
T_DET_DUREE = "detour_duree"  # minutes pour y aller (sauf en transports : calculé en direct)
T_DET_PAUSE = "detour_pause"  # minutes sur place
T_DET_HEURE = "detour_heure"  # « HH:MM:SS » : heure d'arrivée imposée au détour (facultatif)
T_DET_JOURS = "detour_jours"
JOURS_DEFAUT = ["0", "1", "2", "3", "4"]
JOUR_FERIE = "feries"  # dans T_JOURS : le trajet a lieu aussi les jours fériés
MODES_DETOUR = {"marche": "walking", "velo": "bike", "voiture": "car"}

# Écart à partir duquel on conseille de partir plus tôt / un autre itinéraire
ECART_MINUTES = 3

# Événement d'alerte, exploité par une automatisation
EVENEMENT_ALERTE = f"{DOMAIN}_alerte"

ETAT_ATTEIGNABLE = "atteignable"
ETAT_PRESSE = "presse"
ETAT_TROP_TARD = "trop_tard"

# Effets Navitia considérés comme sans gravité (information seule)
EFFETS_INFO = {"OTHER_EFFECT", "UNKNOWN_EFFECT", "ADDITIONAL_SERVICE"}
# Effets qui justifient d'éviter la ligne dans un itinéraire de rechange
EFFETS_BLOQUANTS = {"NO_SERVICE", "REDUCED_SERVICE", "SIGNIFICANT_DELAYS"}

# Lignes de base du panneau info trafic (comme les écrans en gare), par groupe d'affichage.
LIGNES_BASE = {
    "RER": ["A", "B", "C", "D", "E"],
    "Train": ["H", "J", "K", "L", "N", "P", "R", "U", "V"],
    "Métro": ["1", "2", "3", "3B", "4", "5", "6", "7", "7B", "8", "9", "10", "11", "12", "13", "14"],
    "Tram": ["T1", "T2", "T3A", "T3B", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T11", "T12", "T13", "T14"],
}
