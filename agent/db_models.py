"""
Schéma de la base interne de l'agent (agent_internal.db)
Contient les tables 'avis' et 'logs'.
"""
import enum
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean,
    DateTime, Date, Enum as SAEnum
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

# Chemin absolu : agent, Flask et n8n trouvent la même base quel que soit le dossier de lancement
DB_URL = f"sqlite:///{Path(__file__).parent / 'agent_internal.db'}"


class StatutPipeline(str, enum.Enum):
    NOUVEAU = "nouveau"
    GENERE = "généré"
    EN_VALIDATION = "en_validation"
    VALIDE = "validé"
    SYNCHRONISE_PMS = "synchronisé_pms"


class Plateforme(str, enum.Enum):
    AIRBNB = "airbnb"
    BOOKING = "booking"
    VRBO = "vrbo"
    SITE_PROPRIETAIRE = "site_proprietaire"


class StatutErreur(str, enum.Enum):
    OK = "ok"
    EN_ERREUR = "en_erreur"


class ActionLog(str, enum.Enum):
    GENERE = "généré"
    REGENERE = "régénéré"
    VALIDE = "validé"
    REJETE = "rejeté"
    PUBLIE = "publié"
    ECHEC = "échec"
    ERREUR_FINALE = "erreur_finale"
    SIGNALE = "signalé"


class Acteur(str, enum.Enum):
    AGENT = "agent"
    HUMAIN = "humain"


class Avis(Base):
    __tablename__ = "avis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    review_id_pms = Column(Integer, unique=True, nullable=False)
    plateforme = Column(SAEnum(Plateforme), nullable=False)
    note = Column(Integer, nullable=False)
    texte_avis = Column(Text, nullable=False)
    reponse_generee = Column(Text)  # dernière réponse produite par l'agent (une régénération l'écrase, l'ancienne reste dans logs)
    reponse_validee = Column(Text, nullable=True)  # texte final approuvé par l'humain (identique ou édité)

    statut_pipeline = Column(SAEnum(StatutPipeline), default=StatutPipeline.NOUVEAU)
    flag_surveillance = Column(Boolean, default=False)
    flag_doublon = Column(Boolean, default=False)
    flag_hors_sujet = Column(Boolean, default=False)
    raison_hors_sujet = Column(Text, nullable=True)  # explication du classifieur, affichée en revue manuelle
    score_confiance = Column(Integer, nullable=True)

    nb_tentatives_regen = Column(Integer, default=0)
    nb_tentatives_erreur = Column(Integer, default=0)
    statut_erreur = Column(SAEnum(StatutErreur), default=StatutErreur.OK)

    priorite_delai = Column(Boolean, default=False)
    date_avis = Column(Date, nullable=False)
    date_creation = Column(DateTime, default=datetime.utcnow)
    date_validation = Column(DateTime, nullable=True)


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    review_id_pms = Column(Integer, nullable=False)
    action = Column(SAEnum(ActionLog), nullable=False)
    details = Column(Text, nullable=True)
    acteur = Column(SAEnum(Acteur), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


def init_db(db_path=DB_URL):
    """Crée le fichier .db et les tables si elles n'existent pas encore."""
    engine = create_engine(db_path)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine):
    """Renvoie une session SQLAlchemy prête à l'emploi pour insérer/lire."""
    Session = sessionmaker(bind=engine)
    return Session()


if __name__ == "__main__":
    engine = init_db()
    print("Base agent_internal.db créée avec les tables 'avis' et 'logs'.")
