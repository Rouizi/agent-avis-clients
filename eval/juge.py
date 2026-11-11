"""
eval/juge.py
Le juge LLM : un second modèle lit l'avis et la réponse de l'agent, puis rend un verdict
par critère de sa grille (eval/prompts/). Utilisé par run_eval.py.
Les réglages (modèle, température, version de la grille) sont dans criteres.yaml.
"""
import json
import time

import yaml
from openai import OpenAIError

import llm_client
from llm_client import LLMError
from metriques import CRITERES, DOSSIER

REGLAGES = CRITERES["juge"]

with open(DOSSIER / "prompts" / f"{REGLAGES['prompt_version']}.yaml", encoding="utf-8") as f:
    PROMPT_JUGE = yaml.safe_load(f)


def _lire_verdict(contenu):
    """Vérifie la sortie du juge. Renvoie {critère: {"defaut": bool, "raison": str}}."""
    data = json.loads(contenu)
    verdict = {}
    for critere in PROMPT_JUGE["criteres"]:
        avis_du_juge = data[critere]
        if not isinstance(avis_du_juge["defaut"], bool):
            raise ValueError(f"{critere} : le champ defaut doit être un booléen")
        raison = avis_du_juge.get("raison")
        verdict[critere] = {
            "defaut": avis_du_juge["defaut"],
            "raison": raison.strip() if isinstance(raison, str) else "",
        }
    return verdict


def juger(note, commentaire, reponse):
    """
    Renvoie le verdict du juge sur une réponse.
    Lève LLMError après max_essais échecs : run_eval note l'erreur et continue.
    """
    derniere_erreur = None
    for essai in range(1, REGLAGES["max_essais"] + 1):
        try:
            # llm_client.client est lu à chaque appel : les tests peuvent le remplacer
            response = llm_client.client.chat.completions.create(
                model=REGLAGES["model"],
                temperature=REGLAGES["temperature"],
                max_tokens=REGLAGES["max_tokens"],
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": PROMPT_JUGE["system"]},
                    {"role": "user", "content": PROMPT_JUGE["user"].format(
                        note=note, commentaire=commentaire, reponse=reponse)},
                ],
            )
            return _lire_verdict(response.choices[0].message.content)
        except (OpenAIError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            derniere_erreur = e
            if essai < REGLAGES["max_essais"]:
                time.sleep(2 ** essai)

    raise LLMError(
        f"Juge : échec après {REGLAGES['max_essais']} essais : {derniere_erreur}",
        tentatives=REGLAGES["max_essais"],
    )
