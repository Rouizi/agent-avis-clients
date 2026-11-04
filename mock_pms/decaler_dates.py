"""
mock_pms/decaler_dates.py
Redonne aux avis en attente d'évaluation une date récente, sans appel à OpenAI.
À lancer juste avant un test : les avis du seed vieillissent et dépassent vite les 14 jours.
Écrit directement dans mock_pms.db : exception à la règle "API uniquement", car ce sont des données de test.
"""
import random
from datetime import date, timedelta

from models import init_db, get_session, Review, StatutPMS

AGE_MAX_JOURS = 16  # quelques avis restent au-delà des 14 jours, pour tester ce cas


def decaler_dates(session) -> int:
    """Tire une nouvelle date pour chaque avis en attente d'évaluation. Renvoie le nombre d'avis modifiés."""
    avis = session.query(Review).filter(Review.statut == StatutPMS.EN_ATTENTE_EVALUATION).all()
    for review in avis:
        review.date_avis = date.today() - timedelta(days=random.randint(0, AGE_MAX_JOURS))
    session.commit()
    return len(avis)


if __name__ == "__main__":
    session = get_session(init_db())
    print(f"{decaler_dates(session)} avis redatés (0 à {AGE_MAX_JOURS} jours).")
    session.close()
