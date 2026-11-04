"""Mock PMS : routes testées par de vraies requêtes HTTP, et décalage des dates."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

import main
from decaler_dates import AGE_MAX_JOURS, decaler_dates
from models import StatutPMS, get_session
from outils import creer_avis_dans_pms, date_dans_pms

EVALUATION = StatutPMS.EN_ATTENTE_EVALUATION
PUBLICATION = StatutPMS.EN_ATTENTE_PUBLICATION
PUBLIE = StatutPMS.PUBLIE

client = TestClient(main.app)  # envoie les requêtes HTTP à l'API, sans lancer de serveur


def changer_statut(identifiant, statut):
    return client.patch(f"/reviews/{identifiant}/statut", json={"statut": statut.value})


def lire_statut(identifiant):
    return client.get(f"/reviews/{identifiant}").json()["statut"]


def publier(identifiant):
    return client.post(f"/reviews/{identifiant}/publish", json={"reponse": "Merci !"})


# --- Transitions : un seul passage autorisé par PATCH ---

def test_evaluation_vers_publication_est_accepte():
    avis = creer_avis_dans_pms(EVALUATION)

    reponse = changer_statut(avis, PUBLICATION)

    assert reponse.status_code == 200
    assert lire_statut(avis) == PUBLICATION.value


def test_publication_ne_peut_pas_revenir_en_evaluation():
    avis = creer_avis_dans_pms(PUBLICATION)

    reponse = changer_statut(avis, EVALUATION)

    assert reponse.status_code == 409
    assert lire_statut(avis) == PUBLICATION.value


def test_publie_ne_peut_pas_revenir_en_evaluation():
    avis = creer_avis_dans_pms(PUBLIE)

    reponse = changer_statut(avis, EVALUATION)

    assert reponse.status_code == 409
    assert lire_statut(avis) == PUBLIE.value


def test_publie_ne_peut_pas_revenir_en_publication():
    avis = creer_avis_dans_pms(PUBLIE)

    reponse = changer_statut(avis, PUBLICATION)

    assert reponse.status_code == 409
    assert lire_statut(avis) == PUBLIE.value


def test_evaluation_vers_evaluation_est_refuse():
    avis = creer_avis_dans_pms(EVALUATION)

    assert changer_statut(avis, EVALUATION).status_code == 409


def test_publication_vers_publication_est_refuse():
    avis = creer_avis_dans_pms(PUBLICATION)

    assert changer_statut(avis, PUBLICATION).status_code == 409


def test_patch_ne_peut_pas_publier():
    avis = creer_avis_dans_pms(EVALUATION)

    reponse = changer_statut(avis, PUBLIE)

    assert reponse.status_code == 400
    assert lire_statut(avis) == EVALUATION.value


def test_patch_sur_un_avis_introuvable():
    assert changer_statut(999, PUBLICATION).status_code == 404


# --- Publication ---

def test_publier_enregistre_la_reponse():
    avis = creer_avis_dans_pms(PUBLICATION)

    reponse = publier(avis)

    assert reponse.status_code == 200
    assert reponse.json()["statut"] == PUBLIE.value
    assert reponse.json()["reponse_publiee"] == "Merci !"
    assert reponse.json()["date_publication"] is not None


def test_publier_est_refuse_en_evaluation():
    assert publier(creer_avis_dans_pms(EVALUATION)).status_code == 409


def test_publier_est_refuse_si_deja_publie():
    assert publier(creer_avis_dans_pms(PUBLIE)).status_code == 409


# --- Liste des avis ---

def test_liste_filtree_par_statut():
    attendu = creer_avis_dans_pms(EVALUATION)
    creer_avis_dans_pms(PUBLICATION)

    reponse = client.get("/reviews", params={"statut": EVALUATION.value})

    assert [avis["id"] for avis in reponse.json()] == [attendu]


# --- Décalage des dates ---

def lancer_decaler_dates():
    session = get_session(main.engine)
    nombre = decaler_dates(session)
    session.close()
    return nombre


def test_decaler_dates_rend_les_avis_en_attente_recents():
    avis = [creer_avis_dans_pms(EVALUATION) for _ in range(30)]

    assert lancer_decaler_dates() == 30

    plus_ancienne = date.today() - timedelta(days=AGE_MAX_JOURS)
    for identifiant in avis:
        assert plus_ancienne <= date_dans_pms(identifiant) <= date.today()


def test_decaler_dates_ne_touche_pas_aux_autres_statuts():
    ancienne = date(2020, 1, 1)
    en_publication = creer_avis_dans_pms(PUBLICATION, ancienne)
    publie = creer_avis_dans_pms(PUBLIE, ancienne)

    assert lancer_decaler_dates() == 0

    assert date_dans_pms(en_publication) == ancienne
    assert date_dans_pms(publie) == ancienne
