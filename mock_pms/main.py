"""
mock_pms/main.py
API REST qui simule le PMS du client. Seule porte d'entrée vers mock_pms.db.
"""
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from models import init_db, get_session, Review, Plateforme, StatutPMS

app = FastAPI(title="Mock PMS API")
engine = init_db()


# --- Schémas de réponse / requête (Pydantic) ---

class ReviewOut(BaseModel):
    id: int
    plateforme: Plateforme
    note: int
    commentaire: Optional[str]
    date_avis: date
    statut: StatutPMS
    reponse_publiee: Optional[str]
    date_publication: Optional[datetime]

    class Config:
        from_attributes = True


class StatutUpdate(BaseModel):
    statut: StatutPMS


class PublishRequest(BaseModel):
    reponse: str


# --- Endpoints ---

@app.get("/reviews", response_model=list[ReviewOut])
def lister_avis(
    statut: Optional[StatutPMS] = Query(None),
    plateforme: Optional[Plateforme] = Query(None),
):
    """Liste les avis, filtrables par statut et/ou plateforme."""
    session = get_session(engine)
    query = session.query(Review)
    if statut is not None:
        query = query.filter(Review.statut == statut)
    if plateforme is not None:
        query = query.filter(Review.plateforme == plateforme)
    return query.all()


@app.get("/reviews/{review_id}", response_model=ReviewOut)
def obtenir_avis(review_id: int):
    """Détail d'un avis par son id."""
    session = get_session(engine)
    review = session.query(Review).filter(Review.id == review_id).first()
    if review is None:
        raise HTTPException(status_code=404, detail="Avis introuvable")
    return review


@app.patch("/reviews/{review_id}/statut", response_model=ReviewOut)
def changer_statut(review_id: int, body: StatutUpdate):
    """Change le statut d'un avis (ex: en_attente_publication après validation)."""
    if body.statut == StatutPMS.PUBLIE:
        raise HTTPException(
            status_code=400,
            detail="Utilise POST /reviews/{id}/publish pour publier un avis",
        )
 
    session = get_session(engine)
    review = session.query(Review).filter(Review.id == review_id).first()
    if review is None:
        raise HTTPException(status_code=404, detail="Avis introuvable")
 
    review.statut = body.statut
    session.commit()
    session.refresh(review)
    return review


@app.post("/reviews/{review_id}/publish", response_model=ReviewOut)
def publier_avis(review_id: int, body: PublishRequest):
    """Publie la réponse validée : statut -> publié, enregistre le texte final."""
    session = get_session(engine)
    review = session.query(Review).filter(Review.id == review_id).first()
    if review is None:
        raise HTTPException(status_code=404, detail="Avis introuvable")
 
    if review.statut != StatutPMS.EN_ATTENTE_PUBLICATION:
        raise HTTPException(
            status_code=409,
            detail="L'avis doit être en_attente_publication avant d'être publié",
        )
 
    review.statut = StatutPMS.PUBLIE
    review.reponse_publiee = body.reponse
    review.date_publication = datetime.utcnow()
    session.commit()
    session.refresh(review)
    return review