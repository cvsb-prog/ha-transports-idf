# Changelog

Toutes les évolutions notables de Transports IDF. Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
numérotation [SemVer](https://semver.org/lang/fr/).

## [0.5.1] - 2026-11-03

### Ajouté

- Sortie conseillée (numéro et nom) d'après le référentiel IDFM des accès : la plus proche de la fin du trajet à pied.
- Mise à jour mensuelle des deux jeux de données (la copie livrée sert de secours).

## [0.5.0] - 2026-11-03

### Ajouté

- Position conseillée dans la rame (voiture, avant / milieu / arrière) pour chaque correspondance, d'après le jeu de données RATP « Positionnement dans la rame », livré avec l'intégration.

## [0.4.0] - 2026-11-03

### Ajouté

- Options : zone de départ, marge « en se pressant », nombre de passages, rafraîchissement jour / nuit, messages d'information ignorés.
- Lignes d'intérêt suivies dans l'info trafic.
- Carte en 4 sections repliables (recherche, trajets, info trafic, arrêts).

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
