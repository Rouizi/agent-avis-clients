"""
admin/statistiques.py
Les chiffres du tableau de bord (décision #10 de cdc-notes.md), calculés à partir des colonnes
de la table avis, jamais en lisant logs.details.

Chaque fonction reçoit une liste d'avis (des lignes Avis de agent_internal.db) et renvoie un
chiffre ou un petit dictionnaire. Aucune ne lit la base elle-même : on peut les tester avec
des avis créés à la main.

Une part est un nombre entre 0 et 1 (0.25 = 25 %), ou None s'il n'y a rien à compter.
"""
from collections import Counter
from datetime import date

from db_models import StatutErreur, StatutPipeline
from llm_client import CONFIG

VALIDES = (StatutPipeline.VALIDE, StatutPipeline.SYNCHRONISE_PMS)


def part(nombre, total):
    """part(1, 4) -> 0.25      part(0, 0) -> None (rien à compter)"""
    return nombre / total if total else None


def taux_de_retouche(avis):
    """
    Part des réponses de l'agent que l'humain a modifiées avant de les valider.
    Seuls comptent les avis validés qui avaient une réponse de l'agent : les doublons et les
    hors-sujet n'en ont pas. Un espace en plus ou en moins au début ou à la fin ne compte pas.

    4 avis validés : 3 validés tels quels, 1 modifié  ->  0.25

    Limite connue : une réponse entièrement réécrite après 3 rejets (file manuelle) compte
    aussi comme une retouche, car la base ne garde pas la file d'origine de la validation.
    """
    concernes = [a for a in avis if a.statut_pipeline in VALIDES and a.reponse_generee]
    modifies = [a for a in concernes
                if (a.reponse_validee or "").strip() != a.reponse_generee.strip()]
    return part(len(modifies), len(concernes))


def part_automatisee(avis):
    """
    Part des avis traités sans intervention en plus : ni régénération, ni doublon, ni
    hors-sujet, ni erreur (journal de cdc-notes.md, 2026-11-04). Elle ne tient pas compte
    des retouches, qui sont mesurées à part.

    Un avis jamais traité (statut nouveau, sans erreur) ne compte ni en haut ni en bas :
    sinon, un run interrompu laisserait des avis intacts qui feraient MONTER le pourcentage.
    Un avis en erreur garde le statut nouveau, mais il a bien été tenté : il compte.

    10 avis traités, dont 1 régénéré et 1 hors-sujet  ->  0.8
    """
    traites = [a for a in avis if a.statut_pipeline != StatutPipeline.NOUVEAU
               or a.statut_erreur == StatutErreur.EN_ERREUR]
    automatises = [a for a in traites
                   if not a.nb_tentatives_regen and not a.flag_doublon and not a.flag_hors_sujet
                   and a.statut_erreur == StatutErreur.OK]
    return part(len(automatises), len(traites))


def taux_de_regeneration(avis):
    """
    Part des réponses rejetées au moins une fois, parmi les avis qui ont reçu une réponse.

    5 avis avec réponse, dont 1 régénéré  ->  0.2
    """
    avec_reponse = [a for a in avis if a.reponse_generee]
    return part(sum(1 for a in avec_reponse if a.nb_tentatives_regen), len(avec_reponse))


def repartition_des_flags(avis):
    """
    Nombre d'avis pour chaque flag. Un avis peut avoir plusieurs flags.

    ->  {"à surveiller": 3, "hors-sujet": 1, "doublon": 0, "en erreur": 2}
    """
    return {
        "à surveiller": sum(1 for a in avis if a.flag_surveillance),
        "hors-sujet": sum(1 for a in avis if a.flag_hors_sujet),
        "doublon": sum(1 for a in avis if a.flag_doublon),
        "en erreur": sum(1 for a in avis if a.statut_erreur == StatutErreur.EN_ERREUR),
    }


def respect_des_delais(avis, aujourd_hui=None):
    """
    Le délai de publication (14 jours sur Airbnb, delai_publication_jours dans config.yaml),
    compté à partir de la date de l'avis.

    ->  {"valides_a_temps": 8,        validés dans les 10 premiers jours
         "valides_de_justesse": 1,    validés entre le 10e et le 14e jour (delai_priorite_jours)
         "valides_en_retard": 1,      validés après le 14e jour
         "en_attente_en_retard": 2}   pas encore validés, et déjà au-delà du 14e jour
    """
    aujourd_hui = aujourd_hui or date.today()
    limite = CONFIG["seuils"]["delai_publication_jours"]
    priorite = CONFIG["seuils"]["delai_priorite_jours"]
    resultat = {"valides_a_temps": 0, "valides_de_justesse": 0, "valides_en_retard": 0,
                "en_attente_en_retard": 0}

    for a in avis:
        if a.statut_pipeline in VALIDES and a.date_validation:
            jours = (a.date_validation.date() - a.date_avis).days
            if jours > limite:
                resultat["valides_en_retard"] += 1
            elif jours >= priorite:
                resultat["valides_de_justesse"] += 1
            else:
                resultat["valides_a_temps"] += 1
        elif a.statut_pipeline in (StatutPipeline.EN_VALIDATION, StatutPipeline.A_TRAITER_MANUELLEMENT):
            if (aujourd_hui - a.date_avis).days > limite:
                resultat["en_attente_en_retard"] += 1
    return resultat


def par_semaine(avis):
    """
    Volume traité et score de confiance moyen, pour chaque semaine où l'agent a enregistré
    des avis (date_creation). Le score moyen qui baisse ou monte d'une semaine à l'autre
    montre si l'agent « dérive ».

    ->  [{"semaine": "2026-S45", "volume": 200, "score_moyen": 84.2},
         {"semaine": "2026-S46", "volume": 187, "score_moyen": 81.0}]   (de la plus ancienne à la plus récente)
    """
    volumes = Counter()
    scores = {}
    for a in avis:
        annee, numero, _ = a.date_creation.isocalendar()
        semaine = f"{annee}-S{numero:02d}"
        volumes[semaine] += 1
        if a.score_confiance is not None:
            scores.setdefault(semaine, []).append(a.score_confiance)

    return [{"semaine": s, "volume": volumes[s],
             "score_moyen": (sum(scores[s]) / len(scores[s])) if s in scores else None}
            for s in sorted(volumes)]


def tableau_de_bord(avis):
    """Tous les chiffres du tableau de bord, pour la page /tableau-de-bord."""
    return {
        "nb_avis": len(avis),
        "nb_valides": sum(1 for a in avis if a.statut_pipeline in VALIDES),
        "taux_de_retouche": taux_de_retouche(avis),
        "part_automatisee": part_automatisee(avis),
        "taux_de_regeneration": taux_de_regeneration(avis),
        "flags": repartition_des_flags(avis),
        "delais": respect_des_delais(avis),
        "par_semaine": par_semaine(avis),
    }
