"""
Schéma du mock PMS (mock_pms.db)
Simule le système externe du client : ne connaît que les 3 statuts du CDC.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, Integer, Text, Date, DateTime, Enum as SAEnum
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class Plateforme(str, enum.Enum):
    AIRBNB = "airbnb"
    BOOKING = "booking"
    VRBO = "vrbo"
    SITE_PROPRIETAIRE = "site_proprietaire"


class StatutPMS(str, enum.Enum):
    EN_ATTENTE_EVALUATION = "en_attente_evaluation"
    EN_ATTENTE_PUBLICATION = "en_attente_publication"
    PUBLIE = "publié"


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plateforme = Column(SAEnum(Plateforme), nullable=False)
    note = Column(Integer, nullable=False)  # 1-10
    commentaire = Column(Text, nullable=True)  # NULL = avis vide (note sans commentaire, cf. CDC 3.2)
    date_avis = Column(Date, nullable=False)

    statut = Column(SAEnum(StatutPMS), default=StatutPMS.EN_ATTENTE_EVALUATION)
    reponse_publiee = Column(Text, nullable=True)  # texte final tel que "publié" sur la plateforme
    date_publication = Column(DateTime, nullable=True)


def init_db(db_path="sqlite:///mock_pms.db"):
    """Crée le fichier .db et la table si elle n'existe pas encore."""
    engine = create_engine(db_path)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine):
    Session = sessionmaker(bind=engine)
    return Session()


if __name__ == "__main__":
    engine = init_db()
    print("Base mock_pms.db créée avec la table 'reviews'.")