"""
seed_data.py
Génère 200 avis fictifs via l'API OpenAI et les insère dans mock_pms.db.
Nécessite la variable d'environnement OPENAI_API_KEY.
"""
import json
import random
from datetime import date, timedelta

from openai import OpenAI

from models import init_db, get_session, Review, Plateforme, StatutPMS

client = OpenAI()
MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """Tu génères des avis clients fictifs et réalistes pour une société de gestion
locative saisonnière en France (Airbnb, Booking, VRBO, site propriétaire).

Réponds UNIQUEMENT avec un tableau JSON valide, sans texte avant/après, sans balises markdown.
Chaque élément doit avoir exactement ces clés :
- "note" : entier de 1 à 10
- "commentaire" : texte de l'avis en langue naturelle, ou null si l'avis est vide
- "langue" : code langue de l'avis ("fr", "en", "es", "de", "it")

Le ton du commentaire doit correspondre à la note (positif si note haute, négatif si note basse)."""

# Chaque tuple : (nombre d'avis, consigne spécifique pour ce lot)
LOTS = [
    (25, "Avis normaux, notes variées de 1 à 10, en français."),
    (25, "Avis normaux, notes variées de 1 à 10, en français."),
    (25, "Avis normaux, notes variées de 1 à 10, en français."),
    (25, "Avis normaux, notes variées de 1 à 10, en français."),
    (25, "Avis normaux, notes variées de 1 à 10, en français."),
    (15, "Avis normaux, notes variées de 1 à 10, en français."),
    (15, "Notes toutes entre 1 et 3, ton très mécontent, mentionne parfois "
         "'remboursement', 'jamais revenir', 'avocat' ou 'inacceptable'."),
    (10, "Le commentaire parle d'un sujet hors séjour (livraison Amazon, "
         "voisinage sans lien avec le logement, plainte sur autre chose)."),
    (15, "Uniquement en anglais, espagnol, allemand ou italien (pas de français)."),
    (10, "Le champ commentaire doit être null pour tous ces avis (avis vides)."),
]


def generer_lot(n: int, consigne: str) -> list[dict]:
    """Demande à OpenAI de générer n avis selon une consigne spécifique."""
    prompt = f"Génère {n} avis clients. Consigne : {consigne}"

    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=4000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    texte = response.choices[0].message.content.strip()
    texte = texte.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(texte)


def date_aleatoire(max_jours_passes: int = 20) -> date:
    """Date dans les derniers jours, pour tester le seuil des 14 jours (zone grise #8)."""
    return date.today() - timedelta(days=random.randint(0, max_jours_passes))


def construire_review(avis: dict) -> Review:
    return Review(
        plateforme=random.choice(list(Plateforme)),
        note=avis["note"],
        commentaire=avis.get("commentaire"),
        date_avis=date_aleatoire(),
        statut=StatutPMS.EN_ATTENTE_EVALUATION,
    )


def main():
    engine = init_db()
    session = get_session(engine)

    tous_avis = []
    for n, consigne in LOTS:
        print(f"Génération de {n} avis — {consigne[:50]}...")
        tous_avis += generer_lot(n, consigne)

    reviews = [construire_review(a) for a in tous_avis]

    # 10 doublons volontaires (texte identique à un avis existant) — zone grise #5
    avis_avec_texte = [a for a in tous_avis if a.get("commentaire")]
    for avis in random.sample(avis_avec_texte, 10):
        reviews.append(construire_review(avis))

    session.add_all(reviews)
    session.commit()
    print(f"{len(reviews)} avis insérés dans mock_pms.db.")


if __name__ == "__main__":
    main()