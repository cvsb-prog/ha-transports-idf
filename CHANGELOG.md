# Changelog

Toutes les évolutions notables de Transports IDF. Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
numérotation [SemVer](https://semver.org/lang/fr/).

## [0.3.0] - 2026-11-02

### Ajouté

- Service partir_maintenant : itinéraires d'un trajet au départ de maintenant, marche comprise.
- Carte : itinéraires proposés sous chaque trajet.

## [0.2.0] - 2026-11-02

### Ajouté

- Configuration par l'interface (jeton PRIM, zone de départ), sous-entrées « Arrêt » et « Trajet ».
- Capteur « Prochain départ atteignable » par arrêt (passages SIRI Lite, temps de marche, marge).
- Capteur binaire « Perturbation » par trajet (perturbations des lignes de l'itinéraire).
- Carte de tableau de bord : prochains passages et trajets.

## [0.1.x] - 2026-11-02

*Archive non conservée : son contenu est inclus dans la 0.2.0.*

### Ajouté

- Première intégration Home Assistant pour PRIM (Île-de-France Mobilités), configurée par l'interface avec un jeton PRIM.
- Sous-entrées « Arrêt » (prochains passages, SIRI Lite) et « Trajet » (itinéraire Navitia, perturbations).
- Carte de tableau de bord Transports IDF.
