"""Les chiffres du tableau de bord (admin/statistiques.py), sur des avis créés à la main."""
from datetime import date, datetime, timedelta

from db_models import Avis, StatutErreur, StatutPipeline
from statistiques import (
    par_semaine,
    part_automatisee,
    repartition_des_flags,
    respect_des_delais,
    taux_de_regeneration,
    taux_de_retouche,
)

AUJOURD_HUI = date(2026, 11, 11)
VALIDE = StatutPipeline.SYNCHRONISE_PMS


def avis(statut=StatutPipeline.EN_VALIDATION, generee="Merci.", validee=None, regen=0,
         doublon=False, hors_sujet=False, erreur=False, surveillance=False,
         jours_avant_validation=None, age=1, cree_le=datetime(2026, 11, 10), score=80):
    """Un avis, sans base de données. Seuls les champs utiles au test sont à donner."""
    date_avis = AUJOURD_HUI - timedelta(days=age)
    return Avis(
        statut_pipeline=statut, reponse_generee=generee, reponse_validee=validee,
        nb_tentatives_regen=regen, flag_doublon=doublon, flag_hors_sujet=hors_sujet,
        flag_surveillance=surveillance,
        statut_erreur=StatutErreur.EN_ERREUR if erreur else StatutErreur.OK,
        date_avis=date_avis, date_creation=cree_le, score_confiance=score,
        date_validation=(datetime.combine(date_avis, datetime.min.time())
                         + timedelta(days=jours_avant_validation)) if jours_avant_validation is not None else None,
    )


# --- Taux de retouche ---

def test_retouche_une_reponse_modifiee_sur_quatre():
    liste = [avis(VALIDE, "Merci.", "Merci."), avis(VALIDE, "Merci.", "Merci."),
             avis(VALIDE, "Merci.", "Merci."), avis(VALIDE, "Merci.", "Merci beaucoup !")]

    assert taux_de_retouche(liste) == 0.25


def test_retouche_un_espace_en_plus_ne_compte_pas():
    assert taux_de_retouche([avis(VALIDE, "Merci.", "Merci.  ")]) == 0


def test_retouche_les_avis_pas_encore_valides_ne_comptent_pas():
    assert taux_de_retouche([avis(StatutPipeline.EN_VALIDATION)]) is None


def test_retouche_les_hors_sujet_sans_reponse_de_l_agent_ne_comptent_pas():
    assert taux_de_retouche([avis(VALIDE, generee=None, validee="Écrite à la main.")]) is None


# --- % automatisé ---

def test_automatise_ni_regeneration_ni_flag_ni_erreur():
    liste = [avis() for _ in range(8)] + [avis(regen=1), avis(hors_sujet=True, generee=None)]

    assert part_automatisee(liste) == 0.8


def test_automatise_un_avis_en_erreur_ne_compte_pas():
    assert part_automatisee([avis(), avis(erreur=True)]) == 0.5


def test_automatise_sans_avis():
    assert part_automatisee([]) is None


# --- Régénération et flags ---

def test_regeneration_une_reponse_rejetee_sur_cinq():
    assert taux_de_regeneration([avis(), avis(), avis(), avis(), avis(regen=2)]) == 0.2


def test_flags_un_avis_peut_en_avoir_plusieurs():
    flags = repartition_des_flags([avis(surveillance=True, doublon=True), avis(erreur=True)])

    assert flags == {"à surveiller": 1, "hors-sujet": 0, "doublon": 1, "en erreur": 1}


# --- Délais (14 jours, 10 jours pour la priorité, dans config.yaml) ---

def test_delais_valides_a_temps_de_justesse_et_en_retard():
    liste = [avis(VALIDE, jours_avant_validation=3), avis(VALIDE, jours_avant_validation=12),
             avis(VALIDE, jours_avant_validation=15)]

    resultat = respect_des_delais(liste, aujourd_hui=AUJOURD_HUI)

    assert resultat["valides_a_temps"] == 1
    assert resultat["valides_de_justesse"] == 1
    assert resultat["valides_en_retard"] == 1


def test_delais_un_avis_en_attente_depuis_20_jours_est_en_retard():
    resultat = respect_des_delais([avis(age=20), avis(age=5)], aujourd_hui=AUJOURD_HUI)

    assert resultat["en_attente_en_retard"] == 1


# --- Par semaine ---

def test_par_semaine_volume_et_score_moyen():
    liste = [avis(cree_le=datetime(2026, 11, 3), score=80), avis(cree_le=datetime(2026, 11, 4), score=90),
             avis(cree_le=datetime(2026, 11, 10), score=70)]

    assert par_semaine(liste) == [{"semaine": "2026-S45", "volume": 2, "score_moyen": 85},
                                  {"semaine": "2026-S46", "volume": 1, "score_moyen": 70}]


def test_automatise_un_avis_jamais_traite_ne_compte_pas():
    # Un run interrompu laisse des avis au statut nouveau : ils ne doivent pas gonfler le chiffre
    assert part_automatisee([avis(StatutPipeline.NOUVEAU), avis()]) == 1.0


def test_automatise_un_avis_en_erreur_reste_au_statut_nouveau_mais_compte():
    assert part_automatisee([avis(StatutPipeline.NOUVEAU, erreur=True), avis()]) == 0.5
