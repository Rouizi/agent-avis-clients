"""Le jeu de test (eval/dataset.json) est cohérent, et run_eval fonctionne avec un faux LLM."""
import json

import pytest

import guardrails
import juge
import llm_client
import metriques
import run_eval
from llm_client import LLMError

CAS = run_eval.charger_cas()
CAS_AVEC_REPONSE = [cas for cas in CAS if not cas["hors_sujet_attendu"]]


def un_seul_cas(identifiant):
    return [cas for cas in CAS if cas["id"] == identifiant][0]


def categorie(nom):
    return [cas for cas in CAS if cas["categorie"] == nom]


# --- Le jeu de test ---

def test_identifiants_uniques():
    identifiants = [cas["id"] for cas in CAS]

    assert len(identifiants) == len(set(identifiants))


def test_toutes_les_categories_sont_presentes():
    assert {cas["categorie"] for cas in CAS} == {
        "mixte", "hors_sujet", "classique", "negatif_risque", "tres_court",
        "long", "contradiction", "manipulation", "autre_langue",
    }


def test_l_avis_reel_sur_les_courses_est_dans_le_jeu():
    assert "livraison de mes courses" in un_seul_cas("mixte_courses")["commentaire"]
    assert un_seul_cas("mixte_courses")["hors_sujet_attendu"] is False


def test_tous_les_paliers_de_note_sont_couverts():
    paliers = {metriques.palier_de_note(cas["note"]) for cas in CAS_AVEC_REPONSE}

    assert paliers == {"1-4", "5-6", "7-8", "9-10"}


def test_les_avis_longs_depassent_600_caracteres():
    assert all(len(cas["commentaire"]) > 600 for cas in categorie("long"))


def test_les_avis_tres_courts_font_moins_de_15_caracteres():
    assert all(len(cas["commentaire"]) < 15 for cas in categorie("tres_court"))


def test_chaque_avis_a_traiter_a_une_reponse_attendue():
    sans_reponse = [cas["id"] for cas in CAS_AVEC_REPONSE if not cas["reponse_attendue"]]

    assert sans_reponse == []


def test_les_vrais_hors_sujet_n_ont_pas_de_reponse_attendue():
    assert all(cas["reponse_attendue"] is None for cas in categorie("hors_sujet"))


def test_les_reponses_attendues_passent_nos_propres_criteres():
    """Sinon, c'est la référence elle-même qui serait fausse."""
    en_defaut = {cas["id"]: metriques.defauts(cas["reponse_attendue"], cas)
                 for cas in CAS_AVEC_REPONSE
                 if metriques.defauts(cas["reponse_attendue"], cas)}

    assert en_defaut == {}


def test_aucun_avis_du_jeu_ne_figure_dans_les_prompts():
    """Copier un cas de test dans un prompt gonflerait artificiellement les résultats."""
    prompts = (llm_client.PROMPT["system"] + guardrails.PROMPT_HORS_SUJET["system"]
               + juge.PROMPT_JUGE["system"])

    copies = [cas["id"] for cas in CAS if cas["commentaire"] in prompts]

    assert copies == []


# --- run_eval avec un faux LLM ---

CAS_SIMPLE = {
    "id": "simple", "categorie": "classique", "note": 8, "plateforme": "airbnb",
    "commentaire": "Belle terrasse.", "hors_sujet_attendu": False,
    "reponse_attendue": "Merci pour votre retour. Nous sommes ravis que la terrasse vous ait plu.",
    "details_attendus": ["terrasse"], "interdits": [], "score_min": 75, "score_max": 100,
}
CAS_SPAM = {
    "id": "spam", "categorie": "hors_sujet", "note": 10, "plateforme": "airbnb",
    "commentaire": "Achetez ma crypto.", "hors_sujet_attendu": True,
    "reponse_attendue": None, "details_attendus": [], "interdits": [],
    "score_min": None, "score_max": None,
}
BONNE_REPONSE = "Merci pour votre retour. Nous sommes ravis que la terrasse vous ait plu."


def faux_generer(note, plateforme, commentaire):
    return {"reponse": BONNE_REPONSE, "score_confiance": 80, "tentatives": 1}


def faux_generer_en_panne(note, plateforme, commentaire):
    raise LLMError("OpenAI en panne", tentatives=3)


def faux_classifieur_valable(commentaire):
    return False, "Parle du séjour."


def faux_classifieur_hors_sujet(commentaire):
    return True, "Sans lien avec le séjour."


def faux_classifieur_en_panne(commentaire):
    raise LLMError("OpenAI en panne", tentatives=3)


def verdict_juge(*criteres_en_defaut):
    return {critere: {"defaut": critere in criteres_en_defaut, "raison": f"raison {critere}"}
            for critere in juge.PROMPT_JUGE["criteres"]}


def faux_juge_sans_reproche(note, commentaire, reponse):
    return verdict_juge()


def faux_juge_promesse(note, commentaire, reponse):
    return verdict_juge("promesse_action")


def faux_juge_en_panne(note, commentaire, reponse):
    raise LLMError("OpenAI en panne", tentatives=3)


@pytest.fixture(autouse=True)
def _juge_sans_reproche_par_defaut(monkeypatch):
    monkeypatch.setattr(run_eval, "juger", faux_juge_sans_reproche)


