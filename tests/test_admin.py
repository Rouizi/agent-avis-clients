"""Interface Flask (admin/app.py) : connexion, validation, rejet, revue manuelle et publication sur le PMS."""
from datetime import date, timedelta

import pytest
import requests
from fastapi.testclient import TestClient

import app as admin
import main
import pipeline
from db_models import ActionLog, Avis, Plateforme, StatutErreur, StatutPipeline, get_session
from models import StatutPMS
from outils import creer_avis_dans_pms, tous_les_avis, tous_les_logs

REPONSE_AGENT = "Merci pour votre séjour, ravis que la terrasse vous ait plu."
pms = TestClient(main.app)


# --- Préparation ---

class PmsDansLesTests:
    """Envoie les appels HTTP de Flask au mock PMS des tests, sans lancer de serveur."""

    def appel(self, methode, url, **options):
        options.pop("timeout", None)
        chemin = url.replace(admin.CONFIG["pms"]["base_url"], "")
        return getattr(pms, methode)(chemin, **options)

    def patch(self, url, **options):
        return self.appel("patch", url, **options)

    def post(self, url, **options):
        return self.appel("post", url, **options)

    def get(self, url, **options):
        return self.appel("get", url, **options)


def pms_arrete(*args, **kwargs):
    raise requests.ConnectionError("PMS arrêté")


@pytest.fixture(autouse=True)
def pms_branche(monkeypatch):
    faux = PmsDansLesTests()
    monkeypatch.setattr(admin.requests, "patch", faux.patch)
    monkeypatch.setattr(admin.requests, "post", faux.post)
    monkeypatch.setattr(admin.requests, "get", faux.get)
    monkeypatch.setenv("ADMIN_MOT_DE_PASSE", "secret")


@pytest.fixture
def navigateur():
    """Un navigateur déjà connecté."""
    client = admin.app.test_client()
    with client.session_transaction() as session:
        session["connecte"] = True
    return client


def avis_a_valider(jours=1, **champs):
    """Un avis présent dans le PMS et dans la base de l'agent, avec une réponse à valider. Renvoie son id."""
    identifiant = creer_avis_dans_pms(StatutPMS.EN_ATTENTE_EVALUATION)
    valeurs = {"statut_pipeline": StatutPipeline.EN_VALIDATION, "reponse_generee": REPONSE_AGENT}
    valeurs.update(champs)
    session = get_session(pipeline.engine)
    avis = Avis(review_id_pms=identifiant, plateforme=Plateforme.AIRBNB, note=8,
                texte_avis=f"Avis {identifiant}", date_avis=date.today() - timedelta(days=jours), **valeurs)
    session.add(avis)
    session.commit()
    avis_id = avis.id
    session.close()
    return avis_id


def lire(avis_id):
    session = get_session(pipeline.engine)
    avis = session.get(Avis, avis_id)
    session.close()
    return avis


def statut_dans_le_pms(avis_id):
    return pms.get(f"/reviews/{lire(avis_id).review_id_pms}").json()


# --- Connexion ---

def test_sans_connexion_on_est_renvoye_vers_la_page_de_connexion():
    reponse = admin.app.test_client().get("/")

    assert reponse.status_code == 302
    assert "/connexion" in reponse.headers["Location"]


def test_un_mauvais_mot_de_passe_est_refuse():
    reponse = admin.app.test_client().post("/connexion", data={"mot_de_passe": "faux"})

    assert "Mot de passe incorrect" in reponse.get_data(as_text=True)


def test_le_bon_mot_de_passe_ouvre_la_file_de_validation():
    client = admin.app.test_client()

    reponse = client.post("/connexion", data={"mot_de_passe": "secret"}, follow_redirects=True)

    assert "À valider" in reponse.get_data(as_text=True)


def test_sans_mot_de_passe_defini_personne_ne_peut_se_connecter(monkeypatch):
    monkeypatch.delenv("ADMIN_MOT_DE_PASSE")

    reponse = admin.app.test_client().post("/connexion", data={"mot_de_passe": ""}, follow_redirects=True)

    assert "ADMIN_MOT_DE_PASSE" in reponse.get_data(as_text=True)


# --- Valider ---

def test_valider_telle_quelle_publie_la_reponse_sur_le_pms(navigateur):
    avis_id = avis_a_valider()

    navigateur.post(f"/avis/{avis_id}/valider", data={"reponse": REPONSE_AGENT})

    avis = lire(avis_id)
    assert avis.statut_pipeline == StatutPipeline.SYNCHRONISE_PMS
    assert avis.reponse_validee == REPONSE_AGENT
    assert avis.date_validation is not None
    assert statut_dans_le_pms(avis_id)["statut"] == "publié"


