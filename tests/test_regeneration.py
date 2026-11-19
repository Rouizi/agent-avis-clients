"""Rejet d'une réponse par le relecteur (zone grise #2) et file d'erreur (zone grise #7)."""
import pipeline
from db_models import ActionLog, Avis, StatutErreur, StatutPipeline, get_session
from llm_client import LLMError
from outils import ajouter_avis_en_base, appels_llm, avis_pms, tous_les_avis, tous_les_logs, verdict


def ajouter_avis_a_valider(**champs):
    ajouter_avis_en_base(1, statut_pipeline=StatutPipeline.EN_VALIDATION, reponse_generee="Première réponse", **champs)


def rejeter():
    """Fait ce que fera Flask quand le relecteur rejette la réponse."""
    session = get_session(pipeline.engine)
    avis = session.query(Avis).one()
    resultat = pipeline.regenerer(session, avis)
    session.close()
    return resultat


def generation_en_panne(*args, **kwargs):
    raise LLMError("Panne", tentatives=3)


# --- Rejets ---

def test_premier_rejet_donne_une_nouvelle_reponse():
    ajouter_avis_a_valider()

    assert rejeter() is True

    avis = tous_les_avis()[0]
    assert avis.reponse_generee == "Merci."
    assert avis.nb_tentatives_regen == 1
    assert avis.statut_pipeline == StatutPipeline.EN_VALIDATION


def test_regeneration_note_la_version_du_prompt():
    ajouter_avis_a_valider()

    rejeter()

    assert tous_les_avis()[0].prompt_version == pipeline.CONFIG["llm"]["prompt_version"]


def test_la_reponse_rejetee_est_envoyee_en_contre_exemple():
    recu = []
    pipeline.generer_reponse = lambda *args, reponse_rejetee: (
        recu.append(reponse_rejetee) or {"reponse": "Autre", "score_confiance": 70, "tentatives": 1})
    ajouter_avis_a_valider()

    rejeter()

    assert recu == ["Première réponse"]


def test_troisieme_rejet_envoie_en_traitement_manuel():
    ajouter_avis_a_valider()

    assert rejeter() is True
    assert rejeter() is True
    assert tous_les_avis()[0].statut_pipeline == StatutPipeline.EN_VALIDATION

    assert rejeter() is False

    avis = tous_les_avis()[0]
    assert avis.statut_pipeline == StatutPipeline.A_TRAITER_MANUELLEMENT
    assert avis.nb_tentatives_regen == 2
    assert len(appels_llm) == 2  # pas de génération au 3e rejet


# --- Régénération en échec ---

def test_regeneration_en_echec_part_en_file_d_erreur():
    pipeline.generer_reponse = generation_en_panne
    ajouter_avis_a_valider()

    assert rejeter() is False

    avis = tous_les_avis()[0]
    assert avis.statut_erreur == StatutErreur.EN_ERREUR
    assert avis.reponse_generee == "Première réponse"
    assert ActionLog.ERREUR_FINALE in [log.action for log in tous_les_logs()]


def test_rejeu_apres_regeneration_en_echec_garde_le_contre_exemple():
    recu = []
    ajouter_avis_a_valider(statut_erreur=StatutErreur.EN_ERREUR)
    pipeline.generer_reponse = lambda *args, reponse_rejetee: (
        recu.append(reponse_rejetee) or {"reponse": "Autre", "score_confiance": 70, "tentatives": 1})

    resume = pipeline.rejouer_erreurs()

    avis = tous_les_avis()[0]
    assert resume["generes"] == 1
    assert recu == ["Première réponse"]
    assert avis.reponse_generee == "Autre"
    assert avis.statut_erreur == StatutErreur.OK


# --- File d'erreur pendant un run ---

def test_generation_en_echec_pendant_un_run_part_en_file_d_erreur():
    pipeline.generer_reponse = generation_en_panne
    pipeline._recuperer_avis_pms = lambda: [avis_pms(1)]

    resume = pipeline.run_pipeline()

    avis = tous_les_avis()[0]
    assert resume["erreurs"] == 1
    assert avis.statut_erreur == StatutErreur.EN_ERREUR
    assert avis.statut_pipeline == StatutPipeline.NOUVEAU
    assert avis.nb_tentatives_erreur == 3


def test_avis_en_erreur_n_est_pas_retraite_au_run_suivant():
    pipeline.generer_reponse = generation_en_panne
    pipeline._recuperer_avis_pms = lambda: [avis_pms(1)]
    pipeline.run_pipeline()

    pipeline.generer_reponse = lambda *args, **kwargs: {"reponse": "Ok", "score_confiance": 70, "tentatives": 1}
    resume = pipeline.run_pipeline()

    assert resume["generes"] == 0
    assert resume["deja_traites"] == 1


def test_rejouer_les_erreurs_traite_l_avis():
    pipeline.generer_reponse = generation_en_panne
    pipeline._recuperer_avis_pms = lambda: [avis_pms(1)]
    pipeline.run_pipeline()

    pipeline.generer_reponse = lambda *args, **kwargs: {"reponse": "Ok", "score_confiance": 70, "tentatives": 1}
    resume = pipeline.rejouer_erreurs()

    avis = tous_les_avis()[0]
    assert resume["a_rejouer"] == 1
    assert resume["generes"] == 1
    assert avis.statut_erreur == StatutErreur.OK
    assert avis.statut_pipeline == StatutPipeline.EN_VALIDATION


def test_un_rejeu_signale_retourne_en_revue_sans_l_ancienne_reponse(monkeypatch):
    # Une régénération échouée laisse une ancienne réponse ; si le rejeu signale l'avis
    # (hors-sujet), elle doit être effacée, sinon l'avis irait dans la file de validation
    ajouter_avis_en_base(1, statut_pipeline=StatutPipeline.EN_VALIDATION,
                         reponse_generee="Ancienne réponse rejetée",
                         statut_erreur=StatutErreur.EN_ERREUR)
    monkeypatch.setattr(pipeline, "evaluer_avis",
                        lambda *args, **kwargs: verdict(hors_sujet=True, raison="spam"))

    pipeline.rejouer_erreurs()

    avis = tous_les_avis()[0]
    assert avis.flag_hors_sujet
    assert avis.reponse_generee is None
    assert avis.statut_erreur == StatutErreur.OK
