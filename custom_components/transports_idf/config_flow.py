"""Configuration : jeton PRIM, options, et sous-entrées « arrêt » et « trajet »."""

from __future__ import annotations

import asyncio
import math
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)
from homeassistant.util import dt as dt_util

from .api import (
    ClientPrim,
    ErreurJeton,
    ErreurPrim,
    date_navitia,
    estimation_marche,
    prochain_jour_ouvre,
    refs_candidates,
)
from .const import (
    A_COMMUNE,
    A_LAT,
    A_LIGNES,
    A_LIGNES_SUIVIES,
    A_LON,
    A_MARCHE,
    A_MARCHE_CALCULE,
    A_NOM,
    A_REFS,
    A_ZONE_ID,
    CONF_JETON,
    CONF_ZONE,
    DEFAUTS,
    DOMAIN,
    JOURS_DEFAUT,
    MODES_DETOUR,
    NOM,
    OPT_DEBUT_NUIT,
    OPT_FIN_NUIT,
    OPT_IGNORER_INFO,
    OPT_INTERVALLE_JOUR,
    OPT_INTERVALLE_NUIT,
    OPT_LIGNES_INTERET,
    OPT_MARGE,
    OPT_NB_PASSAGES,
    SOUS_ARRET,
    SOUS_TRAJET,
    T_ARRIVEE_HABITUELLE,
    T_AVANCE,
    T_DEPART_HABITUEL,
    T_DEST_ID,
    T_DEST_NOM,
    T_DET_DUREE,
    T_DET_HEURE,
    T_DET_ID,
    T_DET_JOURS,
    T_DET_MODE,
    T_DET_NOM,
    T_DET_PAUSE,
    T_DUREE,
    T_ETAPES,
    T_HEURE,
    T_JOURS,
    T_NOM,
    T_ORIGINE,
    T_ORIGINE_ID,
    T_ORIGINE_NOM,
    T_PERSONNE,
    T_RESUME,
    T_SECTIONS,
    T_TYPE_HEURE,
)
from .coordinator import coordonnees_zone

ZONE_SELECTEUR = EntitySelector(EntitySelectorConfig(domain="zone"))
TYPES_DESTINATION = ["stop_area", "address", "poi", "administrative_region"]


def _nombre(mini: float, maxi: float, pas: float = 1, unite: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=mini, max=maxi, step=pas, mode=NumberSelectorMode.BOX)
    if unite:
        config["unit_of_measurement"] = unite
    return NumberSelector(config)


def _liste(options: list[SelectOptionDict], multiple: bool = False) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(options=options, multiple=multiple, mode=SelectSelectorMode.LIST if multiple else SelectSelectorMode.DROPDOWN)
    )


def _heure_locale(x: Any) -> str | None:
    d = date_navitia(x)
    return d.strftime("%H:%M:%S") if d else None


def _libelle_ligne(l: dict[str, Any]) -> str:
    texte = f"{l.get('mode') or ''} {l.get('nom') or ''}".strip()
    if l.get("nom_long") and l["nom_long"] != l.get("nom"):
        texte += f" — {l['nom_long']}"
    return texte


def _titre_arret(l: dict[str, Any]) -> str:
    nom, commune = l.get("nom") or l["id"], l.get("commune")
    return f"{nom} ({commune})" if commune and commune.lower() not in nom.lower() else nom


def _libelle_lieu(l: dict[str, Any]) -> str:
    texte = l.get("libelle") or l.get("nom") or l["id"]
    if l.get("commune") and l["commune"] not in texte:
        texte += f" ({l['commune']})"
    return texte


