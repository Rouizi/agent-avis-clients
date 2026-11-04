"""Déroulement d'un run (pipeline.py), avec un faux LLM, et requêtes de KPI de cdc-notes.md."""
import guardrails
import pipeline
from db_models import ActionLog, StatutErreur, StatutPipeline
from outils import (
    ajouter_avis_en_base, appels_llm, avis_pms, avis_pms_avec_texte, cinq_avis,
    executer_sql, ids_en_base, tous_les_avis, tous_les_logs, verdict,
)


# --- Reprise après crash ---

def test_reprend_un_avis_reste_nouveau():
    ajouter_avis_en_base(1, statut_pipeline=StatutPipeline.NOUVEAU)
    pipeline._recuperer_avis_pms = lambda: []

    premier_run = pipeline.run_pipeline()
    second_run = pipeline.run_pipeline()

    assert premier_run["repris"] == 1
    assert premier_run["generes"] == 1
    assert second_run["repris"] == 0
    assert tous_les_avis()[0].statut_pipeline == StatutPipeline.EN_VALIDATION


def test_ne_reprend_pas_un_avis_en_file_d_erreur():
    ajouter_avis_en_base(1, statut_pipeline=StatutPipeline.NOUVEAU, statut_erreur=StatutErreur.EN_ERREUR)
    pipeline._recuperer_avis_pms = lambda: []

    resume = pipeline.run_pipeline()

    assert resume["repris"] == 0
    assert appels_llm == []


# --- Option limite ---

def test_limite_traite_les_plus_anciens_d_abord():
    pipeline._recuperer_avis_pms = cinq_avis

    resume = pipeline.run_pipeline(limite=2)

    assert resume["generes"] == 2
    assert appels_llm == ["Avis 5", "Avis 4"]


def test_limite_passe_aux_suivants_au_run_d_apres():
    pipeline._recuperer_avis_pms = cinq_avis

    pipeline.run_pipeline(limite=2)
    resume = pipeline.run_pipeline(limite=2)

    assert resume["generes"] == 2
    assert resume["deja_traites"] == 2
    assert ids_en_base() == [2, 3, 4, 5]


def test_sans_limite_tout_est_traite():
    pipeline._recuperer_avis_pms = cinq_avis

    resume = pipeline.run_pipeline()

    assert resume["recuperes"] == 5
    assert resume["generes"] == 5


def test_avis_vides_ne_comptent_pas_dans_la_limite():
    pipeline._recuperer_avis_pms = lambda: [
        avis_pms_avec_texte(5, ""), avis_pms_avec_texte(4, "  "),
        avis_pms(3), avis_pms(2), avis_pms(1),
    ]

    resume = pipeline.run_pipeline(limite=2)

    assert resume["vides"] == 2
    assert resume["generes"] == 2
    assert ids_en_base() == [2, 3]


def test_reprises_comptent_dans_la_limite():
    for identifiant in (101, 102, 103):
        ajouter_avis_en_base(identifiant, statut_pipeline=StatutPipeline.NOUVEAU)
    pipeline._recuperer_avis_pms = cinq_avis

    premier_run = pipeline.run_pipeline(limite=2)
    assert premier_run["repris"] == 2
    assert appels_llm == ["Crash 101", "Crash 102"]

    second_run = pipeline.run_pipeline(limite=2)
    assert second_run["repris"] == 1
    assert appels_llm[2:] == ["Crash 103", "Avis 5"]


# --- Avis vides, avec les vrais garde-fous et un faux classifieur ---

def test_avis_vides_ni_stockes_ni_evalues():
    commentaires_classes = []

    def faux_classifieur(commentaire):
        commentaires_classes.append(commentaire)
        return False, ""

    guardrails.est_hors_sujet = faux_classifieur
    pipeline.evaluer_avis = guardrails.evaluer_avis
    pipeline._recuperer_avis_pms = lambda: [
        avis_pms_avec_texte(1, ""), avis_pms_avec_texte(2, "   "), avis_pms_avec_texte(3, None),
        avis_pms_avec_texte(4, "Très bon séjour."),
    ]

    resume = pipeline.run_pipeline()

    assert resume["vides"] == 3
    assert resume["generes"] == 1
    assert commentaires_classes == ["Très bon séjour."]
    assert ids_en_base() == [4]


# --- raison_hors_sujet ---

def test_hors_sujet_avec_raison():
    pipeline.evaluer_avis = lambda *args: verdict(hors_sujet=True, raison="Spam publicitaire.")
    pipeline._recuperer_avis_pms = lambda: [avis_pms(1)]

    resume = pipeline.run_pipeline()

    avis = tous_les_avis()[0]
    assert resume["signales"] == 1
    assert avis.flag_hors_sujet
    assert avis.raison_hors_sujet == "Spam publicitaire."
    assert avis.reponse_generee is None
    assert appels_llm == []


def test_la_raison_hors_sujet_est_aussi_dans_les_logs():
    pipeline.evaluer_avis = lambda *args: verdict(hors_sujet=True, raison="Spam publicitaire.")
    pipeline._recuperer_avis_pms = lambda: [avis_pms(1)]

    pipeline.run_pipeline()

    log = tous_les_logs()[0]
    assert log.action == ActionLog.SIGNALE
    assert "Spam publicitaire." in log.details


def test_hors_sujet_sans_raison_donne_null():
    pipeline.evaluer_avis = lambda *args: verdict(hors_sujet=True, raison="")
    pipeline._recuperer_avis_pms = lambda: [avis_pms(1)]

    pipeline.run_pipeline()

    avis = tous_les_avis()[0]
    assert avis.flag_hors_sujet
    assert avis.raison_hors_sujet is None


def test_avis_normal_recoit_une_reponse():
    pipeline._recuperer_avis_pms = lambda: [avis_pms(1)]

    resume = pipeline.run_pipeline()

    avis = tous_les_avis()[0]
    assert resume["generes"] == 1
    assert avis.raison_hors_sujet is None
    assert avis.reponse_generee == "Merci."


# --- Requêtes de KPI de cdc-notes.md ---

def ajouter_avis_pour_les_kpi():
    ajouter_avis_en_base(10, statut_pipeline=StatutPipeline.VALIDE, reponse_generee="A", reponse_validee="A édité")
    ajouter_avis_en_base(11, statut_pipeline=StatutPipeline.SYNCHRONISE_PMS, reponse_generee="B", reponse_validee="B")
    ajouter_avis_en_base(12, statut_pipeline=StatutPipeline.VALIDE, reponse_validee="À la main", flag_doublon=True)
    ajouter_avis_en_base(13, statut_pipeline=StatutPipeline.EN_VALIDATION, reponse_generee="C")


def test_taux_de_retouche():
    ajouter_avis_pour_les_kpi()

    taux = executer_sql("""
        SELECT COUNT(CASE WHEN reponse_validee != reponse_generee THEN 1 END) * 100.0 / COUNT(*)
        FROM avis
        WHERE statut_pipeline IN ('VALIDE', 'SYNCHRONISE_PMS')
          AND reponse_generee IS NOT NULL
    """)

    assert taux == 50.0


def test_pourcentage_automatise():
    ajouter_avis_pour_les_kpi()

    taux = executer_sql("""
        SELECT COUNT(CASE WHEN nb_tentatives_regen = 0
                           AND flag_doublon = FALSE
                           AND flag_hors_sujet = FALSE
                           AND statut_erreur = 'OK'
                           THEN 1 END) * 100.0 / COUNT(*)
        FROM avis
    """)

    assert taux == 75.0
