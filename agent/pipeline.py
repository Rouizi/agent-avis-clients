"""
agent/pipeline.py
Orchestration principale : récupère les avis du PMS, applique les garde-fous,
génère les réponses et les met en file de validation (agent_internal.db).

Lancement :
  CLI       : python pipeline.py              -> un run complet
              python pipeline.py --limite 20  -> traite au plus 20 avis (test sur un échantillon)
              python pipeline.py --rejouer    -> rejoue les avis en erreur
  API (n8n) : uvicorn pipeline:app --port 8001
              POST /run-pipeline (démarre en arrière-plan), GET /run-status
"""
import argparse
import json
import threading
import time
from datetime import date, datetime
from typing import Optional

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException

from db_models import (
    ActionLog, Acteur, Avis, Log, Plateforme,
    StatutErreur, StatutPipeline, get_session, init_db,
)
from guardrails import est_prioritaire, est_vide, evaluer_avis
from llm_client import CONFIG, LLMError, generer_reponse

engine = init_db()


# --- Utilitaires ---

def _log(session, review_id_pms: int, action: ActionLog, acteur: Acteur,
         details: Optional[str] = None) -> None:
    """Trace chaque action (exigence CDC section 7). Le commit est fait par l'appelant."""
    session.add(Log(review_id_pms=review_id_pms, action=action, acteur=acteur, details=details))


def _textes_connus(session, avant_id: Optional[int] = None) -> list[str]:
    """Commentaires déjà en base (pour la détection de doublons). Le premier avis est conservé."""
    query = session.query(Avis.texte_avis)
    if avant_id is not None:
        query = query.filter(Avis.id < avant_id)
    return [ligne[0] for ligne in query.all()]


def _recuperer_avis_pms() -> list[dict]:
    """
    GET /reviews?statut=en_attente_evaluation sur le PMS, avec retry (zone grise #7).
    Après le dernier échec, l'erreur remonte et le run s'arrête : aucun avis n'a encore été
    récupéré, ils seront repris au run suivant.
    """
    pms = CONFIG["pms"]
    max_retries = CONFIG["generation"]["max_retries"]

    for tentative in range(1, max_retries + 1):
        try:
            reponse = requests.get(
                f"{pms['base_url']}/reviews",
                params={"statut": "en_attente_evaluation"},
                timeout=pms["timeout_secondes"],
            )
            reponse.raise_for_status()
            return reponse.json()
        except requests.RequestException:
            if tentative == max_retries:
                raise
            time.sleep(2 ** tentative)  # attente progressive : 2s, 4s...


def _construire_avis(r: dict) -> Avis:
    """Transforme un avis du PMS en ligne de la base. Lève une erreur si l'avis est mal formé."""
    return Avis(
        review_id_pms=int(r["id"]),
        plateforme=Plateforme(r["plateforme"]),
        note=int(r["note"]),
        texte_avis=r["commentaire"],
        date_avis=date.fromisoformat(r["date_avis"]),
        statut_pipeline=StatutPipeline.NOUVEAU,
    )


def _rafraichir_priorites(session) -> None:
    """Recalcule la priorité délai des avis en attente : un avis vieillit d'un run à l'autre."""
    en_attente = session.query(Avis).filter(Avis.statut_pipeline != StatutPipeline.SYNCHRONISE_PMS)
    for avis in en_attente:
        avis.priorite_delai = est_prioritaire(avis.date_avis)
    session.commit()


# --- Traitement d'un avis ---

