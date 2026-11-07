# Transports IDF

Intégration Home Assistant pour les transports en commun d'Île-de-France, à partir des API
[PRIM](https://prim.iledefrance-mobilites.fr/) d'Île-de-France Mobilités, avec sa carte de tableau de bord.

Version actuelle : **0.9.5** (voir le [CHANGELOG](CHANGELOG.md)).

## Fonctionnalités

- **Arrêts** : prochains passages en temps réel (SIRI Lite), capteur « prochain départ atteignable » selon le temps de marche et une marge.
- **Trajets** : itinéraire habituel, capteur binaire « perturbation » sur les lignes empruntées.
- Événement `transports_idf_alerte` pour bâtir ses propres alertes de trajet.
- Service **Partir maintenant** : itinéraires d'un trajet au départ de maintenant, marche comprise.
- **Réglages** : zone de départ, marge « en se pressant », rafraîchissement jour / nuit. Lignes d'intérêt pour l'info trafic.
- **Position dans la rame** et **sortie conseillée** (jeux de données RATP / IDFM livrés, mis à jour chaque mois).
- Voie et mission des RER, panneau de sortie à l'arrivée.
- **Trajet nominal** du jour, **jours du trajet** et **détour** avant les transports.
- Messages de perturbation filtrés sur la portion empruntée, le sens et l'heure du trajet ; jours fériés.
- Carte du trajet à pied (Leaflet).

## Carte de tableau de bord

La carte `custom:transports-idf-card` est servie par l'intégration (aucune ressource à ajouter à la main). Elle présente : recherche d'itinéraire, trajets, info trafic, arrêts.
Elle se règle depuis l'éditeur visuel (sections à masquer, arrêts à afficher).

## Installation

Installation manuelle (HACS prévu une fois l'intégration mûre) :

1. Copier `custom_components/transports_idf` dans le dossier `custom_components` de Home Assistant.
2. Redémarrer Home Assistant.
3. Paramètres → Appareils et services → Ajouter une intégration → **Transports IDF**, puis coller un jeton PRIM
   (compte gratuit sur prim.iledefrance-mobilites.fr ; le jeton n'est affiché qu'à sa création).
4. Ajouter des arrêts et des trajets depuis la page de l'intégration.

Mise à jour : remplacer le dossier, redémarrer, actualiser le navigateur. Réglages, arrêts et trajets sont conservés.

### Fond des cartes

Sans réglage, les cartes utilisent OpenStreetMap. Avec une clé gratuite CARTO (Configurer → Réglages), elles utilisent les fonds CARTO clair et sombre.

## Données envoyées

- PRIM : identifiants d'arrêts et de lignes, et pour les itinéraires les positions de départ et d'arrivée.
- Fond de carte (OpenStreetMap ou CARTO) : les tuiles de la zone affichée.

## Données et licences tierces

- RATP « Positionnement dans la rame » et IDFM « Référentiel des arrêts : Accès » : jeux de données ouverts, copie livrée dans `donnees/`.
- [Leaflet](https://leafletjs.com/) 1.9.4 (BSD-2-Clause), embarqué dans `www/leaflet/`.
- Logo Île-de-France Mobilités dans `brand/`.
- Données temps réel et calcul d'itinéraire : PRIM, Île-de-France Mobilités.

## Licence

MIT, voir [LICENSE](LICENSE).
