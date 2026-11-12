# Transports IDF

Intégration Home Assistant pour les transports en commun d'Île-de-France, à partir des API
[PRIM](https://prim.iledefrance-mobilites.fr/) d'Île-de-France Mobilités, avec sa carte de tableau de bord.

Version actuelle : **1.1.0b1** (pré-version de test ; version stable : 1.0.0) (voir le [CHANGELOG](CHANGELOG.md)).

## Fonctionnalités

- **Arrêts** : prochains passages en temps réel (SIRI Lite), capteur « prochain départ atteignable » selon le temps de marche et une marge.
- **Trajets** : itinéraire habituel, capteur binaire « perturbation » sur les lignes empruntées.
- Événement `transports_idf_alerte` pour bâtir ses propres alertes de trajet (automatisation d'exemple dans `examples/`).
- Service **Partir maintenant** : itinéraires d'un trajet au départ de maintenant, marche comprise.
- **Réglages** : zone de départ, marge « en se pressant », rafraîchissement jour / nuit.
- **Lignes d'intérêt** : sous-entrées « Ligne », suivies dans l'info trafic.
- **Position dans la rame** et **sortie conseillée** (jeux de données RATP / IDFM livrés, mis à jour chaque mois).
- Voie et mission des RER, panneau de sortie à l'arrivée.
- **Trajet nominal** du jour, **jours du trajet** et **détour** avant les transports.
- Messages de perturbation filtrés sur la portion empruntée, le sens et l'heure du trajet ; jours fériés.
- Cartes : trajet à pied, trajet complet, plan d'une ligne (tracé open data IDFM) ; portions interrompues en pointillés gris.

## Carte de tableau de bord

La carte `custom:transports-idf-card` est servie par l'intégration (aucune ressource à ajouter à la main). Elle présente : recherche d'itinéraire, trajets, info trafic, arrêts.
Elle se règle depuis l'éditeur visuel (sections à masquer, arrêts à afficher).

## Installation

### Par HACS (conseillé)

1. HACS → ⋮ → **Dépôts personnalisés** → `https://github.com/cvsb-prog/ha-transports-idf`, type **Intégration**.
2. Rechercher **Transports IDF** dans HACS → **Télécharger**, puis redémarrer Home Assistant.
3. Paramètres → Appareils et services → Ajouter une intégration → **Transports IDF**, puis coller un jeton PRIM
   (compte gratuit sur prim.iledefrance-mobilites.fr ; le jeton n'est affiché qu'à sa création).
4. Ajouter des arrêts, des trajets et des lignes depuis la page de l'intégration.

Les mises à jour arrivent par HACS. Après une mise à jour, actualiser le navigateur (dans l'application mobile : vider le cache).

### À la main

Copier `custom_components/transports_idf` dans le dossier `custom_components` de Home Assistant, redémarrer, puis suivre les
étapes 3 et 4. Mise à jour : remplacer le dossier, redémarrer, actualiser le navigateur.

Dans les deux cas, réglages, arrêts, trajets et lignes sont conservés : ils sont enregistrés par Home Assistant, pas dans le dossier.

### Fond des cartes

Sans réglage, les cartes utilisent OpenStreetMap. Avec une clé gratuite CARTO (Configurer → Réglages), elles utilisent CARTO Voyager, légèrement atténué en thème sombre.

## Données envoyées

- PRIM : identifiants d'arrêts et de lignes, et pour les itinéraires les positions de départ et d'arrivée.
- Fond de carte (OpenStreetMap ou CARTO) : les tuiles de la zone affichée.
- Open data IDFM : le numéro de la ligne dont on affiche le plan.

## Données et licences tierces

- RATP « Positionnement dans la rame » et IDFM « Référentiel des arrêts : Accès » : jeux de données ouverts, copie livrée dans `donnees/`.
- [Leaflet](https://leafletjs.com/) 1.9.4 (BSD-2-Clause), embarqué dans `www/leaflet/`.
- Logo Île-de-France Mobilités dans `brand/`.
- IDFM « Tracés des lignes de transport en commun » (open data), interrogé à la demande.
- Données temps réel et calcul d'itinéraire : PRIM, Île-de-France Mobilités.

## Signaler un problème

Ouvrir une [issue](https://github.com/cvsb-prog/ha-transports-idf/issues/new/choose) avec la version, le journal en mode debug
(page de l'intégration → « Activer la journalisation de débogage ») et une capture si la carte est en cause.

## Développement

```bash
pip install -r requirements_test.txt
pytest
ruff check --select F,E9 custom_components tests
```

L'intégration continue (GitHub Actions) lance les tests, ruff, hassfest et la validation HACS à chaque push et pull request.

Branches : `master` (versions publiées), `dev` (développement) ; une branche `feature/…` ou `fix/…` par chantier,
rebasée sur `dev` puis fusionnée sans avance rapide (`--no-ff`).

## Licence

MIT, voir [LICENSE](LICENSE).