def test_une_reponse_modifiee_est_publiee_avec_le_texte_modifie(navigateur):
    avis_id = avis_a_valider()

    navigateur.post(f"/avis/{avis_id}/valider", data={"reponse": "Merci, à bientôt !"})

    assert lire(avis_id).reponse_validee == "Merci, à bientôt !"
    assert lire(avis_id).reponse_generee == REPONSE_AGENT  # gardée pour le taux de retouche


def test_la_validation_est_journalisee_avec_la_facon_de_valider(navigateur):
    avis_id = avis_a_valider()

    navigateur.post(f"/avis/{avis_id}/valider", data={"reponse": "Merci, à bientôt !"})

    actions = [(log.action, log.details) for log in tous_les_logs()]
    assert (ActionLog.VALIDE, "Réponse modifiée") in actions
    assert ActionLog.PUBLIE in [action for action, _ in actions]


def test_une_reponse_vide_n_est_pas_publiee(navigateur):
    avis_id = avis_a_valider()

    navigateur.post(f"/avis/{avis_id}/valider", data={"reponse": "   "})

    assert lire(avis_id).statut_pipeline == StatutPipeline.EN_VALIDATION
    assert statut_dans_le_pms(avis_id)["statut"] == "en_attente_evaluation"


# --- Publication quand le PMS ne répond pas ---

def test_pms_arrete_la_reponse_reste_validee(navigateur, monkeypatch):
    avis_id = avis_a_valider()
    monkeypatch.setattr(admin.requests, "patch", pms_arrete)

    reponse = navigateur.post(f"/avis/{avis_id}/valider", data={"reponse": REPONSE_AGENT},
                              follow_redirects=True)

    assert lire(avis_id).statut_pipeline == StatutPipeline.VALIDE
    assert "Publication à refaire" in reponse.get_data(as_text=True)


def test_une_publication_ratee_peut_etre_refaite(navigateur, monkeypatch):
    avis_id = avis_a_valider()
    monkeypatch.setattr(admin.requests, "post", pms_arrete)  # le PATCH passe, le POST échoue
    navigateur.post(f"/avis/{avis_id}/valider", data={"reponse": REPONSE_AGENT})
    monkeypatch.setattr(admin.requests, "post", PmsDansLesTests().post)  # le PMS revient

    navigateur.post(f"/avis/{avis_id}/publier")

    assert lire(avis_id).statut_pipeline == StatutPipeline.SYNCHRONISE_PMS
    assert statut_dans_le_pms(avis_id)["reponse_publiee"] == REPONSE_AGENT


def test_republier_un_avis_deja_publie_dans_le_pms_est_un_succes():
    avis_id = avis_a_valider()
    identifiant = lire(avis_id).review_id_pms
    assert admin.publier_sur_pms(identifiant, REPONSE_AGENT) is None

    assert admin.publier_sur_pms(identifiant, REPONSE_AGENT) is None


# --- Rejeter ---

def test_rejeter_genere_une_nouvelle_reponse(navigateur):
    avis_id = avis_a_valider()

    navigateur.post(f"/avis/{avis_id}/rejeter")

    avis = lire(avis_id)
    assert avis.reponse_generee == "Merci."  # la réponse du faux LLM des tests
    assert avis.nb_tentatives_regen == 1
    assert avis.statut_pipeline == StatutPipeline.EN_VALIDATION


def test_au_troisieme_rejet_l_avis_est_a_traiter_manuellement(navigateur):
    avis_id = avis_a_valider()

    for _ in range(3):
        navigateur.post(f"/avis/{avis_id}/rejeter")

    assert lire(avis_id).statut_pipeline == StatutPipeline.A_TRAITER_MANUELLEMENT


# --- Les files ---

def test_un_avis_en_erreur_n_est_pas_dans_la_file_de_validation():
    avis_a_valider(statut_erreur=StatutErreur.EN_ERREUR)
    session = get_session(pipeline.engine)

    assert admin.file_de_validation(session) == []
    session.close()


def test_les_avis_proches_du_delai_passent_en_premier():
    recent = avis_a_valider(jours=1)
    urgent = avis_a_valider(jours=12)
    session = get_session(pipeline.engine)

    assert [a.id for a in admin.file_de_validation(session)] == [urgent, recent]
    session.close()


# --- Revue manuelle ---

