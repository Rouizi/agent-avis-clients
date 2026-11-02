"""
agent/guardrails.py
Garde-fous appliqués à chaque avis avant la génération :
  - très négatif (note <= seuil) -> flag "à surveiller"        (zone grise #3)
  - doublon (similarité, règles) -> file de revue manuelle     (zone grise #5)
  - hors-sujet (LLM classifieur) -> file de revue manuelle     (zone grise #5)
  - avis proche des 14 jours     -> priorité dans la file      (zone grise #8)
Les avis vides (CDC 3.2) sont écartés en amont par le pipeline, avec est_vide.
Tous les seuils viennent de config.yaml.
"""
import json
import re
import time
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Optional

from openai import OpenAIError

from llm_client import BASE_DIR, CONFIG, LLMError, _charger_yaml, client

PROMPT_HORS_SUJET = _charger_yaml(
    BASE_DIR / "prompts" / f"{CONFIG['llm']['prompt_hors_sujet']}.yaml"
)


# --- Règles simples (sans LLM) ---

def est_vide(commentaire: Optional[str]) -> bool:
    """Note sans commentaire : l'avis est exclu du traitement."""
    return not commentaire or not commentaire.strip()


def est_tres_negatif(note: int) -> bool:
    return note <= CONFIG["seuils"]["note_tres_negatif"]


def est_prioritaire(date_avis: date) -> bool:
    """Vrai si l'avis approche de la limite de publication (ex : 14 jours sur Airbnb)."""
    age_jours = (date.today() - date_avis).days
    return age_jours >= CONFIG["seuils"]["delai_priorite_jours"]


@lru_cache(maxsize=None)
def _normaliser(texte: str) -> str:
    """Minuscules, sans accents ni ponctuation, espaces réduits."""
    texte = unicodedata.normalize("NFKD", texte.lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    texte = re.sub(r"[^\w\s]", " ", texte)
    return re.sub(r"\s+", " ", texte).strip()


def est_doublon(commentaire: str, commentaires_connus: list[str]) -> bool:
    """
    Compare le commentaire aux commentaires déjà vus (même lot + avis déjà traités).
    Le premier avis est conservé, les suivants sont marqués doublons.
    """
    seuil = CONFIG["seuils"]["similarite_doublon"]
    candidat = _normaliser(commentaire)

    # Trop court : deux voyageurs peuvent légitimement écrire "Super séjour !"
    if len(candidat) < CONFIG["seuils"]["doublon_longueur_min"]:
        return False

    for autre in commentaires_connus:
        autre_norm = _normaliser(autre)
        total = len(candidat) + len(autre_norm)
        # Borne haute du ratio : si les longueurs sont trop différentes, inutile de comparer
        if total == 0 or 2 * min(len(candidat), len(autre_norm)) / total < seuil:
            continue
        if SequenceMatcher(None, candidat, autre_norm).ratio() >= seuil:
            return True
    return False


# --- Classifieur LLM ---

def est_hors_sujet(commentaire: str) -> tuple[bool, str]:
    """
    Appel LLM classifieur, avec retry (max_retries dans la config).
    Renvoie (hors_sujet, raison) : la raison est une phrase courte, utile à la traçabilité.
    Lève LLMError après épuisement des tentatives (l'avis part en file d'erreur).
    """
    max_retries = CONFIG["generation"]["max_retries"]
    derniere_erreur = None

    for tentative in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=CONFIG["llm"]["model"],
                temperature=0,
                max_tokens=100,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": PROMPT_HORS_SUJET["system"]},
                    {"role": "user",
                     "content": PROMPT_HORS_SUJET["user"].format(commentaire=commentaire)},
                ],
            )
            data = json.loads(response.choices[0].message.content)
            if not isinstance(data["hors_sujet"], bool):
                raise ValueError("Le champ hors_sujet doit être un booléen")
            return data["hors_sujet"], str(data.get("raison", "")).strip()
        except (OpenAIError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            derniere_erreur = e
            if tentative < max_retries:
                time.sleep(2 ** tentative)

    raise LLMError(
        f"Classifieur hors-sujet : échec après {max_retries} tentatives : {derniere_erreur}",
        tentatives=max_retries,
    )


# --- Point d'entrée unique pour le pipeline ---

def evaluer_avis(note: int, commentaire: str, date_avis: date,
                 commentaires_connus: list[str]) -> dict:
    """
    Applique tous les garde-fous à un avis non vide (l'appelant écarte les avis vides). Renvoie :
      flag_surveillance : True si très négatif
      flag_doublon      : True si quasi identique à un avis déjà vu
      flag_hors_sujet   : True si le classifieur LLM le juge hors sujet
      raison_hors_sujet : phrase du classifieur expliquant le verdict (None si pas hors-sujet)
      priorite_delai    : True si l'avis approche de la limite de publication
    Les flags sont indépendants : un avis peut être doublon ET hors-sujet (ex : spam répété).
    Le classifieur LLM est donc appelé pour tout avis.
    """
    hors_sujet, raison = est_hors_sujet(commentaire)
    return {
        "flag_surveillance": est_tres_negatif(note),
        "flag_doublon": est_doublon(commentaire, commentaires_connus),
        "flag_hors_sujet": hors_sujet,
        "raison_hors_sujet": raison if hors_sujet else None,
        "priorite_delai": est_prioritaire(date_avis),
    }


if __name__ == "__main__":
    # Auto-test : python guardrails.py
    from datetime import timedelta

    # Règles sans LLM
    assert est_vide(None) and est_vide("   ") and not est_vide("Super")
    assert est_tres_negatif(3) and not est_tres_negatif(4)
    assert est_prioritaire(date.today() - timedelta(days=12))
    assert not est_prioritaire(date.today() - timedelta(days=2))

    base = "Appartement très propre, accueil chaleureux, on reviendra avec plaisir !"
    assert est_doublon("Appartement très propre, accueil chaleureux, on reviendra avec plaisir", [base])
    assert est_doublon("APPARTEMENT TRES PROPRE accueil chaleureux on reviendra avec plaisir!!", [base])
    assert not est_doublon("Logement bruyant et mal isolé, nuits difficiles.", [base])
    assert not est_doublon("Super séjour !", ["Super séjour !"])  # trop court
    print("Règles sans LLM : OK")

    # Classifieur LLM (nécessite OPENAI_API_KEY valide)
    import os
    if os.environ.get("OPENAI_API_KEY", "").startswith("sk-"):
        assert est_hors_sujet("Le transporteur a perdu mon colis, service lamentable.")[0]
        assert not est_hors_sujet("Appartement sale et bruyant, jamais revenir, inacceptable.")[0]
        print("Classifieur hors-sujet : OK")
    else:
        print("Classifieur hors-sujet : non testé (pas de clé OPENAI_API_KEY)")