# ---------------------------------------------------------------------------
class TransportsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _verifier(self, jeton: str) -> str | None:
        client = ClientPrim(async_get_clientsession(self.hass), jeton)
        try:
            await client.lieux("Châtelet", ["stop_area"])
        except ErreurJeton:
            return "jeton_invalide"
        except ErreurPrim:
            return "injoignable"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        erreurs: dict[str, str] = {}
        if user_input is not None:
            if erreur := await self._verifier(user_input[CONF_JETON].strip()):
                erreurs["base"] = erreur
            else:
                return self.async_create_entry(
                    title=NOM, data={CONF_JETON: user_input[CONF_JETON].strip(), CONF_ZONE: user_input[CONF_ZONE]}
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_JETON): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Required(CONF_ZONE, default="zone.home"): ZONE_SELECTEUR,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=erreurs)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        erreurs: dict[str, str] = {}
        if user_input is not None:
            if erreur := await self._verifier(user_input[CONF_JETON].strip()):
                erreurs["base"] = erreur
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_JETON: user_input[CONF_JETON].strip()}
                )
        schema = vol.Schema({vol.Required(CONF_JETON): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))})
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=erreurs)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TransportsOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SOUS_ARRET: ArretFlow, SOUS_TRAJET: TrajetFlow}


# ---------------------------------------------------------------------------
class TransportsOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._trouvees: list[dict[str, Any]] = []
        self._gardees: list[dict[str, Any]] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["reglages", "lignes"])

    def _enregistrer(self, maj: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **maj})

    async def async_step_reglages(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            entree = self.config_entry
            if CONF_ZONE in user_input:
                self.hass.config_entries.async_update_entry(entree, data={**entree.data, CONF_ZONE: user_input.pop(CONF_ZONE)})
            return self._enregistrer(user_input)
        o = {**DEFAUTS, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(CONF_ZONE, default=self.config_entry.data.get(CONF_ZONE, "zone.home")): ZONE_SELECTEUR,
                vol.Required(OPT_MARGE, default=o[OPT_MARGE]): _nombre(0, 10, 1, "min"),
                vol.Required(OPT_NB_PASSAGES, default=o[OPT_NB_PASSAGES]): _nombre(1, 30),
                vol.Required(OPT_INTERVALLE_JOUR, default=o[OPT_INTERVALLE_JOUR]): _nombre(15, 600, 5, "s"),
                vol.Required(OPT_INTERVALLE_NUIT, default=o[OPT_INTERVALLE_NUIT]): _nombre(60, 3600, 30, "s"),
                vol.Required(OPT_DEBUT_NUIT, default=o[OPT_DEBUT_NUIT]): TimeSelector(),
                vol.Required(OPT_FIN_NUIT, default=o[OPT_FIN_NUIT]): TimeSelector(),
                vol.Required(OPT_IGNORER_INFO, default=o[OPT_IGNORER_INFO]): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="reglages", data_schema=schema)

    async def async_step_lignes(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Lignes d'intérêt ajoutées au panneau info trafic (en plus des lignes de base)."""
        actuelles = list(self.config_entry.options.get(OPT_LIGNES_INTERET) or [])
        erreurs: dict[str, str] = {}
        if user_input is not None:
            gardees = [l for l in actuelles if l["id"] in set(user_input.get("garder", []))]
            texte = (user_input.get("recherche") or "").strip()
            if not texte:
                return self._enregistrer({OPT_LIGNES_INTERET: gardees})
            client = self.config_entry.runtime_data.client
            try:
                self._trouvees = await client.chercher_lignes(texte)
            except ErreurPrim:
                erreurs["base"] = "injoignable"
            else:
                if self._trouvees:
                    self._gardees = gardees
                    return await self.async_step_lignes_choix()
                erreurs["base"] = "aucune_ligne"
        schema = vol.Schema(
            {
                vol.Optional("garder", default=[l["id"] for l in actuelles]): _liste(
                    [SelectOptionDict(value=l["id"], label=_libelle_ligne(l)) for l in actuelles], True
                ),
                vol.Optional("recherche"): TextSelector(),
            }
        )
        return self.async_show_form(step_id="lignes", data_schema=schema, errors=erreurs)

    async def async_step_lignes_choix(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            choisies = [
                {k: l.get(k) for k in ("id", "code", "nom", "nom_long", "mode", "couleur", "couleur_texte")}
                for l in self._trouvees
                if l["id"] in set(user_input.get("lignes", []))
            ]
            deja = {l["id"] for l in self._gardees}
            return self._enregistrer({OPT_LIGNES_INTERET: self._gardees + [l for l in choisies if l["id"] not in deja]})
        options = [SelectOptionDict(value=l["id"], label=_libelle_ligne(l)) for l in self._trouvees]
        return self.async_show_form(step_id="lignes_choix", data_schema=vol.Schema({vol.Optional("lignes", default=[]): _liste(options, True)}))


# ---------------------------------------------------------------------------
class _Base(ConfigSubentryFlow):
    @property
    def client(self) -> ClientPrim:
        entree = self._get_entry()
        donnees = getattr(entree, "runtime_data", None)
        if donnees is not None:
            return donnees.client
        return ClientPrim(async_get_clientsession(self.hass), entree.data[CONF_JETON])

    async def _chercher(self, texte: str, types: list[str]) -> tuple[list[dict[str, Any]], str | None]:
        try:
            lieux = await self.client.lieux(texte, types)
        except ErreurJeton:
            return [], "jeton_invalide"
        except ErreurPrim:
            return [], "injoignable"
        return lieux, (None if lieux else "aucun_resultat")


class ArretFlow(_Base):
    """Ajouter / modifier un arrêt suivi."""

    def __init__(self) -> None:
        self._lieux: list[dict[str, Any]] = []
        self._zone: dict[str, Any] = {}
        self._lignes: list[dict[str, Any]] = []
        self._refs: list[str] = []
        self._vues: set[str] = set()
        self._suivies: list[str] = []
        self._marche_calcule: int | None = None
        self._marche_estime: int = 0

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        erreurs: dict[str, str] = {}
        if user_input is not None:
            self._lieux, erreur = await self._chercher(user_input["recherche"], ["stop_area"])
            if erreur:
                erreurs["base"] = erreur
            else:
                return await self.async_step_choix()
        schema = vol.Schema({vol.Required("recherche"): TextSelector()})
        return self.async_show_form(step_id="user", data_schema=schema, errors=erreurs)

    async def async_step_choix(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._zone = next(l for l in self._lieux if l["id"] == user_input["arret"])
            deja = {s.data.get(A_ZONE_ID) for s in self._get_entry().subentries.values() if s.subentry_type == SOUS_ARRET}
            if self._zone["id"] in deja:
                return self.async_abort(reason="deja_suivi")
            if (erreur := await self._preparer()) is not None:
                return self.async_abort(reason=erreur)
            return await self.async_step_lignes()
        options = [SelectOptionDict(value=l["id"], label=_libelle_lieu(l)) for l in self._lieux]
        return self.async_show_form(step_id="choix", data_schema=vol.Schema({vol.Required("arret"): _liste(options)}))

    async def _preparer(self) -> str | None:
        """Lignes de la zone, références temps réel qui répondent, temps de marche."""
        client, zid = self.client, self._zone["id"]
        try:
            self._lignes, points = await asyncio.gather(client.lignes_zone(zid), client.points_zone(zid))
        except ErreurJeton:
            return "jeton_invalide"
        except ErreurPrim:
            return "injoignable"
        candidates = refs_candidates(zid, points)
        reponses = await asyncio.gather(*(client.passages(r) for r in candidates), return_exceptions=True)
        self._refs = []
        for ref, rep in zip(candidates, reponses, strict=True):
            if isinstance(rep, list) and rep:
                self._refs.append(ref)
                self._vues |= {p["ligne"] for p in rep if p["ligne"]}
        # La zone entière couvre souvent tous ses quais : inutile de garder les quais en double.
        zone_ref = candidates[0] if candidates else None
        if zone_ref in self._refs:
            lignes_zone = {p["ligne"] for rep, ref in zip(reponses, candidates, strict=True) if ref == zone_ref and isinstance(rep, list) for p in rep}
            if self._vues <= lignes_zone:
                self._refs = [zone_ref]
        if not self._refs:  # rien en ce moment (la nuit…) : on garde tout, on filtrera à l'usage
            self._refs = candidates
        await self._calculer_marche()
        return None

    async def _calculer_marche(self) -> None:
        lat, lon = coordonnees_zone(self.hass, self._get_entry().data.get(CONF_ZONE))
        zlat = self._zone.get(A_LAT) if A_LAT in self._zone else self._zone.get("lat")
        zlon = self._zone.get(A_LON) if A_LON in self._zone else self._zone.get("lon")
        self._marche_estime = estimation_marche(lat, lon, zlat, zlon) if zlat is not None and zlon is not None else 600
        try:
            self._marche_calcule = await self.client.temps_marche(lat, lon, self._zone["id"])
        except ErreurPrim:
            self._marche_calcule = None

    def _options_lignes(self) -> list[SelectOptionDict]:
        return [
            SelectOptionDict(value=l["id"], label=_libelle_ligne(l) + ("" if l["code"] in self._vues or not self._vues else " (pas de passage en ce moment)"))
            for l in self._lignes
        ]

    async def async_step_lignes(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._suivies = user_input.get("lignes", [])
            return await self.async_step_marche()
        schema = vol.Schema({vol.Optional("lignes", default=[l["id"] for l in self._lignes]): _liste(self._options_lignes(), True)})
        return self.async_show_form(
            step_id="lignes", data_schema=schema, description_placeholders={"arret": _libelle_lieu(self._zone)}
        )

    def _minutes_proposees(self) -> int:
        secondes = self._marche_calcule if self._marche_calcule is not None else self._marche_estime
        return max(1, math.ceil(secondes / 60))

    def _texte_marche(self) -> str:
        if self._marche_calcule is not None:
            return f"{self._minutes_proposees()} min, calculé par le calculateur d'itinéraires PRIM"
        return f"{self._minutes_proposees()} min, estimé à vol d'oiseau (calculateur sans réponse)"

    async def async_step_marche(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            z = self._zone
            return self.async_create_entry(
                title=_titre_arret(z),
                data={
                    A_ZONE_ID: z["id"],
                    A_NOM: z.get("nom"),
                    A_COMMUNE: z.get("commune"),
                    A_LAT: z.get("lat"),
                    A_LON: z.get("lon"),
                    A_REFS: self._refs,
                    A_LIGNES: self._lignes,
                    A_LIGNES_SUIVIES: self._suivies,
                    A_MARCHE: user_input[A_MARCHE],
                    A_MARCHE_CALCULE: None if self._marche_calcule is None else round(self._marche_calcule / 60, 1),
                },
                unique_id=z["id"],
            )
        schema = vol.Schema({vol.Required(A_MARCHE, default=self._minutes_proposees()): _nombre(0, 60, 0.5, "min")})
        return self.async_show_form(step_id="marche", data_schema=schema, description_placeholders={"calcul": self._texte_marche()})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sous = self._get_reconfigure_subentry()
        d = sous.data
        if user_input is not None:
            maj = {A_LIGNES_SUIVIES: user_input.get("lignes", []), A_MARCHE: user_input[A_MARCHE]}
            if user_input.get("recalculer"):
                self._zone = {"id": d[A_ZONE_ID], "lat": d.get(A_LAT), "lon": d.get(A_LON)}
                await self._calculer_marche()
                maj[A_MARCHE] = self._minutes_proposees()
                maj[A_MARCHE_CALCULE] = None if self._marche_calcule is None else round(self._marche_calcule / 60, 1)
            return self.async_update_and_abort(self._get_entry(), sous, data_updates=maj)
        self._lignes = d.get(A_LIGNES, [])
        schema = vol.Schema(
            {
                vol.Optional("lignes", default=d.get(A_LIGNES_SUIVIES) or [l["id"] for l in self._lignes]): _liste(self._options_lignes(), True),
                vol.Required(A_MARCHE, default=d.get(A_MARCHE, 5)): _nombre(0, 60, 0.5, "min"),
                vol.Optional("recalculer", default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, description_placeholders={"arret": sous.title})


# ---------------------------------------------------------------------------
JOURS = [SelectOptionDict(value=str(n), label=j) for n, j in enumerate(["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"])]
MODES = [
    SelectOptionDict(value="marche", label="À pied"),
    SelectOptionDict(value="velo", label="À vélo"),
    SelectOptionDict(value="voiture", label="En voiture"),
    SelectOptionDict(value="transports", label="En transports"),
]
VITESSES = {"marche": 1.2, "velo": 4.2, "voiture": 6.0}  # m/s, estimation de secours (ville)


def _jours(defaut: list[str]) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=JOURS, multiple=True, mode=SelectSelectorMode.LIST))


class TrajetFlow(_Base):
    """Ajouter / modifier un trajet habituel, propre à une personne."""

    def __init__(self) -> None:
        self._saisie: dict[str, Any] = {}
        self._lieux: list[dict[str, Any]] = []
        self._dest: dict[str, Any] = {}
        self._itineraires: list[dict[str, Any]] = []
        self._origine: dict[str, Any] | None = None
        self._lieux_depart: list[dict[str, Any]] = []
        self._lieux_detour: list[dict[str, Any]] = []
        self._detour: dict[str, Any] = {}  # réglages du détour en cours de saisie
        self._lieu_detour: dict[str, Any] | None = None

    def _schema_trajet(self, d: dict[str, Any], avec_destination: bool) -> vol.Schema:
        champs: dict[Any, Any] = {
            vol.Required(T_NOM, default=d.get(T_NOM, "")): TextSelector(),
            vol.Required(T_PERSONNE, default=d.get(T_PERSONNE)) if d.get(T_PERSONNE) else vol.Required(T_PERSONNE): EntitySelector(EntitySelectorConfig(domain="person")),
            vol.Required(T_ORIGINE, default=d.get(T_ORIGINE) or self._get_entry().data.get(CONF_ZONE, "zone.home")): ZONE_SELECTEUR,
        }
        if avec_destination:
            champs[vol.Optional("recherche_depart")] = TextSelector()
            champs[vol.Required("recherche")] = TextSelector()
        champs[vol.Required(T_TYPE_HEURE, default=d.get(T_TYPE_HEURE, "arrivee"))] = SelectSelector(
            SelectSelectorConfig(
                options=[SelectOptionDict(value="arrivee", label="Heure d'arrivée cible"), SelectOptionDict(value="depart", label="Heure de départ")],
                mode=SelectSelectorMode.LIST,
            )
        )
        champs[vol.Required(T_HEURE, default=d.get(T_HEURE, "09:00:00"))] = TimeSelector()
        champs[vol.Required(T_JOURS, default=d.get(T_JOURS) or JOURS_DEFAUT)] = _jours(JOURS_DEFAUT)
        champs[vol.Required(T_AVANCE, default=d.get(T_AVANCE, 60))] = _nombre(10, 240, 5, "min")
        if not d.get(T_DET_ID):
            champs[vol.Optional("recherche_detour")] = TextSelector()
        else:
            champs[vol.Required(T_DET_MODE, default=d.get(T_DET_MODE, "marche"))] = _liste(MODES)
            champs[vol.Required(T_DET_DUREE, default=d.get(T_DET_DUREE, 10))] = _nombre(0, 120, 1, "min")
            champs[vol.Required(T_DET_PAUSE, default=d.get(T_DET_PAUSE, 5))] = _nombre(0, 120, 1, "min")
            champs[vol.Optional(T_DET_HEURE, description={"suggested_value": d.get(T_DET_HEURE)})] = TimeSelector()
            champs[vol.Required(T_DET_JOURS, default=d.get(T_DET_JOURS) or JOURS_DEFAUT)] = _jours(JOURS_DEFAUT)
            champs[vol.Optional("supprimer_detour", default=False)] = BooleanSelector()
        if not avec_destination:
            champs[vol.Optional("recalculer", default=False)] = BooleanSelector()
        return vol.Schema(champs)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        erreurs: dict[str, str] = {}
        if user_input is not None:
            self._saisie = dict(user_input)
            self._origine = None
            texte_depart = (user_input.get("recherche_depart") or "").strip()
            if texte_depart:
                self._lieux_depart, erreur = await self._chercher(texte_depart, TYPES_DESTINATION)
                if erreur:
                    erreurs["recherche_depart"] = erreur
            if not erreurs:
                self._lieux, erreur = await self._chercher(user_input["recherche"], TYPES_DESTINATION)
                if erreur:
                    erreurs["recherche"] = erreur
            texte_detour = (user_input.get("recherche_detour") or "").strip()
            if not erreurs and texte_detour:
                self._lieux_detour, erreur = await self._chercher(texte_detour, TYPES_DESTINATION)
                if erreur:
                    erreurs["recherche_detour"] = erreur
            if not erreurs:
                return await (self.async_step_depart() if texte_depart else self.async_step_destination())
        return self.async_show_form(step_id="user", data_schema=self._schema_trajet(user_input or {}, True), errors=erreurs)

    async def async_step_depart(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._origine = next(l for l in self._lieux_depart if l["id"] == user_input["depart"])
            return await self.async_step_destination()
        options = [SelectOptionDict(value=l["id"], label=_libelle_lieu(l)) for l in self._lieux_depart]
        return self.async_show_form(step_id="depart", data_schema=vol.Schema({vol.Required("depart"): _liste(options)}))

    def _depuis(self) -> str:
        if self._origine:
            return self._origine["id"]
        lat, lon = coordonnees_zone(self.hass, self._saisie[T_ORIGINE])
        return f"{lon};{lat}"

    async def async_step_destination(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._dest = next(l for l in self._lieux if l["id"] == user_input["destination"])
            return await (self.async_step_detour() if self._lieux_detour else self.async_step_itineraire())
        options = [SelectOptionDict(value=l["id"], label=_libelle_lieu(l)) for l in self._lieux]
        return self.async_show_form(step_id="destination", data_schema=vol.Schema({vol.Required("destination"): _liste(options)}))

    async def async_step_detour(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Détour avant les transports : lieu et façon d'y aller."""
        if user_input is not None:
            self._lieu_detour = next(l for l in self._lieux_detour if l["id"] == user_input["lieu"])
            self._detour = {T_DET_ID: self._lieu_detour["id"], T_DET_NOM: _libelle_lieu(self._lieu_detour), T_DET_MODE: user_input[T_DET_MODE]}
            return await self.async_step_detour_reglages()
        options = [SelectOptionDict(value=l["id"], label=_libelle_lieu(l)) for l in self._lieux_detour]
        schema = vol.Schema({vol.Required("lieu"): _liste(options), vol.Required(T_DET_MODE, default="marche"): _liste(MODES)})
        return self.async_show_form(step_id="detour", data_schema=schema)

    async def _duree_detour(self) -> tuple[int, bool]:
        """Minutes pour aller de la maison au détour (calculées si possible), et si c'est un calcul PRIM."""
        mode = self._detour[T_DET_MODE]
        if mode == "transports":
            return 0, False
        try:
            secondes = await self.client.temps_direct(self._depuis_maison(), self._detour[T_DET_ID], MODES_DETOUR[mode])
        except ErreurPrim:
            secondes = None
        if secondes:
            return max(1, math.ceil(secondes / 60)), True
        lat, lon = self._coord_maison()
        l = self._lieu_detour or {}
        if l.get("lat") is None:
            return 10, False
        metres = estimation_marche(lat, lon, l["lat"], l["lon"]) * 1.2  # estimation_marche = secondes à 1,2 m/s
        return max(1, math.ceil(metres / VITESSES[mode] / 60)), False

    async def async_step_detour_reglages(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._detour.update(user_input)
            self._detour.setdefault(T_DET_DUREE, 0)
            return await self.async_step_itineraire()
        duree, calcule = await self._duree_detour()
        champs: dict[Any, Any] = {}
        if self._detour[T_DET_MODE] != "transports":
            champs[vol.Required(T_DET_DUREE, default=duree)] = _nombre(0, 120, 1, "min")
        champs[vol.Required(T_DET_PAUSE, default=5)] = _nombre(0, 120, 1, "min")
        champs[vol.Optional(T_DET_HEURE)] = TimeSelector()
        champs[vol.Required(T_DET_JOURS, default=self._saisie.get(T_JOURS) or JOURS_DEFAUT)] = _jours(JOURS_DEFAUT)
        texte = "en transports : calculé en direct à chaque fois" if self._detour[T_DET_MODE] == "transports" else (
            f"{duree} min, {'calculé par PRIM' if calcule else 'estimé à vol d’oiseau'}"
        )
        return self.async_show_form(
            step_id="detour_reglages", data_schema=vol.Schema(champs),
            description_placeholders={"lieu": self._detour[T_DET_NOM], "calcul": texte},
        )

    def _coord_maison(self) -> tuple[float, float]:
        if self._origine and self._origine.get("lat") is not None:
            return self._origine["lat"], self._origine["lon"]
        return coordonnees_zone(self.hass, self._saisie[T_ORIGINE])

    def _depuis_maison(self) -> str:
        if self._origine:
            return self._origine["id"]
        lat, lon = coordonnees_zone(self.hass, self._saisie[T_ORIGINE])
        return f"{lon};{lat}"

    async def _calculer(self) -> str | None:
        quand = prochain_jour_ouvre(dt_util.now().replace(tzinfo=None), self._saisie[T_HEURE])
        represente = "arrival" if self._saisie.get(T_TYPE_HEURE) == "arrivee" else "departure"
        depuis = self._depuis()
        if self._detour.get(T_DET_ID):
            # Les transports partent du détour : à l'heure imposée + temps sur place si elle existe.
            depuis = self._detour[T_DET_ID]
            if self._detour.get(T_DET_HEURE):
                h, m, *_ = (int(x) for x in self._detour[T_DET_HEURE].split(":"))
                quand = quand.replace(hour=h, minute=m) + timedelta(minutes=float(self._detour.get(T_DET_PAUSE) or 0))
                represente = "departure"
        try:
            self._itineraires = await self.client.itineraires(depuis, self._dest["id"], quand, represente=represente)
        except ErreurJeton:
            return "jeton_invalide"
        except ErreurPrim:
            return "injoignable"
        self._itineraires = [i for i in self._itineraires if i["sections"]]
        return None if self._itineraires else "aucun_itineraire"

    def _options_itineraires(self) -> list[SelectOptionDict]:
        sortie = []
        for n, i in enumerate(self._itineraires):
            horaires = f"{i['depart_h']} → {i['arrivee_h']} · " if i.get("depart_h") and i.get("arrivee_h") else ""
            sortie.append(SelectOptionDict(value=str(n), label=horaires + i["resume"]))
        return sortie

    async def async_step_itineraire(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            choix = self._itineraires[int(user_input["itineraire"])]
            donnees = {
                T_NOM: self._saisie[T_NOM],
                T_PERSONNE: self._saisie[T_PERSONNE],
                T_ORIGINE: self._saisie[T_ORIGINE],
                T_ORIGINE_ID: self._origine["id"] if self._origine else None,
                T_ORIGINE_NOM: _libelle_lieu(self._origine) if self._origine else None,
                T_ETAPES: [{k: v for k, v in e.items() if k not in ("depart", "arrivee", "depart_brut", "suivants", "rame", "alerte")} for e in choix.get("etapes", [])],
                T_HEURE: self._saisie[T_HEURE],
                T_TYPE_HEURE: self._saisie.get(T_TYPE_HEURE, "depart"),
                T_DEPART_HABITUEL: self._depart_maison(choix),
                T_JOURS: self._saisie.get(T_JOURS) or JOURS_DEFAUT,
                **({k: v for k, v in self._detour.items()} if self._detour.get(T_DET_ID) else {}),
                T_ARRIVEE_HABITUELLE: _heure_locale(choix.get("arrivee")),
                T_DUREE: choix.get("duree"),
                T_AVANCE: self._saisie[T_AVANCE],
                T_DEST_ID: self._dest["id"],
                T_DEST_NOM: _libelle_lieu(self._dest),
                T_SECTIONS: choix["sections"],
                T_RESUME: choix["resume"],
            }
            if self.source == "reconfigure":
                sous = self._get_reconfigure_subentry()
                return self.async_update_and_abort(self._get_entry(), sous, title=self._titre(donnees), data=donnees)
            return self.async_create_entry(title=self._titre(donnees), data=donnees)
        if (erreur := await self._calculer()) is not None:
            return self.async_abort(reason=erreur)
        schema = vol.Schema({vol.Required("itineraire", default="0"): _liste(self._options_itineraires())})
        return self.async_show_form(
            step_id="itineraire", data_schema=schema, description_placeholders={"destination": _libelle_lieu(self._dest)}
        )

    def _depart_maison(self, choix: dict[str, Any]) -> str | None:
        """Départ habituel de la maison : départ des transports moins temps sur place et trajet du détour."""
        depart = date_navitia(choix.get("depart"))
        if depart is None:
            return None
        if self._detour.get(T_DET_ID):
            if self._detour.get(T_DET_HEURE):
                h, m, *_ = (int(x) for x in self._detour[T_DET_HEURE].split(":"))
                depart = depart.replace(hour=h, minute=m)
            else:
                depart -= timedelta(minutes=float(self._detour.get(T_DET_PAUSE) or 0))
            depart -= timedelta(minutes=float(self._detour.get(T_DET_DUREE) or 15))
        return depart.strftime("%H:%M:%S")

    def _titre(self, d: dict[str, Any]) -> str:
        etat = self.hass.states.get(d[T_PERSONNE])
        qui = etat.name if etat else d[T_PERSONNE]
        return f"{d[T_NOM]} ({qui})"

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sous = self._get_reconfigure_subentry()
        d = dict(sous.data)
        if user_input is not None:
            recalculer = user_input.pop("recalculer", False)
            texte_detour = (user_input.pop("recherche_detour", "") or "").strip()
            if user_input.pop("supprimer_detour", False):
                d = {k: v for k, v in d.items() if not k.startswith("detour_")}
            if d.get(T_DET_ID) and T_DET_HEURE not in user_input:
                d.pop(T_DET_HEURE, None)  # heure imposée effacée
            nouveau = {**d, **user_input}
            if texte_detour:
                self._lieux_detour, erreur = await self._chercher(texte_detour, TYPES_DESTINATION)
                if erreur:
                    return self.async_abort(reason=erreur)
                self._saisie = nouveau
                self._dest = {"id": d[T_DEST_ID], "libelle": d.get(T_DEST_NOM)}
                if d.get(T_ORIGINE_ID):
                    self._origine = {"id": d[T_ORIGINE_ID], "libelle": d.get(T_ORIGINE_NOM)}
                return await self.async_step_detour()
            change = any(user_input.get(k) != d.get(k) for k in (T_ORIGINE, T_HEURE, T_TYPE_HEURE))
            if user_input.get(T_ORIGINE) != d.get(T_ORIGINE):
                nouveau[T_ORIGINE_ID] = nouveau[T_ORIGINE_NOM] = None  # nouvelle zone de départ
            elif d.get(T_ORIGINE_ID):
                self._origine = {"id": d[T_ORIGINE_ID], "libelle": d.get(T_ORIGINE_NOM)}
            if nouveau.get(T_DET_ID):
                self._detour = {k: v for k, v in nouveau.items() if k.startswith("detour_")}
            if recalculer or change:
                self._saisie = nouveau
                self._dest = {"id": d[T_DEST_ID], "libelle": d.get(T_DEST_NOM)}
                return await self.async_step_itineraire()
            return self.async_update_and_abort(self._get_entry(), sous, title=self._titre(nouveau), data=nouveau)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._schema_trajet(d, False),
            description_placeholders={"destination": d.get(T_DEST_NOM, ""), "itineraire": d.get(T_RESUME, "")},
        )
