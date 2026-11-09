"""Refus 429 de PRIM (trop de requêtes) : réessai après une pause, pas d'erreur « quota »."""

import pytest

from custom_components.transports_idf.api import ClientPrim, ErreurQuota


class Reponse:
    def __init__(self, status, corps=None):
        self.status, self._corps, self.headers = status, corps or {}, {}

    async def json(self, content_type=None):
        return self._corps

    async def text(self):
        return "Too Many Requests"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Session:
    def __init__(self, statuts):
        self.statuts, self.appels = list(statuts), 0

    def get(self, url, **kwargs):
        self.appels += 1
        statut = self.statuts.pop(0)
        return Reponse(statut, {"ok": True} if statut == 200 else None)


async def test_429_puis_succes():
    session = Session([429, 429, 200])
    assert await ClientPrim(session, "jeton")._get("https://exemple/x") == {"ok": True}
    assert session.appels == 3


async def test_429_persistant():
    session = Session([429, 429, 429, 429])
    with pytest.raises(ErreurQuota, match="trop de requêtes"):
        await ClientPrim(session, "jeton")._get("https://exemple/x")
    assert session.appels == 3  # 1 essai + 2 réessais (pauses réduites en test)
