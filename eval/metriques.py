"""
eval/metriques.py
Le correcteur de l'évaluation : du texte ou des chiffres en entrée, un résultat en sortie.
Aucun appel au LLM ici : tout se teste sans clé OpenAI (tests/test_eval_metriques.py).
Le verdict du juge (juge.py) arrive ici déjà prêt, comme une simple donnée.

Les fonctions sont rangées en 5 groupes :
  1. Préparer le texte   : outils de base utilisés partout
  2. Répétitions         : les réponses se ressemblent-elles trop ?
  3. Défauts             : cette réponse respecte-t-elle les règles ?
  4. Score de confiance  : le score repère-t-il les mauvaises réponses ?
  5. Hors-sujet          : le classifieur se trompe-t-il ?
"""
import re
import statistics
import unicodedata
from collections import Counter
from pathlib import Path

import yaml

DOSSIER = Path(__file__).parent

# Les réglages de la mesure (2 à 4 phrases, mots du tutoiement, réglages du juge...)
with open(DOSSIER / "criteres.yaml", encoding="utf-8") as f:
    CRITERES = yaml.safe_load(f)

# Les paliers de ton de l'agent (1-4, 5-6, 7-8, 9-10)
with open(DOSSIER.parent / "agent" / "config.yaml", encoding="utf-8") as f:
    TONS = yaml.safe_load(f)["tons"]


# --- 1. Préparer le texte ---

def mots(texte):
    """
    Découpe un texte en mots : minuscules, sans accents, sans ponctuation.

    "Nous sommes DÉSOLÉS !"  ->  ['nous', 'sommes', 'desoles']
    "Merci d'être venus."    ->  ['merci', 'd', 'etre', 'venus']
    """
    sans_accents = unicodedata.normalize("NFKD", texte.lower())
    sans_accents = "".join(c for c in sans_accents if not unicodedata.combining(c))
    return re.findall(r"\w+", sans_accents)


def contient(texte, expression):
    """
    Vrai si l'expression apparaît dans le texte, en début de mot.
    Les majuscules, les accents et la ponctuation sont ignorés.

    ("Nous sommes sincèrement désolés.", "sincerement desoles")  ->  True
    ("Vous demandez un remboursement.", "rembours")              ->  True   (début de « remboursement »)
    ("Merci pour ces ballons.", "allons")                        ->  False  (milieu du mot « ballons »)
    """
    return " " + " ".join(mots(expression)) in " " + " ".join(mots(texte))


def phrases(texte):
    """
    Coupe le texte en phrases, après chaque . ! ou ?

    "Merci ! Nous sommes ravis. À bientôt ?"  ->  ["Merci !", "Nous sommes ravis.", "À bientôt ?"]
    """
    return re.split(r"(?<=[.!?])\s+", texte.strip())


# --- 2. Répétitions ---

def formule_ouverture(reponse):
    """
    Les 3 premiers mots de la réponse (nb_mots_ouverture dans criteres.yaml).

    "Nous vous remercions pour votre retour."  ->  "nous vous remercions"
    """
    return " ".join(mots(reponse)[:CRITERES["nb_mots_ouverture"]])


def ouverture_la_plus_frequente(reponses):
    """
    La formule d'ouverture la plus utilisée, et le nombre de réponses qui l'utilisent.

    ["Merci à vous.", "Merci à vous, vraiment.", "Un grand merci."]  ->  ("merci a vous", 2)
    Sur le test réel (19 réponses)                                   ->  ("nous vous remercions", 13)
    """
    formule, nombre = Counter(formule_ouverture(r) for r in reponses).most_common(1)[0]
    return formule, nombre


def compter_expression(reponses, expression):
    """
    Nombre de réponses qui contiennent l'expression. Une réponse compte une seule fois.

    (["Désolés, vraiment désolés.", "Bonjour."], "désolés")  ->  1
    (test réel, "sincèrement désolés")                       ->  10
    """
    return sum(contient(r, expression) for r in reponses)


def compter_expressions_repetees(reponses):
    """
    Trouve toute seule les suites de 3 mots qui reviennent, sans savoir quoi chercher.
    Utile pour repérer un nouveau tic de langage dans une v2.

    ["Quel beau retour.", "Quel beau retour, merci."]
      ->  {"quel beau retour": 2, "beau retour merci": 1}
    """
    taille = CRITERES["nb_mots_expression"]
    compteur = Counter()
    for reponse in reponses:
        m = mots(reponse)
        compteur.update({" ".join(m[i:i + taille]) for i in range(len(m) - taille + 1)})
    return compteur


