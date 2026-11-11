"""Le juge LLM (eval/juge.py) avec un faux client OpenAI : lecture du verdict, réglages, retry."""
import json

import pytest

import juge
import llm_client
from llm_client import LLMError
from outils import attentes
from test_llm import faux_client


def sortie_du_juge(*criteres_en_defaut, **modifications):
    """Le texte JSON qu'enverrait le juge."""
    data = {critere: {"raison": f"raison {critere}", "defaut": critere in criteres_en_defaut}
            for critere in juge.PROMPT_JUGE["criteres"]}
    data.update(modifications)
    return json.dumps(data, ensure_ascii=False)


def juger():
    return juge.juger(2, "Chauffage en panne.", "Merci. Nous allons réparer le chauffage.")


def test_verdict_lu_du_premier_coup():
    llm_client.client, appels = faux_client(sortie_du_juge("promesse_action"))

    verdict = juger()

    assert verdict["promesse_action"] == {"defaut": True, "raison": "raison promesse_action"}
    assert verdict["aveu_faute"]["defaut"] is False
    assert list(verdict) == juge.PROMPT_JUGE["criteres"]
    assert len(appels) == 1


def test_le_juge_utilise_ses_propres_reglages():
    llm_client.client, appels = faux_client(sortie_du_juge())

    juger()

    assert appels[0]["model"] == "gpt-4o"
    assert appels[0]["temperature"] == 0
    assert appels[0]["response_format"] == {"type": "json_object"}


def test_le_juge_recoit_la_note_l_avis_et_la_reponse():
    llm_client.client, appels = faux_client(sortie_du_juge())

    juger()

    message = appels[0]["messages"][1]["content"]
    assert "2/10" in message
    assert "Chauffage en panne." in message
    assert "Nous allons réparer le chauffage." in message


def test_un_critere_manquant_declenche_un_nouvel_essai():
    incomplet = json.dumps({"promesse_action": {"raison": "r", "defaut": False}})
    llm_client.client, appels = faux_client(incomplet, sortie_du_juge())

    juger()

    assert len(appels) == 2
    assert attentes == [2]


def test_un_defaut_qui_n_est_pas_un_booleen_declenche_un_nouvel_essai():
    mal_forme = sortie_du_juge(enquete={"raison": "r", "defaut": "oui"})
    llm_client.client, appels = faux_client(mal_forme, sortie_du_juge())

    juger()

    assert len(appels) == 2


def test_le_juge_echoue_apres_trois_sorties_invalides():
    llm_client.client, appels = faux_client("pas du json", "{}", "[]")

    with pytest.raises(LLMError) as erreur:
        juger()

    assert erreur.value.tentatives == 3
    assert attentes == [2, 4]


def test_le_juge_est_un_autre_modele_que_celui_de_l_agent():
    """Conseil de la doc d'Anthropic : ne pas noter ses propres réponses avec le même modèle."""
    assert juge.REGLAGES["model"] != llm_client.CONFIG["llm"]["model"]


def test_chaque_critere_est_decrit_dans_la_grille():
    assert all(critere in juge.PROMPT_JUGE["system"] for critere in juge.PROMPT_JUGE["criteres"])
