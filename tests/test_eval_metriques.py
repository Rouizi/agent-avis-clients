"""Les mesures de l'évaluation (eval/metriques.py), sur des phrases simples et sur le test réel."""
import json
from pathlib import Path

from metriques import (
    compter_expression,
    defauts,
    criteres_en_defaut,
    en_francais,
    erreurs_hors_sujet,
    formule_ouverture,
    longueur_correcte,
    ouverture_la_plus_frequente,
    palier_de_note,
    part_sans_defaut,
    repetitions_moyennes,
    scores_selon_defauts,
    statistiques_scores,
    tutoie,
)

FICHIER_TEST_REEL = Path(__file__).parents[1] / "eval" / "run_reel_v1.json"
TEST_REEL = json.loads(FICHIER_TEST_REEL.read_text(encoding="utf-8"))
AVIS_AVEC_REPONSE = [avis for avis in TEST_REEL if avis["reponse"]]
REPONSES_DU_TEST_REEL = [avis["reponse"] for avis in AVIS_AVEC_REPONSE]
REPONSES_NOTES_1_A_4 = [avis["reponse"] for avis in AVIS_AVEC_REPONSE if avis["note"] <= 4]
REPONSES_NOTES_5_A_10 = [avis["reponse"] for avis in AVIS_AVEC_REPONSE if avis["note"] >= 5]

BONNE_REPONSE = "Merci pour votre retour. Nous sommes ravis que la terrasse vous ait plu."


def cas_de_test(details_attendus=(), interdits=()):
    return {"details_attendus": list(details_attendus), "interdits": list(interdits)}


# --- Répétitions ---

def test_formule_d_ouverture_trois_premiers_mots():
    assert formule_ouverture("Nous vous remercions pour votre retour.") == "nous vous remercions"


def test_ouverture_la_plus_frequente():
    reponses = ["Merci à vous.", "Merci à vous, vraiment.", "Un grand merci."]

    assert ouverture_la_plus_frequente(reponses) == ("merci a vous", 2)


def test_une_expression_compte_une_seule_fois_par_reponse():
    reponses = ["Désolés, vraiment désolés.", "Bonjour."]

    assert compter_expression(reponses, "désolés") == 1


def test_moyenne_de_deux_series():
    serie_1 = ["Nous vous remercions.", "Nous vous remercions.", "Merci à vous."]  # 2 sur 3
    serie_2 = ["Nous vous remercions.", "Un grand merci.", "Merci à vous."]        # 1 sur 3

    resultat = repetitions_moyennes([serie_1, serie_2])

    assert resultat["parts_expressions_surveillees"]["nous vous remercions"] == 0.5


def test_les_expressions_frequentes_sont_trouvees_sans_les_connaitre_a_l_avance():
    serie = ["Quel beau retour.", "Quel beau retour, merci."]

    resultat = repetitions_moyennes([serie])

    assert {"expression": "quel beau retour", "part": 1.0} in resultat["expressions_frequentes"]


def test_serie_vide_ignoree():
    assert repetitions_moyennes([[]]) is None


def test_palier_de_note_lu_dans_la_config():
    assert palier_de_note(1) == "1-4"
    assert palier_de_note(4) == "1-4"
    assert palier_de_note(7) == "7-8"
    assert palier_de_note(10) == "9-10"


# --- Défauts d'une réponse ---

def test_bonne_reponse_sans_defaut():
    assert defauts(BONNE_REPONSE, cas_de_test(details_attendus=["terrasse"])) == []


def test_detail_absent():
    assert defauts(BONNE_REPONSE, cas_de_test(details_attendus=["piscine"])) == ["detail_absent"]


def test_au_moins_un_detail_suffit():
    assert defauts(BONNE_REPONSE, cas_de_test(details_attendus=["piscine", "terrasse"])) == []


def test_mot_interdit_pour_ce_cas():
    assert defauts(BONNE_REPONSE, cas_de_test(interdits=["ravis"])) == ["interdit:ravis"]


def verdict_juge(*criteres_en_defaut):
    """Un verdict du juge où seuls les critères donnés sont en défaut."""
    return {critere: {"defaut": critere in criteres_en_defaut, "raison": "raison"}
            for critere in ["promesse_action", "compensation", "enquete", "aveu_faute",
                            "contradiction_ignoree"]}


def test_un_defaut_vu_par_le_juge_compte_comme_defaut():
    assert defauts(BONNE_REPONSE, cas_de_test(details_attendus=["terrasse"]),
                   verdict_juge("enquete")) == ["juge:enquete"]


def test_un_juge_sans_reproche_n_ajoute_aucun_defaut():
    assert defauts(BONNE_REPONSE, cas_de_test(details_attendus=["terrasse"]), verdict_juge()) == []