def repetitions_d_une_serie(reponses):
    """
    Les répétitions dans une série : une réponse par avis, comme un vrai lot.
    Les résultats sont des proportions (0.75 = 75 % des réponses).

    4 réponses, dont 3 commencent par « Nous vous remercions »
    et 2 contiennent « sincèrement désolés »
      ->  {"nb_reponses": 4,
           "ouverture": "nous vous remercions",
           "part_ouverture": 0.75,
           "parts_expressions_surveillees": {"nous vous remercions": 0.75,
                                             "sincèrement désolés": 0.5}}
    """
    formule, nombre = ouverture_la_plus_frequente(reponses)
    return {
        "nb_reponses": len(reponses),
        "ouverture": formule,
        "part_ouverture": nombre / len(reponses),
        "parts_expressions_surveillees": {
            expression: compter_expression(reponses, expression) / len(reponses)
            for expression in CRITERES["expressions_surveillees"]
        },
    }


def repetitions_moyennes(series):
    """
    Mesure les répétitions sur plusieurs lots, puis fait la moyenne.

    Le LLM ne répond jamais deux fois pareil : chaque avis reçoit 3 réponses.
    Une série = une réponse par avis, comme un vrai lot de la semaine.
    Exemple avec 3 avis (A, B, C), « Nous » = la réponse commence par « Nous vous remercions » :

                 1re réponse   2e réponse   3e réponse
        Avis A   Nous          Merci        Nous
        Avis B   Nous          Nous         Nous
        Avis C   Merci         Merci        Nous
                 série 1       série 2      série 3

    série 1 : 2 réponses sur 3 avec « nous vous remercions »  ->  0.67
    série 2 : 1 sur 3                                         ->  0.33
    série 3 : 3 sur 3                                         ->  1.0
    moyenne : (0.67 + 0.33 + 1.0) / 3                          ->  0.67

    Avec « Nous » = "Nous vous remercions pour votre retour." et « Merci » = "Merci pour votre avis.",
    la fonction renvoie :
      {"nb_reponses_par_serie": 3,              (3 avis, donc 3 réponses par série)
       "part_ouverture_moyenne": 0.78,          (voir ci-dessous)
       "ouvertures_par_serie": ["nous vous remercions", "merci pour votre", "nous vous remercions"],
       "parts_expressions_surveillees": {"nous vous remercions": 0.67,   (le calcul ci-dessus)
                                         "sincèrement désolés": 0.0},    (jamais présente)
       "expressions_frequentes": [{"expression": "nous vous remercions", "part": 0.67},
                                  {"expression": "vous remercions pour", "part": 0.67},
                                  {"expression": "remercions pour votre", "part": 0.67},
                                  {"expression": "pour votre retour", "part": 0.67},
                                  {"expression": "merci pour votre", "part": 0.33},
                                  {"expression": "pour votre avis", "part": 0.33}]}

    Pourquoi 0.78 et pas 0.67 : "part_ouverture" prend dans chaque série la formule la plus
    fréquente, quelle qu'elle soit. Dans la série 2, c'est « merci pour votre » (2 sur 3).
    Séries 1, 2, 3 : 2/3, 2/3, 3/3  ->  moyenne 0.78. C'est pour ça que "ouvertures_par_serie"
    dit quelle formule domine chaque série.
    "expressions_frequentes" : "part" = nombre de réponses qui contiennent la suite de 3 mots,
    divisé par le total des réponses de toutes les séries (ici 6 sur 9 = 0.67).

    Renvoie aussi "expressions_frequentes" : les 10 suites de 3 mots les plus fréquentes.
    Une série vide est ignorée ; si toutes sont vides, renvoie None.
    Plusieurs lots (series) plutôt qu'un seul : un seul lot peut tomber par hasard sur un bon ou un mauvais tirage.
    """
    series = [serie for serie in series if serie]
    if not series:
        return None

    mesures = [repetitions_d_une_serie(serie) for serie in series]

    compteur = Counter()
    for serie in series:
        compteur.update(compter_expressions_repetees(serie))
    nb_reponses = sum(len(serie) for serie in series)

    return {
        "nb_reponses_par_serie": mesures[0]["nb_reponses"],
        "part_ouverture_moyenne": statistics.mean(m["part_ouverture"] for m in mesures),
        "ouvertures_par_serie": [m["ouverture"] for m in mesures],
        "parts_expressions_surveillees": {
            expression: statistics.mean(m["parts_expressions_surveillees"][expression] for m in mesures)
            for expression in CRITERES["expressions_surveillees"]
        },
        "expressions_frequentes": [
            {"expression": expression, "part": nombre / nb_reponses}
            for expression, nombre in compteur.most_common(CRITERES["nb_expressions_affichees"])
        ],
    }


