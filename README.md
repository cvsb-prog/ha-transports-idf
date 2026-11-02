# Transports IDF

Intégration Home Assistant pour les transports en commun d'Île-de-France, à partir des API
[PRIM](https://prim.iledefrance-mobilites.fr/) d'Île-de-France Mobilités, avec sa carte de tableau de bord.

Version actuelle : **0.2.0** (voir le [CHANGELOG](CHANGELOG.md)).

## Fonctionnalités

- **Arrêts** : prochains passages en temps réel (SIRI Lite), capteur « prochain départ atteignable » selon le temps de marche et une marge.
- **Trajets** : itinéraire habituel, capteur binaire « perturbation » sur les lignes empruntées.
- Événement `transports_idf_alerte` pour bâtir ses propres alertes de trajet.

## Carte de tableau de bord

La carte `custom:transports-idf-card` est servie par l'intégration (aucune ressource à ajouter à la main). Elle présente : prochains passages et trajets.

## Installation

Installation manuelle (HACS prévu une fois l'intégration mûre) :

1. Copier `custom_components/transports_idf` dans le dossier `custom_components` de Home Assistant.
2. Redémarrer Home Assistant.
3. Paramètres → Appareils et services → Ajouter une intégration → **Transports IDF**, puis coller un jeton PRIM
   (compte gratuit sur prim.iledefrance-mobilites.fr ; le jeton n'est affiché qu'à sa création).
4. Ajouter des arrêts et des trajets depuis la page de l'intégration.

Mise à jour : remplacer le dossier, redémarrer, actualiser le navigateur. Réglages, arrêts et trajets sont conservés.

## Données envoyées

- PRIM : identifiants d'arrêts et de lignes, et pour les itinéraires les positions de départ et d'arrivée.

## Données et licences tierces

- Données temps réel et calcul d'itinéraire : PRIM, Île-de-France Mobilités.

## Licence

MIT, voir [LICENSE](LICENSE).