def test_les_defauts_du_juge_s_ajoutent_a_ceux_des_regles():
    resultat = defauts(BONNE_REPONSE, cas_de_test(interdits=["ravis"]), verdict_juge("aveu_faute"))

    assert resultat == ["juge:aveu_faute", "interdit:ravis"]


def test_sans_verdict_du_juge_seules_les_regles_comptent():
    assert criteres_en_defaut(None) == []
    assert defauts(BONNE_REPONSE, cas_de_test(details_attendus=["terrasse"])) == []


def test_une_seule_phrase_est_trop_courte():
    assert not longueur_correcte("Merci pour votre retour.")


def test_cinq_phrases_est_trop_long():
    assert not longueur_correcte("Un. Deux. Trois. Quatre. Cinq.")


def test_quatre_phrases_est_accepte():
    assert longueur_correcte("Un. Deux ! Trois ? Quatre.")


def test_tutoiement_detecte():
    assert tutoie("Merci pour ton retour.")


def test_vouvoiement_accepte():
    assert not tutoie(BONNE_REPONSE)


def test_reponse_en_francais_acceptee():
    assert en_francais(BONNE_REPONSE)


def test_reponse_en_anglais_detectee():
    assert not en_francais("Thank you for your review. We are glad you enjoyed the terrace.")


def test_part_sans_defaut():
    assert part_sans_defaut([[], ["longueur"], [], ["tutoiement", "longueur"]]) == 0.5


# --- Score de confiance ---

def test_statistiques_scores():
    stats = statistiques_scores([80, 85, 85, 90])

    assert stats["min"] == 80
    assert stats["max"] == 90
    assert stats["valeur_la_plus_frequente"] == 85
    assert stats["part_valeur_la_plus_frequente"] == 0.5


def test_score_moyen_selon_les_defauts():
    scores = [90, 80, 50]
    liste_de_defauts = [[], [], ["longueur"]]

    resultat = scores_selon_defauts(scores, liste_de_defauts)

    assert resultat["moyenne_sans_defaut"] == 85
    assert resultat["moyenne_avec_defaut"] == 50


# --- Classifieur hors-sujet ---

def resultat_classifieur(identifiant, attendu, obtenu):
    return {"id": identifiant, "hors_sujet_attendu": attendu, "hors_sujet_obtenu": obtenu,
            "raison_hors_sujet": "raison"}


def test_avis_valable_classe_hors_sujet_est_un_faux_positif():
    erreurs = erreurs_hors_sujet([resultat_classifieur("mixte", attendu=False, obtenu=True)])

    assert [e["id"] for e in erreurs["faux_positifs"]] == ["mixte"]
    assert erreurs["faux_negatifs"] == []


def test_vrai_hors_sujet_non_detecte_est_un_faux_negatif():
    erreurs = erreurs_hors_sujet([resultat_classifieur("spam", attendu=True, obtenu=False)])

    assert [e["id"] for e in erreurs["faux_negatifs"]] == ["spam"]


def test_classifieur_en_echec_n_est_pas_une_erreur_de_verdict():
    erreurs = erreurs_hors_sujet([resultat_classifieur("panne", attendu=False, obtenu=None)])

    assert erreurs["faux_positifs"] == []
    assert erreurs["nb_erreurs_techniques"] == 1


# --- Le test réel : les mesures doivent retrouver ce qu'on a compté à la main ---

def test_reel_19_reponses_generees_et_1_avis_signale():
    assert len(TEST_REEL) == 20
    assert len(REPONSES_DU_TEST_REEL) == 19
    assert sum(avis["flag_hors_sujet"] for avis in TEST_REEL) == 1


def test_reel_le_faux_hors_sujet_est_l_avis_sur_les_courses():
    signale = [avis for avis in TEST_REEL if avis["flag_hors_sujet"]][0]

    assert "livraison de mes courses" in signale["commentaire"]


def test_reel_13_reponses_sur_19_commencent_par_nous_vous_remercions():
    assert ouverture_la_plus_frequente(REPONSES_DU_TEST_REEL) == ("nous vous remercions", 13)
    assert compter_expression(REPONSES_DU_TEST_REEL, "nous vous remercions") == 13


def test_reel_10_reponses_sur_19_contiennent_sincerement_desoles():
    assert compter_expression(REPONSES_DU_TEST_REEL, "sincèrement désolés") == 10


def test_reel_sincerement_desoles_concentre_sur_les_notes_de_1_a_4():
    assert len(REPONSES_NOTES_1_A_4) == 11
    assert compter_expression(REPONSES_NOTES_1_A_4, "sincèrement désolés") == 10
    assert compter_expression(REPONSES_NOTES_5_A_10, "sincèrement désolés") == 0


def test_reel_scores_de_75_a_95_et_souvent_85():
    stats = statistiques_scores([avis["score_confiance"] for avis in AVIS_AVEC_REPONSE])

    assert stats["min"] == 75
    assert stats["max"] == 95
    assert stats["valeur_la_plus_frequente"] == 85