def palier_de_note(note):
    """
    Le palier de ton d'une note, d'après agent/config.yaml.
    Sert à mesurer les répétitions séparément par palier.

    1 -> "1-4"     6 -> "5-6"     7 -> "7-8"     10 -> "9-10"
    """
    for palier in TONS:
        if palier["note_min"] <= note <= palier["note_max"]:
            return f"{palier['note_min']}-{palier['note_max']}"
    raise ValueError(f"Aucun palier pour la note {note}")


# --- 3. Défauts d'une réponse ---
# Les règles ci-dessous vérifient ce qui est mécanique. Les engagements risqués et les
# contradictions entre note et texte sont jugés par le LLM juge (juge.py), dont le verdict
# arrive ici sous forme de données : ce fichier reste sans appel au LLM.

def longueur_correcte(reponse):
    """
    Vrai si la réponse fait de 2 à 4 phrases (phrases_min et phrases_max dans criteres.yaml).

    "Merci pour votre retour."        ->  False  (1 phrase)
    "Un. Deux ! Trois ? Quatre."      ->  True   (4 phrases)
    "Un. Deux. Trois. Quatre. Cinq."  ->  False  (5 phrases)
    """
    return CRITERES["phrases_min"] <= len(phrases(reponse)) <= CRITERES["phrases_max"]


def tutoie(reponse):
    """
    Vrai si la réponse contient un mot du tutoiement (tu, te, toi, ton, ta, tes). C'est un défaut.

    "Merci pour ton retour."    ->  True   (défaut)
    "Merci pour votre retour."  ->  False
    """
    return any(mot in CRITERES["mots_tutoiement"] for mot in mots(reponse))


def en_francais(reponse):
    """
    Vrai si la réponse contient au moins 2 mots français courants
    (nous, vous, votre, merci, sommes, avec, les).

    "Merci pour votre retour, nous sommes ravis."  ->  True
    "Thank you for your review."                    ->  False  (défaut)
    """
    presents = set(mots(reponse)) & set(CRITERES["mots_francais"])
    return len(presents) >= CRITERES["nb_mots_francais_min"]


def cite_un_detail(reponse, details_attendus):
    """
    Vrai si la réponse reprend au moins un détail de l'avis.
    Sans détail attendu (avis très court comme « Bof. »), il n'y a rien à vérifier : True.

    ("Ravis que la terrasse vous ait plu.", ["piscine", "terrasse"])  ->  True
    ("Ravis que la terrasse vous ait plu.", ["piscine"])              ->  False  (défaut)
    ("Merci pour votre avis.", [])                                    ->  True
    """
    if not details_attendus:
        return True
    return any(contient(reponse, detail) for detail in details_attendus)


def criteres_en_defaut(verdict_juge):
    """
    Transforme le verdict du juge en liste simple des critères en défaut.

    {"enquete": {"defaut": True, "raison": "..."},
     "aveu_faute": {"defaut": False, "raison": "..."}}  ->  ["enquete"]
    None (le juge a échoué)                             ->  []
    """
    if not verdict_juge:
        return []
    return [critere for critere, avis in verdict_juge.items() if avis["defaut"]]


def defauts(reponse, cas, verdict_juge=None):
    """
    La fonction centrale : applique toutes les règles, ajoute les défauts vus par le juge,
    et renvoie la liste complète. Liste vide = aucun défaut.
    Sans verdict du juge, seules les règles sont vérifiées.

    réponse : "Merci, nous allons enquêter."   (1 seule phrase)
    cas     : détails attendus ["chauffage"], aucun mot interdit
    juge    : enquête en défaut
      ->  ["juge:enquete", "detail_absent", "longueur"]
    """
    trouves = [f"juge:{critere}" for critere in criteres_en_defaut(verdict_juge)]
    trouves += [f"interdit:{mot}" for mot in cas["interdits"] if contient(reponse, mot)]
    if not cite_un_detail(reponse, cas["details_attendus"]):
        trouves.append("detail_absent")
    if not longueur_correcte(reponse):
        trouves.append("longueur")
    if tutoie(reponse):
        trouves.append("tutoiement")
    if not en_francais(reponse):
        trouves.append("pas_en_francais")
    return trouves


