"""
Partie technique des tests : pas besoin de la lire pour comprendre les tests.
Avant chaque test, automatiquement :
  - les deux bases sont remplacées par des bases temporaires et vides ;
  - le LLM et les garde-fous du pipeline sont remplacés par des faux, sans aucun appel à OpenAI ;
  - time.sleep n'attend plus, pour que les tests soient instantanés.
Après chaque test, tout ce que le test a remplacé est remis en place.
"""
import importlib
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests

RACINE = Path(__file__).parents[1]
sys.path[:0] = [str(RACINE / "agent"), str(RACINE / "mock_pms")]
os.environ["OPENAI_API_KEY"] = "faux-pour-test"  # le client OpenAI est créé à l'import

import db_models
import models

_DOSSIER_IMPORT = Path(tempfile.mkdtemp())


def _importer_sans_vraie_base(nom: str, module_bd):
    """pipeline et main ouvrent leur base dès l'import : on les redirige vers un fichier temporaire."""
    vrai_init_db = module_bd.init_db
    module_bd.init_db = lambda: vrai_init_db(f"sqlite:///{_DOSSIER_IMPORT / nom}.db")
    try:
        return importlib.import_module(nom)
    finally:
        module_bd.init_db = vrai_init_db


pipeline = _importer_sans_vraie_base("pipeline", db_models)
main = _importer_sans_vraie_base("main", models)

import guardrails
import llm_client
import outils


class _OpenAIInterdit:
    """Fait échouer tout test qui tenterait un vrai appel à OpenAI."""

    def __getattr__(self, nom):
        raise AssertionError("Appel à OpenAI interdit pendant les tests")


@pytest.fixture(autouse=True)
def _preparer_chaque_test(tmp_path, monkeypatch):
    # Bases temporaires
    monkeypatch.setattr(pipeline, "engine", db_models.init_db(f"sqlite:///{tmp_path / 'agent.db'}"))
    monkeypatch.setattr(main, "engine", models.init_db(f"sqlite:///{tmp_path / 'pms.db'}"))

    # Faux par défaut
    monkeypatch.setattr(llm_client, "client", _OpenAIInterdit())
    monkeypatch.setattr(guardrails, "client", _OpenAIInterdit())
    monkeypatch.setattr(pipeline, "generer_reponse", outils.faux_generer)
    monkeypatch.setattr(pipeline, "evaluer_avis", outils.faux_evaluer_sans_flag)
    monkeypatch.setattr(time, "sleep", outils.attentes.append)
    outils.appels_llm.clear()
    outils.attentes.clear()

    # Ce que les tests remplacent eux-mêmes : la valeur d'origine est remise après le test
    monkeypatch.setattr(pipeline, "_recuperer_avis_pms", pipeline._recuperer_avis_pms)
    monkeypatch.setattr(guardrails, "est_hors_sujet", guardrails.est_hors_sujet)
    monkeypatch.setattr(requests, "get", requests.get)
    monkeypatch.setitem(pipeline.CONFIG["pms"], "base_url", pipeline.CONFIG["pms"]["base_url"])
