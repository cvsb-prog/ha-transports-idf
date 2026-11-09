import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def caches_vides():
    """Les plans de ligne sont gardés une journée en mémoire : chaque test repart de zéro."""
    from custom_components.transports_idf import _PLANS
    from custom_components.transports_idf.api import _PLANS_COMPLETS

    _PLANS.clear()
    _PLANS_COMPLETS.clear()
    yield


@pytest.fixture(autouse=True)
def debit_libre(monkeypatch):
    """Pas d'espacement des requêtes en test (l'horloge peut y être figée)."""
    from custom_components.transports_idf import api

    monkeypatch.setattr(api, "ESPACEMENT", 0.0)
    monkeypatch.setattr(api, "PAUSES_429", (0.0, 0.0))
    yield