def part_sans_defaut(liste_de_defauts):
    """
    Le chiffre résumé : la part des réponses qui n'ont aucun défaut.
    Reçoit une liste de défauts par réponse ; renvoie None s'il n'y a aucune réponse.

    [[], ["longueur"], [], ["tutoiement", "longueur"]]  ->  0.5  (2 réponses sur 4 sans défaut)
    """
    if not liste_de_defauts:
        return None
    return sum(not d for d in liste_de_defauts) / len(liste_de_defauts)


# --- 4. Score de confiance ---

def statistiques_scores(scores):
    """
    Résume une liste de scores ; renvoie None si la liste est vide.
    L'écart-type dit à quel point les scores sont étalés : proche de 0, ils sont tous
    pareils et le score ne distingue pas les réponses.

    [80, 85, 85, 90]
      ->  {"min": 80, "max": 90, "moyenne": 85, "ecart_type": 3.5,
           "valeur_la_plus_frequente": 85, "part_valeur_la_plus_frequente": 0.5}
    """
    if not scores:
        return None
    valeur, nombre = Counter(scores).most_common(1)[0]
    return {
        "min": min(scores),
        "max": max(scores),
        "moyenne": statistics.mean(scores),
        "ecart_type": statistics.pstdev(scores),
        "valeur_la_plus_frequente": valeur,
        "part_valeur_la_plus_frequente": nombre / len(scores),
    }


def dans_la_fourchette(score, cas):
    """
    Vrai si le score tombe dans la fourchette prévue pour ce cas (score_min, score_max).

    score 85, fourchette 75 à 100  ->  True
    score 85, fourchette 40 à 59   ->  False  (avis ambigu : le LLM aurait dû douter)
    """
    return cas["score_min"] <= score <= cas["score_max"]


def scores_selon_defauts(scores, liste_de_defauts):
    """
    Le vrai test du score : les réponses avec défaut ont-elles une note plus basse ?
    Si les deux moyennes sont proches, le score ne repère pas les mauvaises réponses.

    scores [90, 80, 50], défauts [[], [], ["longueur"]]
      ->  {"nb_avec_defaut": 1, "nb_sans_defaut": 2,
           "moyenne_avec_defaut": 50, "moyenne_sans_defaut": 85}   (le score est utile)
    """
    avec = [s for s, d in zip(scores, liste_de_defauts) if d]
    sans = [s for s, d in zip(scores, liste_de_defauts) if not d]
    return {
        "nb_avec_defaut": len(avec),
        "nb_sans_defaut": len(sans),
        "moyenne_avec_defaut": statistics.mean(avec) if avec else None,
        "moyenne_sans_defaut": statistics.mean(sans) if sans else None,
    }


# --- 5. Classifieur hors-sujet ---

def erreurs_hors_sujet(resultats):
    """
    Compare le verdict du classifieur au verdict attendu de chaque cas.
      - faux positif  : un bon avis classé hors sujet à tort (l'avis sur les courses)
      - faux négatif  : un vrai hors-sujet laissé passer (un spam)
      - erreur technique : le classifieur est tombé en panne (hors_sujet_obtenu = None),
        ce n'est ni un faux positif ni un faux négatif

    [{"id": "mixte_courses", "hors_sujet_attendu": False, "hors_sujet_obtenu": True, ...},
     {"id": "hs_spam",       "hors_sujet_attendu": True,  "hors_sujet_obtenu": False, ...}]
      ->  {"nb_cas_classes": 2, "nb_erreurs_techniques": 0,
           "faux_positifs": [{"id": "mixte_courses", "raison": "..."}],
           "faux_negatifs": [{"id": "hs_spam", "raison": "..."}]}
    """
    classes = [r for r in resultats if r["hors_sujet_obtenu"] is not None]
    faux_positifs = [r for r in classes if r["hors_sujet_obtenu"] and not r["hors_sujet_attendu"]]
    faux_negatifs = [r for r in classes if not r["hors_sujet_obtenu"] and r["hors_sujet_attendu"]]

    def detail(r):
        return {"id": r["id"], "raison": r["raison_hors_sujet"]}

    return {
        "nb_cas_classes": len(classes),
        "nb_erreurs_techniques": len(resultats) - len(classes),
        "faux_positifs": [detail(r) for r in faux_positifs],
        "faux_negatifs": [detail(r) for r in faux_negatifs],
    }
