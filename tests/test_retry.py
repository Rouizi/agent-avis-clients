"""Retry de la récupération des avis auprès du PMS (zone grise #7)."""
import pytest
import requests

import pipeline
from outils import attentes

COUPURE = requests.ConnectionError("coupure")
TROP_LENT = requests.Timeout("trop lent")


class FausseReponse:
    """Imite la réponse de requests.get."""

    def __init__(self, code=200, contenu=None):
        self.code = code
        self.contenu = contenu

    def raise_for_status(self):
        if self.code >= 400:
            raise requests.HTTPError(str(self.code))

    def json(self):
        return self.contenu


def simuler_pms(*sorties):
    """Remplace requests.get : chaque appel renvoie ou lève la sortie suivante. Renvoie la liste des appels."""
    appels = []

    def faux_get(*args, **kwargs):
        sortie = sorties[len(appels)]
        appels.append(args)
        if isinstance(sortie, Exception):
            raise sortie
        return sortie

    requests.get = faux_get
    return appels


def test_succes_du_premier_coup():
    appels = simuler_pms(FausseReponse(contenu=[{"id": 1}]))

    resultat = pipeline._recuperer_avis_pms()

    assert resultat == [{"id": 1}]
    assert len(appels) == 1
    assert attentes == []


def test_succes_au_troisieme_essai():
    appels = simuler_pms(COUPURE, TROP_LENT, FausseReponse(contenu=[{"id": 1}]))

    resultat = pipeline._recuperer_avis_pms()

    assert resultat == [{"id": 1}]
    assert len(appels) == 3
    assert attentes == [2, 4]


def test_trois_coupures_font_remonter_l_erreur():
    appels = simuler_pms(COUPURE, COUPURE, COUPURE)

    with pytest.raises(requests.ConnectionError):
        pipeline._recuperer_avis_pms()

    assert len(appels) == 3
    assert attentes == [2, 4]


def test_trois_timeouts_font_remonter_l_erreur():
    appels = simuler_pms(TROP_LENT, TROP_LENT, TROP_LENT)

    with pytest.raises(requests.Timeout):
        pipeline._recuperer_avis_pms()

    assert len(appels) == 3


def test_trois_erreurs_500_font_remonter_l_erreur():
    appels = simuler_pms(FausseReponse(500), FausseReponse(500), FausseReponse(500))

    with pytest.raises(requests.HTTPError):
        pipeline._recuperer_avis_pms()

    assert len(appels) == 3


def test_vraie_erreur_de_connexion_sur_un_port_ferme():
    pipeline.CONFIG["pms"]["base_url"] = "http://127.0.0.1:9"

    with pytest.raises(requests.ConnectionError):
        pipeline._recuperer_avis_pms()

    assert attentes == [2, 4]


def test_pms_en_panne_arrete_le_run():
    simuler_pms(COUPURE, COUPURE, COUPURE)

    with pytest.raises(requests.ConnectionError):
        pipeline.run_pipeline()