def _traiter(session, avis: Avis, commentaires_connus: list[str]) -> str:
    """
    Garde-fous puis génération pour un avis déjà enregistré en base.
    Renvoie "genere", "signale" (doublon / hors-sujet) ou "erreur".
    """
    try:
        verdict = evaluer_avis(avis.note, avis.texte_avis, avis.date_avis, commentaires_connus)
        avis.flag_surveillance = verdict["flag_surveillance"]
        avis.flag_doublon = verdict["flag_doublon"]
        avis.flag_hors_sujet = verdict["flag_hors_sujet"]
        avis.raison_hors_sujet = verdict["raison_hors_sujet"] or None
        avis.priorite_delai = verdict["priorite_delai"]

        if avis.flag_doublon or avis.flag_hors_sujet:
            # Doublon et/ou hors-sujet : revue manuelle, pas de réponse générée
            motifs = " + ".join(
                nom for nom, actif in (("doublon", avis.flag_doublon),
                                       ("hors_sujet", avis.flag_hors_sujet)) if actif)
            avis.statut_pipeline = StatutPipeline.EN_VALIDATION
            avis.nb_tentatives_erreur = 0
            # Cas du rejeu : une régénération échouée laisse une ancienne réponse rejetée.
            # On l'efface (elle reste dans les logs), sinon l'avis signalé apparaîtrait dans
            # la file de validation au lieu de la revue manuelle (reponse_generee doit être NULL).
            avis.reponse_generee = None
            avis.score_confiance = None
            details = f"{motifs} : revue manuelle, aucune réponse générée"
            if verdict["raison_hors_sujet"]:
                details += f" | raison hors-sujet : {verdict['raison_hors_sujet']}"
            _log(session, avis.review_id_pms, ActionLog.SIGNALE, Acteur.AGENT, details)
            issue = "signale"
        else:
            # Rejeu après une régénération échouée : l'ancienne réponse reste le contre-exemple
            resultat = generer_reponse(avis.note, avis.plateforme.value, avis.texte_avis,
                                       reponse_rejetee=avis.reponse_generee)
            avis.reponse_generee = resultat["reponse"]
            avis.score_confiance = resultat["score_confiance"]
            avis.prompt_version = CONFIG["llm"]["prompt_version"]
            avis.statut_pipeline = StatutPipeline.EN_VALIDATION
            avis.nb_tentatives_erreur = resultat["tentatives"] - 1  # échecs avant le succès
            _log(session, avis.review_id_pms, ActionLog.GENERE, Acteur.AGENT,
                 f"prompt={avis.prompt_version}, score_confiance={resultat['score_confiance']}, "
                 f"tentatives={resultat['tentatives']}")
            issue = "genere"
        avis.statut_erreur = StatutErreur.OK

    except LLMError as e:
        # Après les 3 retries : l'avis part en file d'erreur, traité manuellement ou rejoué
        avis.statut_erreur = StatutErreur.EN_ERREUR
        avis.nb_tentatives_erreur = e.tentatives
        _log(session, avis.review_id_pms, ActionLog.ERREUR_FINALE, Acteur.AGENT, str(e))
        issue = "erreur"

    session.commit()
    return issue


# --- Runs ---

def run_pipeline(limite: Optional[int] = None) -> dict:
    """
    Un run complet : récupération, filtrage, garde-fous, génération. Renvoie un résumé.
    limite : nombre maximal d'avis traités (reprises comprises), pour tester sur un échantillon.
    """
    debut = datetime.utcnow()
    resume = {"repris": 0, "recuperes": 0, "deja_traites": 0, "vides": 0, "invalides": 0,
              "generes": 0, "signales": 0, "erreurs": 0}
    cle_resume = {"genere": "generes", "signale": "signales", "erreur": "erreurs"}

    session = get_session(engine)
    try:
        _rafraichir_priorites(session)

        # Reprise après crash : avis enregistrés lors d'un run précédent mais jamais traités
        a_reprendre = session.query(Avis).filter(
            Avis.statut_pipeline == StatutPipeline.NOUVEAU,
            Avis.statut_erreur == StatutErreur.OK,
        ).order_by(Avis.id).limit(limite).all()
        for avis in a_reprendre:
            issue = _traiter(session, avis, _textes_connus(session, avant_id=avis.id))
            resume["repris"] += 1
            resume[cle_resume[issue]] += 1
        traites = len(a_reprendre)

        avis_pms = _recuperer_avis_pms()
        resume["recuperes"] = len(avis_pms)

        deja_en_base = {ligne[0] for ligne in session.query(Avis.review_id_pms).all()}
        connus = _textes_connus(session)

        # Les plus anciens d'abord : ce sont les plus proches de la limite de publication
        for r in sorted(avis_pms, key=lambda x: str(x.get("date_avis", ""))):
            try:
                if r.get("id") in deja_en_base:         # F4 : jamais deux fois le même avis
                    resume["deja_traites"] += 1
                    continue
                if est_vide(r.get("commentaire")):      # CDC 3.2 : note sans commentaire
                    resume["vides"] += 1
                    continue
                avis = _construire_avis(r)
            except (KeyError, ValueError, TypeError, AttributeError):
                # Avis mal formé : il ne doit pas bloquer les suivants, à ce run comme aux prochains
                resume["invalides"] += 1
                continue
            if limite is not None and traites >= limite:
                continue                                # on parcourt la suite : compteurs du résumé exacts

            session.add(avis)
            session.commit()  # l'avis est enregistré avant traitement : reprise possible en cas de crash

            issue = _traiter(session, avis, connus)
            resume[cle_resume[issue]] += 1
            connus.append(r["commentaire"])
            traites += 1
    finally:
        session.close()

    resume["duree_secondes"] = round((datetime.utcnow() - debut).total_seconds(), 1)
    return resume