def test_un_avis_recoit_trois_reponses(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_valable)

    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=3)

    assert len(resultat["essais"]) == 3
    assert resultat["essais"][0]["defauts"] == []
    assert resultat["essais"][0]["dans_la_fourchette"] is True


def test_un_vrai_hors_sujet_ne_recoit_pas_de_reponse(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_hors_sujet)

    resultat = run_eval.evaluer_cas(CAS_SPAM, nb_essais=3)

    assert resultat["essais"] == []
    assert resultat["hors_sujet_obtenu"] is True


def test_un_avis_faussement_classe_hors_sujet_recoit_quand_meme_ses_reponses(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_hors_sujet)

    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=3)

    assert len(resultat["essais"]) == 3
    assert resultat["hors_sujet_obtenu"] is True


def test_une_panne_de_generation_est_notee_sans_tout_arreter(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer_en_panne)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_valable)

    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=3)
    resume = run_eval.resumer([resultat], nb_essais=3)

    assert resume["nb_erreurs_generation"] == 3
    assert resume["nb_reponses"] == 0


def test_une_panne_du_classifieur_est_notee_sans_tout_arreter(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_en_panne)

    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=3)
    resume = run_eval.resumer([resultat], nb_essais=3)

    assert resume["hors_sujet"]["nb_erreurs_techniques"] == 1
    assert resume["nb_reponses"] == 3


def test_le_rapport_indique_la_version_du_prompt(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_valable)
    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=3)

    rapport = run_eval.construire_rapport([resultat], nb_essais=3)

    assert rapport["prompt_version"] == llm_client.CONFIG["llm"]["prompt_version"]
    assert rapport["nb_essais"] == 3


def test_le_rapport_est_ecrit_en_json(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_valable)
    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=3)
    rapport = run_eval.construire_rapport([resultat], nb_essais=3)

    chemin = run_eval.ecrire_rapport(rapport, dossier=tmp_path)

    assert json.loads(chemin.read_text(encoding="utf-8"))["resume"]["nb_reponses"] == 3


def test_le_resume_est_ecrit_a_cote_du_rapport(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_valable)
    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=3)
    rapport = run_eval.construire_rapport([resultat], nb_essais=3)

    chemin = run_eval.ecrire_rapport(rapport, dossier=tmp_path)

    resume = chemin.with_suffix(".txt").read_text(encoding="utf-8")
    assert resume == run_eval.texte_du_resume(rapport)


def test_le_resume_se_construit_meme_si_tout_est_en_panne(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer_en_panne)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_en_panne)
    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=3)

    texte = run_eval.texte_du_resume(run_eval.construire_rapport([resultat], nb_essais=3))

    assert "3 erreurs" in texte


def test_le_jeu_de_test_complet_passe_avec_un_faux_llm(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_valable)

    resultats = [run_eval.evaluer_cas(cas, nb_essais=3) for cas in CAS]
    resume = run_eval.resumer(resultats, nb_essais=3)

    assert resume["nb_reponses"] == 3 * len(CAS_AVEC_REPONSE)
    assert len(resume["hors_sujet"]["faux_negatifs"]) == len(categorie("hors_sujet"))


# --- Le juge dans run_eval ---

def test_le_juge_recoit_la_note_l_avis_et_la_reponse(monkeypatch):
    recus = []
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_valable)
    monkeypatch.setattr(run_eval, "juger", lambda *args: recus.append(args) or verdict_juge())

    run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=1)

    assert recus == [(8, "Belle terrasse.", BONNE_REPONSE)]


def test_un_defaut_vu_par_le_juge_est_compte(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_valable)
    monkeypatch.setattr(run_eval, "juger", faux_juge_promesse)

    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=3)
    resume = run_eval.resumer([resultat], nb_essais=3)

    assert resultat["essais"][0]["defauts"] == ["juge:promesse_action"]
    assert resume["part_reponses_sans_defaut"] == 0
    assert resume["juge"]["nb_reponses_par_critere"]["promesse_action"] == 3
    assert resume["juge"]["passages"][0] == {"id": "simple", "critere": "promesse_action",
                                             "raison": "raison promesse_action"}


def test_une_panne_du_juge_garde_la_reponse_mais_l_exclut_du_pourcentage(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_valable)
    monkeypatch.setattr(run_eval, "juger", faux_juge_en_panne)

    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=3)
    resume = run_eval.resumer([resultat], nb_essais=3)

    assert resume["nb_reponses"] == 3
    assert resume["nb_erreurs_juge"] == 3
    assert resume["part_reponses_sans_defaut"] is None
    assert "OpenAI en panne" in resultat["essais"][0]["erreur_juge"]


def test_le_rapport_indique_le_juge(monkeypatch):
    monkeypatch.setattr(run_eval, "generer_reponse", faux_generer)
    monkeypatch.setattr(run_eval, "est_hors_sujet", faux_classifieur_valable)
    resultat = run_eval.evaluer_cas(CAS_SIMPLE, nb_essais=1)

    rapport = run_eval.construire_rapport([resultat], nb_essais=1)

    assert rapport["modele_juge"] == "gpt-4o"
    assert rapport["prompt_juge"] == "juge_v1"
