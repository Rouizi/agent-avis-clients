"""Règles des garde-fous (guardrails.py)."""
from datetime import date, timedelta

import guardrails
from guardrails import est_doublon, est_prioritaire, est_tres_negatif, est_vide, evaluer_avis
from llm_client import CONFIG

SEUIL_NEGATIF = CONFIG["seuils"]["note_tres_negatif"]
SEUIL_PRIORITE = CONFIG["seuils"]["delai_priorite_jours"]
AVIS_LONG = "Appartement très propre, accueil chaleureux, on reviendra avec plaisir !"


def test_est_vide():
    assert est_vide(None)
    assert est_vide("")
    assert est_vide("   ")
    assert not est_vide("Super")


def test_note_au_seuil_est_tres_negative():
    assert est_tres_negatif(SEUIL_NEGATIF)


def test_note_au_dessus_du_seuil_n_est_pas_tres_negative():
    assert not est_tres_negatif(SEUIL_NEGATIF + 1)


def test_avis_au_seuil_de_delai_est_prioritaire():
    assert est_prioritaire(date.today() - timedelta(days=SEUIL_PRIORITE))


def test_avis_sous_le_seuil_de_delai_n_est_pas_prioritaire():
    assert not est_prioritaire(date.today() - timedelta(days=SEUIL_PRIORITE - 1))


def test_doublon_sans_la_ponctuation():
    assert est_doublon("Appartement très propre, accueil chaleureux, on reviendra avec plaisir", [AVIS_LONG])


def test_doublon_malgre_les_majuscules_et_les_accents():
    assert est_doublon("APPARTEMENT TRES PROPRE accueil chaleureux on reviendra avec plaisir!!", [AVIS_LONG])


def test_avis_different_n_est_pas_un_doublon():
    assert not est_doublon("Logement bruyant et mal isolé, nuits difficiles.", [AVIS_LONG])


def test_avis_trop_court_n_est_jamais_un_doublon():
    assert not est_doublon("Super séjour !", ["Super séjour !"])


def test_doublon_detecte_sur_un_avis_de_plus_de_200_caracteres():
    avis_long = AVIS_LONG * 4
    presque_pareil = avis_long.replace("Appartement", "Logement", 1)

    assert len(avis_long) > 200
    assert est_doublon(presque_pareil, [avis_long])


def test_avis_longs_differents_ne_sont_pas_des_doublons():
    avis_long = AVIS_LONG * 4
    autre_avis = "Logement bruyant et mal isolé, nuits difficiles, accueil absent. " * 4

    assert not est_doublon(autre_avis, [avis_long])


def test_evaluer_avis_combine_tous_les_flags():
    guardrails.est_hors_sujet = lambda commentaire: (True, "Spam.")

    resultat = evaluer_avis(SEUIL_NEGATIF, AVIS_LONG, date.today(), [AVIS_LONG])

    assert resultat["flag_surveillance"] is True
    assert resultat["flag_doublon"] is True
    assert resultat["flag_hors_sujet"] is True
    assert resultat["raison_hors_sujet"] == "Spam."
    assert resultat["priorite_delai"] is False


def test_evaluer_avis_ignore_la_raison_si_pas_hors_sujet():
    guardrails.est_hors_sujet = lambda commentaire: (False, "Parle bien du logement.")

    resultat = evaluer_avis(8, AVIS_LONG, date.today(), [])

    assert resultat["raison_hors_sujet"] is None
