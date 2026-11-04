"""Retry des appels au LLM : génération et classifieur hors-sujet, avec un faux client OpenAI."""
import json
from types import SimpleNamespace

import pytest

import guardrails
import llm_client
import outils
from llm_client import CONFIG, LLMError
from outils import attentes

BONNE_REPONSE = json.dumps({"reponse": "Merci pour votre retour.", "score_confiance": 150})
BON_VERDICT = json.dumps({"hors_sujet": True, "raison": "Parle d'un colis."})


def faux_client(*contenus):
    """Imite le client OpenAI : chaque appel renvoie le texte suivant. Renvoie le client et la liste des appels."""
    appels = []

    def create(**kwargs):
        contenu = contenus[len(appels)]
        appels.append(kwargs)
        message = SimpleNamespace(content=contenu)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completions = SimpleNamespace(create=create)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), appels


def generer():
    return llm_client.generer_reponse(8, "airbnb", "Très bon séjour.")


# --- Génération ---

def test_generation_valide_du_premier_coup():
    llm_client.client, appels = faux_client(BONNE_REPONSE)

    resultat = generer()

    assert resultat["reponse"] == "Merci pour votre retour."
    assert resultat["tentatives"] == 1
    assert len(appels) == 1


def test_score_de_confiance_ramene_entre_0_et_100():
    llm_client.client, _ = faux_client(BONNE_REPONSE)  # le LLM a répondu 150

    assert generer()["score_confiance"] == 100


def test_generation_reessaie_apres_un_json_invalide():
    llm_client.client, _ = faux_client("pas du json", BONNE_REPONSE)

    resultat = generer()

    assert resultat["tentatives"] == 2
    assert attentes == [2]


def test_generation_echoue_apres_trois_sorties_invalides():
    llm_client.client, appels = faux_client("pas du json", '{"autre": 1}', '{"reponse": "", "score_confiance": 50}')

    with pytest.raises(LLMError) as erreur:
        generer()

    assert erreur.value.tentatives == 3
    assert len(appels) == 3
    assert attentes == [2, 4]


def test_reponse_null_declenche_un_nouvel_essai():
    llm_client.client, appels = faux_client('{"reponse": null, "score_confiance": 80}', BONNE_REPONSE)

    resultat = generer()

    assert resultat["reponse"] == "Merci pour votre retour."
    assert len(appels) == 2


def test_reponse_qui_n_est_pas_du_texte_declenche_un_nouvel_essai():
    llm_client.client, appels = faux_client('{"reponse": {"a": 1}, "score_confiance": 80}', BONNE_REPONSE)

    assert generer()["reponse"] == "Merci pour votre retour."
    assert len(appels) == 2


def test_score_booleen_declenche_un_nouvel_essai():
    llm_client.client, appels = faux_client('{"reponse": "Merci.", "score_confiance": true}', BONNE_REPONSE)

    assert generer()["score_confiance"] == 100
    assert len(appels) == 2


def test_reponse_null_trois_fois_finit_en_erreur():
    null = '{"reponse": null, "score_confiance": 80}'
    llm_client.client, _ = faux_client(null, null, null)

    with pytest.raises(LLMError):
        generer()


def test_client_openai_sans_retry_cache_et_avec_timeout():
    assert outils.client_openai_reel.max_retries == 0
    assert outils.client_openai_reel.timeout == CONFIG["llm"]["timeout_secondes"]


def test_reponse_rejetee_ajoutee_au_message():
    llm_client.client, appels = faux_client(BONNE_REPONSE)

    llm_client.generer_reponse(8, "airbnb", "Très bon séjour.", reponse_rejetee="Ancienne réponse")

    assert "Ancienne réponse" in appels[0]["messages"][1]["content"]


# --- Classifieur hors-sujet ---

def test_classifieur_renvoie_le_verdict_et_la_raison():
    guardrails.client, _ = faux_client(BON_VERDICT)

    assert guardrails.est_hors_sujet("Mon colis est perdu.") == (True, "Parle d'un colis.")


def test_classifieur_raison_null_reste_vide():
    guardrails.client, _ = faux_client('{"hors_sujet": true, "raison": null}')

    assert guardrails.est_hors_sujet("Mon colis est perdu.") == (True, "")


def test_classifieur_utilise_les_reglages_de_la_config():
    guardrails.client, appels = faux_client(BON_VERDICT)

    guardrails.est_hors_sujet("Mon colis est perdu.")

    assert appels[0]["temperature"] == CONFIG["llm"]["temperature_hors_sujet"]
    assert appels[0]["max_tokens"] == CONFIG["llm"]["max_tokens_hors_sujet"]


def test_classifieur_reessaie_si_le_verdict_n_est_pas_un_booleen():
    guardrails.client, appels = faux_client('{"hors_sujet": "oui"}', BON_VERDICT)

    assert guardrails.est_hors_sujet("Mon colis est perdu.")[0] is True
    assert len(appels) == 2


def test_classifieur_echoue_apres_trois_sorties_invalides():
    guardrails.client, appels = faux_client("pas du json", "pas du json", "pas du json")

    with pytest.raises(LLMError):
        guardrails.est_hors_sujet("Mon colis est perdu.")

    assert len(appels) == 3
