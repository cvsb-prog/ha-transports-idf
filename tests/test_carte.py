"""Logique des cartes (tronçons coupés, arrêts non desservis) : script Node, sauté si Node est absent."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js absent")
def test_reseau_de_la_carte():
    script = Path(__file__).parent / "carte" / "reseau.cjs"
    sortie = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert sortie.returncode == 0, sortie.stderr
    assert sortie.stdout.strip() == "ok"
