"""
agent/llm_client.py
Génère une réponse à un avis : charge la config et le prompt versionné,
appelle le LLM, valide le JSON reçu, réessaie en cas d'échec (zone grise #7).
Nécessite OPENAI_API_KEY dans l'environnement.
"""
import json
import time
from pathlib import Path
from typing import Optional

import yaml
from openai import OpenAI, OpenAIError

BASE_DIR = Path(__file__).parent


def _charger_yaml(chemin: Path) -> dict:
    with open(chemin, encoding="utf-8") as f:
        return yaml.safe_load(f)


CONFIG = _charger_yaml(BASE_DIR / "config.yaml")
PROMPT = _charger_yaml(BASE_DIR / "prompts" / f"{CONFIG['llm']['prompt_version']}.yaml")

client = OpenAI()

class LLMError(Exception):
    """Échec définitif après tous les retries. Porte le nombre de tentatives effectuées."""

    def __init__(self, message: str, tentatives: int):
        super().__init__(message)
        self.tentatives = tentatives


def choisir_ton(note: int) -> str:
    """Renvoie la consigne de ton correspondant à la note (config.yaml > tons)."""
    for palier in CONFIG["tons"]:
        if palier["note_min"] <= note <= palier["note_max"]:
            return palier["ton"]
    raise ValueError(f"Aucun ton défini pour la note {note}")


def _appeler_llm(note: int, plateforme: str, commentaire: str,
                 reponse_rejetee: Optional[str]) -> dict:
    """Un seul appel LLM, sans retry. Lève une exception si la sortie est invalide."""
    message_user = PROMPT["user"].format(
        note=note,
        plateforme=plateforme,
        commentaire=commentaire,
        ton=choisir_ton(note),
    )
    if reponse_rejetee:
        message_user += (
            "\n\nUne première réponse a été rejetée par le relecteur. "
            "Propose une formulation sensiblement différente :\n" + reponse_rejetee
        )

    llm = CONFIG["llm"]
    response = client.chat.completions.create(
        model=llm["model"],
        temperature=llm["temperature"],
        max_tokens=llm["max_tokens"],
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": PROMPT["system"]},
            {"role": "user", "content": message_user},
        ],
    )

    data = json.loads(response.choices[0].message.content)
    reponse = str(data["reponse"]).strip()
    score = int(data["score_confiance"])
    if not reponse:
        raise ValueError("Réponse vide")

    return {"reponse": reponse, "score_confiance": max(0, min(100, score))}


def generer_reponse(note: int, plateforme: str, commentaire: str,
                    reponse_rejetee: Optional[str] = None) -> dict:
    """
    Génère la réponse + le score de confiance, avec retry (max_retries dans la config).
    Renvoie {"reponse": str, "score_confiance": int, "tentatives": int}.
    Lève LLMError après épuisement des tentatives (l'avis part alors en file d'erreur).
    """
    if not commentaire:
        # Les avis vides sont exclus en amont (CDC 3.2) : inutile de réessayer
        raise ValueError("Impossible de générer une réponse sans commentaire")

    max_retries = CONFIG["generation"]["max_retries"]
    derniere_erreur = None

    for tentative in range(1, max_retries + 1):
        try:
            resultat = _appeler_llm(note, plateforme, commentaire, reponse_rejetee)
            resultat["tentatives"] = tentative
            return resultat
        except (OpenAIError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            derniere_erreur = e
            if tentative < max_retries:
                time.sleep(2 ** tentative)  # attente progressive : 2s, 4s...

    raise LLMError(
        f"Échec après {max_retries} tentatives : {derniere_erreur}",
        tentatives=max_retries,
    )


if __name__ == "__main__":
    # Test rapide en CLI : python llm_client.py
    test = generer_reponse(
        note=3,
        plateforme="airbnb",
        commentaire="Le chauffage était en panne et personne n'a répondu à nos messages.",
    )
    print(json.dumps(test, ensure_ascii=False, indent=2))
