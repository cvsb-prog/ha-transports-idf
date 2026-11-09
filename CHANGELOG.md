# Changelog

Toutes les évolutions notables de Transports IDF. Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
numérotation [SemVer](https://semver.org/lang/fr/).

## [Non publié]

### Ajouté

- Intégration continue GitHub Actions : ruff et tests à chaque push, hassfest et validation HACS.
- hacs.json, préalable à la distribution par HACS.

### Modifié

- manifest.json : documentation et suivi des problèmes sur le dépôt, codeowners.

## [0.10.9] - 2026-11-09

### Ajouté

- Arrêts préférés : clic sur le logo d'une ligne pour afficher son tracé sous l'arrêt.

### Corrigé

- Faux « quota PRIM atteint » au démarrage : requêtes espacées, refus 429 réessayés, message « trop de requêtes (429) ».

## [0.10.8] - 2026-11-09

### Corrigé

- Plan d'une ligne : formes redécoupées aux arrêts, un seul tracé par portion entre deux arrêts voisins (plus de doublons ni de bouts sans arrêt) ; calcul hors de la boucle de Home Assistant.
- Carte d'un trajet : si le tracé PRIM reste douteux, plus court chemin sur le plan de la ligne, d'arrêt desservi en arrêt desservi.

## [0.10.7] - 2026-11-09

*Livraison non fonctionnelle (plans épais et dédoublés, une forme par variante de parcours) : version restée sur `dev`, non publiée sur `master`.*

### Ajouté

- Plan d'une ligne : tracé du jeu de données open data IDFM « Tracés des lignes de transport en commun » (sans clé, seul le numéro de ligne est envoyé) ; sert aussi de secours aux cartes de trajet.

## [0.10.6] - 2026-11-09

*Archive non conservée : son contenu est inclus dans la 0.10.7.*

### Corrigé

- Recalage du tracé d'une section analysé sur de vraies réponses PRIM : entre deux arrêts consécutifs, le plus court morceau du tracé qui les relie, où qu'il soit.
- Constat : PRIM ne fournit aucun tracé de ligne ni de parcours.

## [0.10.5] - 2026-11-09

*Archive non conservée : son contenu est inclus dans la 0.10.7.*

### Corrigé

- Détection des allers-retours d'un tracé par segments (tracés PRIM peu denses).

## [0.10.4] - 2026-11-09

*Archive non conservée : son contenu est inclus dans la 0.10.7.*

### Ajouté

- Plan d'une ligne : tracés des parcours de la ligne quand PRIM ne fournit pas celui de la ligne.

## [0.10.3] - 2026-11-09

*Livraison non fonctionnelle (tracé toujours faux sur une ligne de RER à branches) : version restée sur `dev`, non publiée sur `master`.*

### Corrigé

- Aller-retour sur une autre branche coupé entre deux arrêts ; à défaut, morceau du tracé de la ligne (jamais de ligne droite).

## [0.10.2] - 2026-11-08

*Archive non conservée : son contenu est inclus dans la 0.10.3.*

### Corrigé

- Carte d'un trajet : premier recalage du tracé d'une ligne de RER à branches sur les arrêts desservis (morceaux invraisemblables remplacés par une ligne droite, approche abandonnée).

## [0.10.1] - 2026-11-08

*Livraison non fonctionnelle (archive livrée mal formée : dossier custom_components/ et fichiers annexes à la racine) : version restée sur `dev`, non publiée sur `master`.*

### Ajouté

- Portion interrompue d'une ligne en pointillés gris (plan de ligne et cartes de trajet).

### Modifié

- Fond CARTO Voyager partout, atténué en thème sombre ; cartes sur toute la largeur.
- Lien « itinéraire à pied détaillé » retiré.

### Corrigé

- Info trafic : bus à nom long alignés sur les autres colonnes.

## [0.10.0] - 2026-11-08

### Ajouté

- Sous-entrée « Ligne » (« Ajouter une ligne ») ; les anciennes lignes d'intérêt sont reprises automatiquement.
- « Voir le trajet sur la carte » et « Voir le plan de la ligne » (service plan_ligne).

### Modifié

- Durées d'1 h ou plus affichées « 1 h 06 ».
- Info trafic en tableau aligné ; titre « Arrêts préférés ».
- Clic sur le temps de marche pour la carte (plus de logos 🗺️) ; une seule croix dans la recherche.

## [0.9.5] - 2026-11-07

### Modifié

- Tuiles CARTO à l'adresse documentée (rastertiles), repli automatique sur l'ancienne adresse.

## [0.9.4] - 2026-11-07

### Corrigé

- Tuiles OpenStreetMap refusées (403) : l'adresse de Home Assistant est envoyée (referrerPolicy).
- Éditeur : le capteur Info trafic n'est plus proposé parmi les arrêts.
- Info trafic : messages d'une ligne repliés à l'ouverture.

## [0.9.3] - 2026-11-07

*Livraison non fonctionnelle (tuiles OpenStreetMap refusées (403)) : version restée sur `dev`, non publiée sur `master`.*

### Ajouté

- Clé CARTO facultative dans les réglages ; fond OpenStreetMap par défaut, assombri en thème sombre.
- Itinéraires en temps réel (data_freshness=realtime).

### Modifié

- Éditeur de la carte refait (3 sections, interrupteurs « Masquer … »).
- Carte à pied sur toute la largeur.

### Corrigé

- Messages d'autres lignes desservant les mêmes arrêts écartés.
- Messages « en direction de X » gardés seulement pour une étape vers X.

## [0.9.2] - 2026-11-06

*Livraison non fonctionnelle (cartes toujours sans fond) : version restée sur `dev`, non publiée sur `master`.*

### Ajouté

- Formulaires : pictogrammes [RER] [TRAIN] [M] [TRAM] [BUS] ; « inclure les jours fériés ».
- « Calcul impossible » : cause affichée et journalisée.

### Modifié

- Recherche : un seul sélecteur jour + heure.
- Messages gardés selon le sens de l'étape.

## [0.9.1] - 2026-11-06

*Livraison non fonctionnelle (cartes sans fond (CARTO exige désormais une clé)) : version restée sur `dev`, non publiée sur `master`.*

### Corrigé

- Faux « en retard » le matin : le rappel retient tout départ qui arrive encore à l'heure.

## [0.9.0] - 2026-11-06

*Archive non conservée : son contenu est inclus dans la 0.9.1.*

### Ajouté

- Logo Île-de-France Mobilités (dossier brand/).
- Formulaire de trajet : un seul champ « Départ », « Partir à / Arriver à », option jours fériés, étape « Itinéraire habituel » avec « Autre » pour saisir ses lignes.
- Carte : grille alignée de l'info trafic, carte du trajet à pied avec son tracé (Leaflet embarqué).
- Clic sur un logo de ligne du résumé : message affiché sous l'étape concernée.

### Modifié

- Veille d'un trajet fixée à 60 min.
- Messages filtrés sur la portion empruntée et l'heure du trajet, dédoublonnés ; ascenseurs regroupés.
- Exemples des formulaires et tests sans lien avec un trajet réel.

## [0.8.1] - 2026-11-04

### Corrigé

- Le message d'une ligne perturbée s'affiche sous le résumé, la proposition ou l'étape cliquée, et non partout.

## [0.8.0] - 2026-11-04

### Ajouté

- Jours du trajet : veille, alertes et trajet nominal seulement ces jours-là.
- Détour avant les transports (adresse, mode, temps sur place, heure imposée, jours).
- Trajet nominal du jour, comparé aux itinéraires du moment.

### Modifié

- Itinéraires toujours calculés au départ de maintenant.
- Info trafic en bas de la carte.

## [0.7.1] - 2026-11-04

### Ajouté

- Écart affiché entre propositions (« part 5 min plus tard », « 3 min plus rapide »).

### Modifié

- Pictogramme « temps réel » redessiné.

## [0.7.0] - 2026-11-04

### Modifié

- Itinéraires proposés sans intitulé.
- Rame dessinée sans fond.
- Pictogramme « temps réel » sur les passages.

## [0.6.0] - 2026-11-03

### Ajouté

- Prochains départs d'un trajet.
- Panneau de sortie à l'arrivée.
- Voie et mission des RER.

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