def rejouer_erreurs() -> dict:
    """Rejoue les avis tombés en file d'erreur (reprise sur erreur, CDC section 6)."""
    resume = {"a_rejouer": 0, "generes": 0, "signales": 0, "erreurs": 0}
    cle_resume = {"genere": "generes", "signale": "signales", "erreur": "erreurs"}

    session = get_session(engine)
    try:
        en_erreur = session.query(Avis).filter(Avis.statut_erreur == StatutErreur.EN_ERREUR).all()
        resume["a_rejouer"] = len(en_erreur)
        for avis in en_erreur:
            issue = _traiter(session, avis, _textes_connus(session, avant_id=avis.id))
            resume[cle_resume[issue]] += 1
    finally:
        session.close()
    return resume


def regenerer(session, avis: Avis) -> bool:
    """
    Appelé par Flask quand le relecteur REJETTE une réponse (zone grise #2).
    Régénère avec l'ancienne réponse en contre-exemple. Renvoie False dans deux cas :
      - limite de régénérations atteinte : l'avis passe en file "à traiter manuellement" ;
      - génération en échec : l'avis part en file d'erreur, et reste rejouable.
    """
    _log(session, avis.review_id_pms, ActionLog.REJETE, Acteur.HUMAIN,
         f"Réponse rejetée : {avis.reponse_generee}")

    if (not avis.reponse_generee
            or avis.nb_tentatives_regen >= CONFIG["generation"]["max_regenerations"]):
        avis.statut_pipeline = StatutPipeline.A_TRAITER_MANUELLEMENT
        session.commit()
        return False

    try:
        resultat = generer_reponse(avis.note, avis.plateforme.value, avis.texte_avis,
                                   reponse_rejetee=avis.reponse_generee)
    except LLMError as e:
        avis.statut_erreur = StatutErreur.EN_ERREUR
        avis.nb_tentatives_erreur = e.tentatives
        _log(session, avis.review_id_pms, ActionLog.ERREUR_FINALE, Acteur.AGENT, str(e))
        session.commit()
        return False

    avis.nb_tentatives_regen += 1
    avis.reponse_generee = resultat["reponse"]
    avis.score_confiance = resultat["score_confiance"]
    avis.prompt_version = CONFIG["llm"]["prompt_version"]
    _log(session, avis.review_id_pms, ActionLog.REGENERE, Acteur.AGENT,
         f"régénération {avis.nb_tentatives_regen}/{CONFIG['generation']['max_regenerations']}, "
         f"prompt={avis.prompt_version}, score_confiance={resultat['score_confiance']}")
    session.commit()
    return True


# --- API pour n8n ---

app = FastAPI(title="Agent avis clients")
_verrou = threading.Lock()  # un seul run à la fois
ETAT = {"statut": "jamais_lance"}


def _executer(fonction) -> None:
    try:
        ETAT.update({"statut": "termine", "resume": fonction()})
    except Exception as e:  # noqa: BLE001 : on veut tout capturer pour le statut
        ETAT.update({"statut": "echec", "erreur": str(e)})
    finally:
        ETAT["fin"] = datetime.utcnow().isoformat()
        _verrou.release()


def _lancer(background: BackgroundTasks, fonction) -> dict:
    if not _verrou.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Un run est déjà en cours")
    # Avant de répondre : la tâche ne démarre qu'après la réponse, et /run-status afficherait encore le run précédent
    ETAT.clear()
    ETAT.update({"statut": "en_cours", "debut": datetime.utcnow().isoformat()})
    background.add_task(_executer, fonction)
    return {"statut": "demarre"}


@app.post("/run-pipeline", status_code=202)
def api_run_pipeline(background: BackgroundTasks):
    """Appelé par n8n chaque lundi. Répond tout de suite : le run (plusieurs minutes) tourne en arrière-plan."""
    return _lancer(background, run_pipeline)


@app.post("/rejouer-erreurs", status_code=202)
def api_rejouer_erreurs(background: BackgroundTasks):
    return _lancer(background, rejouer_erreurs)


@app.get("/run-status")
def api_run_status():
    return ETAT


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de traitement des avis clients")
    parser.add_argument("--rejouer", action="store_true", help="rejouer les avis en erreur")
    parser.add_argument("--limite", type=int, help="nombre maximal d'avis à traiter pendant ce run")
    args = parser.parse_args()
    if args.limite is not None and args.limite < 1:
        parser.error("--limite doit être supérieur ou égal à 1")
    if args.limite is not None and args.rejouer:
        parser.error("--limite ne se combine pas avec --rejouer")
    resultat = rejouer_erreurs() if args.rejouer else run_pipeline(limite=args.limite)
    print(json.dumps(resultat, ensure_ascii=False, indent=2))