def avis_hors_sujet():
    return avis_a_valider(reponse_generee=None, flag_hors_sujet=True,
                          raison_hors_sujet="Plainte sur une livraison")


def test_un_hors_sujet_est_dans_la_revue_et_pas_dans_la_validation(navigateur):
    avis_hors_sujet()

    assert "Plainte sur une livraison" in navigateur.get("/revue").get_data(as_text=True)
    assert "Aucune réponse à valider" in navigateur.get("/").get_data(as_text=True)


def test_ecarter_un_hors_sujet(navigateur):
    avis_id = avis_hors_sujet()

    navigateur.post(f"/avis/{avis_id}/ecarter")

    assert lire(avis_id).statut_pipeline == StatutPipeline.ECARTE
    assert ActionLog.ECARTE in [log.action for log in tous_les_logs()]
    assert statut_dans_le_pms(avis_id)["statut"] == "en_attente_evaluation"  # rien n'est publié


def test_un_faux_hors_sujet_recoit_une_reponse_ecrite_a_la_main(navigateur):
    avis_id = avis_hors_sujet()

    navigateur.post(f"/avis/{avis_id}/repondre")
    navigateur.post(f"/avis/{avis_id}/valider", data={"reponse": "Merci, ravis que l'appartement vous ait plu."})

    assert lire(avis_id).statut_pipeline == StatutPipeline.SYNCHRONISE_PMS
    assert ("Réponse écrite à la main" in [log.details for log in tous_les_logs()])


# --- Les pages s'affichent ---

def test_toutes_les_pages_s_affichent(navigateur):
    avis_a_valider()
    avis_a_valider(jours=20, statut_pipeline=StatutPipeline.A_TRAITER_MANUELLEMENT)
    avis_hors_sujet()

    for page in ["/", "/revue", "/manuel", "/tableau-de-bord"]:
        assert navigateur.get(page).status_code == 200, page


def test_le_tableau_de_bord_affiche_le_taux_de_retouche(navigateur):
    navigateur.post(f"/avis/{avis_a_valider()}/valider", data={"reponse": REPONSE_AGENT})
    navigateur.post(f"/avis/{avis_a_valider()}/valider", data={"reponse": "Autre texte."})

    assert "50 %" in navigateur.get("/tableau-de-bord").get_data(as_text=True)
    assert len(tous_les_avis()) == 2


# --- Corrections de la revue de code du 2026-11-16 ---

def test_une_reponse_multiligne_validee_telle_quelle_n_est_pas_une_retouche(navigateur):
    # Le navigateur renvoie les sauts de ligne en \r\n : ça ne doit pas compter comme retouche
    avis_id = avis_a_valider(reponse_generee="Merci pour la terrasse.\nÀ bientôt.")

    navigateur.post(f"/avis/{avis_id}/valider", data={"reponse": "Merci pour la terrasse.\r\nÀ bientôt."})

    actions = [(log.action, log.details) for log in tous_les_logs()]
    assert (ActionLog.VALIDE, "Réponse telle quelle") in actions


def test_un_avis_publie_ne_peut_pas_etre_ecarte(navigateur):
    # Un onglet resté ouvert sur la revue ne doit pas écarter un avis déjà publié
    avis_id = avis_a_valider()
    navigateur.post(f"/avis/{avis_id}/valider", data={"reponse": REPONSE_AGENT})

    navigateur.post(f"/avis/{avis_id}/ecarter")

    assert lire(avis_id).statut_pipeline == StatutPipeline.SYNCHRONISE_PMS


def test_un_avis_publie_ne_peut_pas_repartir_en_file_manuelle(navigateur):
    avis_id = avis_a_valider()
    navigateur.post(f"/avis/{avis_id}/valider", data={"reponse": REPONSE_AGENT})

    navigateur.post(f"/avis/{avis_id}/repondre")

    assert lire(avis_id).statut_pipeline == StatutPipeline.SYNCHRONISE_PMS


def test_un_avis_en_erreur_ne_peut_pas_etre_rejete(navigateur):
    avis_id = avis_a_valider(statut_erreur=StatutErreur.EN_ERREUR)

    navigateur.post(f"/avis/{avis_id}/rejeter")

    assert lire(avis_id).reponse_generee == REPONSE_AGENT  # pas de régénération


def test_le_champ_retour_ne_peut_pas_rediriger_vers_un_autre_site(navigateur):
    avis_id = avis_a_valider()

    reponse = navigateur.post(f"/avis/{avis_id}/valider",
                              data={"reponse": REPONSE_AGENT, "retour": "https://site-pirate.example"})

    assert reponse.headers["Location"] == "/"
