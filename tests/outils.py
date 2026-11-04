"""Petites fonctions partagées par les tests."""
from datetime import date, timedelta

import db_models
import main
import models
import pipeline
from db_models import Avis, Log, Plateforme
from sqlalchemy import text

appels_llm = []  # les commentaires envoyés au faux LLM, dans l'ordre
attentes = []    # les durées demandées à time.sleep, dans l'ordre


# --- Faux ---

def faux_generer(note, plateforme, commentaire, **options):
    appels_llm.append(commentaire)
    return {"reponse": "Merci.", "score_confiance": 80, "tentatives": 1}


def faux_evaluer_sans_flag(*args, **kwargs):
    return verdict()


def verdict(hors_sujet=False, raison=None):
    return {"flag_surveillance": False, "flag_doublon": False, "flag_hors_sujet": hors_sujet,
            "raison_hors_sujet": raison, "priorite_delai": False}


# --- Avis renvoyés par le PMS ---

def avis_pms_avec_texte(identifiant, commentaire):
    """Plus l'identifiant est grand, plus l'avis est ancien."""
    return {
        "id": identifiant,
        "plateforme": "airbnb",
        "note": 8,
        "commentaire": commentaire,
        "date_avis": (date.today() - timedelta(days=identifiant)).isoformat(),
    }


def avis_pms(identifiant):
    return avis_pms_avec_texte(identifiant, f"Avis {identifiant}")


def cinq_avis():
    return [avis_pms(i) for i in range(1, 6)]


# --- Base de l'agent ---

def ajouter_avis_en_base(identifiant, **champs):
    session = db_models.get_session(pipeline.engine)
    session.add(Avis(review_id_pms=identifiant, plateforme=Plateforme.AIRBNB, note=8,
                     texte_avis=f"Crash {identifiant}", date_avis=date.today(), **champs))
    session.commit()
    session.close()


def tous_les_avis():
    session = db_models.get_session(pipeline.engine)
    avis = session.query(Avis).order_by(Avis.review_id_pms).all()
    session.close()
    return avis


def ids_en_base():
    return [avis.review_id_pms for avis in tous_les_avis()]


def tous_les_logs():
    session = db_models.get_session(pipeline.engine)
    logs = session.query(Log).all()
    session.close()
    return logs


def executer_sql(requete):
    session = db_models.get_session(pipeline.engine)
    resultat = session.execute(text(requete)).scalar()
    session.close()
    return resultat


# --- Base du PMS ---

def creer_avis_dans_pms(statut, date_avis=date(2020, 1, 1)):
    """Crée un avis avec ce statut et renvoie son id."""
    session = models.get_session(main.engine)
    review = models.Review(plateforme=models.Plateforme.AIRBNB, note=8, commentaire="Bien",
                           date_avis=date_avis, statut=statut)
    session.add(review)
    session.commit()
    identifiant = review.id
    session.close()
    return identifiant


def date_dans_pms(identifiant):
    session = models.get_session(main.engine)
    resultat = session.get(models.Review, identifiant).date_avis
    session.close()
    return resultat
