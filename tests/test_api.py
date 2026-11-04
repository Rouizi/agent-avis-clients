"""API de l'agent, appelée par n8n : routes testées par de vraies requêtes HTTP."""
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

import pipeline

client = TestClient(pipeline.app)  # le test attend la fin de la tâche de fond avant de continuer


def lire_statut():
    return client.get("/run-status").json()


def test_statut_avant_tout_lancement():
    assert lire_statut()["statut"] == "jamais_lance"


def test_lancer_un_run_puis_lire_le_resultat():
    pipeline.run_pipeline = lambda: {"generes": 5}

    reponse = client.post("/run-pipeline")

    assert reponse.status_code == 202
    assert lire_statut()["statut"] == "termine"
    assert lire_statut()["resume"] == {"generes": 5}
    assert "fin" in lire_statut()


def test_run_qui_plante_donne_le_statut_echec():
    def run_en_panne():
        raise RuntimeError("Le PMS ne répond pas")

    pipeline.run_pipeline = run_en_panne

    client.post("/run-pipeline")

    assert lire_statut()["statut"] == "echec"
    assert lire_statut()["erreur"] == "Le PMS ne répond pas"


def test_on_peut_relancer_apres_un_echec():
    def run_en_panne():
        raise RuntimeError("Panne")

    pipeline.run_pipeline = run_en_panne
    client.post("/run-pipeline")

    pipeline.run_pipeline = lambda: {"generes": 1}

    assert client.post("/run-pipeline").status_code == 202
    assert lire_statut()["statut"] == "termine"


def test_second_lancement_refuse_pendant_un_run():
    pipeline._verrou.acquire()  # comme si un run tournait déjà
    try:
        assert client.post("/run-pipeline").status_code == 409
        assert client.post("/rejouer-erreurs").status_code == 409
    finally:
        pipeline._verrou.release()


def test_statut_en_cours_avant_meme_le_demarrage_du_run():
    pipeline.ETAT.update({"statut": "termine", "resume": {"generes": 200}})  # le run de la semaine dernière

    pipeline._lancer(BackgroundTasks(), lambda: {})  # la tâche est prévue, mais pas encore démarrée
    pipeline._verrou.release()

    assert lire_statut()["statut"] == "en_cours"
    assert "resume" not in lire_statut()


def test_rejouer_les_erreurs_passe_par_l_api():
    pipeline.rejouer_erreurs = lambda: {"a_rejouer": 2}

    assert client.post("/rejouer-erreurs").status_code == 202
    assert lire_statut()["resume"] == {"a_rejouer": 2}
