"""
eval/run_eval.py
Mesure la version du prompt indiquée dans agent/config.yaml sur le jeu de test.
Chaque réponse est contrôlée par des règles (metriques.py) et par un LLM juge (juge.py).
Appelle le vrai LLM, mais ne touche ni à agent_internal.db ni au PMS.

    cd eval && python run_eval.py

Écrit un rapport dans eval/resultats/ et affiche un résumé.
Les chiffres se comparent entre versions de prompt : le jeu de test est volontairement
chargé en cas difficiles, il ne représente pas un lot réel.

Le déroulement, dans l'ordre (voir main tout en bas) :
  1. lire les 39 avis de dataset.json                          -> charger_cas
  2. pour chaque avis : classifieur hors-sujet, puis 3 réponses
     générées, chacune jugée et mesurée                        -> evaluer_cas
  3. calculer tous les chiffres avec metriques.py               -> resumer, construire_rapport
  4. écrire le rapport JSON et son résumé .txt, afficher le résumé -> ecrire_rapport, texte_du_resume

Ce fichier ne calcule presque rien lui-même : il appelle le LLM, range les résultats,
et confie les calculs à metriques.py.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

DOSSIER = Path(__file__).parent
sys.path.insert(0, str(DOSSIER.parent / "agent"))

from guardrails import est_hors_sujet
from llm_client import CONFIG, LLMError, generer_reponse

import metriques
from juge import PROMPT_JUGE, REGLAGES as REGLAGES_JUGE, juger
from metriques import CRITERES


def charger_cas():
    """
    Lit le jeu de test : une liste de 39 cas.

    -> [{"id": "mixte_courses", "categorie": "mixte", "note": 9, "plateforme": "airbnb",
         "commentaire": "Super appartement ! Mais j'ai été un peu déçu par la livraison...",
         "hors_sujet_attendu": False, "reponse_attendue": "Merci beaucoup...",
         "details_attendus": ["appartement"], "interdits": ["excuses", ...],
         "score_min": 75, "score_max": 100},
        ...]
    """
    with open(DOSSIER / "dataset.json", encoding="utf-8") as f:
        return json.load(f)


# --- Évaluer un cas ---

def classer(cas):
    """
    Demande au classifieur de l'agent (guardrails.est_hors_sujet) si l'avis est hors sujet.
    Renvoie (verdict, raison). Si le classifieur tombe en panne, le verdict vaut None
    et la raison contient l'erreur : l'évaluation continue quand même.

    avis « Promo crypto : doublez votre capital... »  ->  (True, "Spam publicitaire.")
    avis sur les courses                             ->  (True, "Plainte sur une livraison...")  (faux positif)
    OpenAI en panne                                  ->  (None, "Classifieur hors-sujet : échec après 3 tentatives...")
    """
    try:
        return est_hors_sujet(cas["commentaire"])
    except LLMError as e:
        return None, str(e)


def faire_juger(cas, reponse):
    """
    Fait juger une réponse par le LLM juge (juge.py).
    Renvoie (verdict, erreur) : l'un des deux vaut toujours None.

    réponse « ... allons enquêter sur cette situation. »
      ->  ({"promesse_action": {"defaut": True, "raison": "« allons enquêter » promet..."},
            "enquete": {"defaut": True, "raison": "..."},
            "compensation": {"defaut": False, ...}, ...},
           None)
    juge en panne  ->  (None, "Juge : échec après 3 essais : ...")
    """
    try:
        return juger(cas["note"], cas["commentaire"], reponse), None
    except LLMError as e:
        return None, str(e)


def generer_un_essai(cas):
    """
    Un essai = une réponse générée par l'agent, puis jugée et mesurée.
    Une erreur est notée et n'arrête pas l'évaluation. Si seul le juge échoue, la réponse
    est gardée, mais elle est exclue du % sans défaut (on ne sait pas si elle en a).

    Tout va bien :
      ->  {"reponse": "Merci pour votre retour. Nous allons enquêter.",
           "score_confiance": 85,
           "dans_la_fourchette": True,
           "verdict_juge": {"promesse_action": {"defaut": True, ...}, ...},
           "erreur_juge": None,
           "defauts": ["juge:promesse_action", "juge:enquete", "detail_absent"]}
    Le juge échoue      ->  même chose, avec "verdict_juge": None et "erreur_juge": "Juge : échec..."
    La génération échoue ->  {"erreur": "Échec après 3 tentatives : ..."}
    """
    try:
        resultat = generer_reponse(cas["note"], cas["plateforme"], cas["commentaire"])
    except LLMError as e:
        return {"erreur": str(e)}

    reponse = resultat["reponse"]
    score = resultat["score_confiance"]
    verdict, erreur_juge = faire_juger(cas, reponse)
    return {
        "reponse": reponse,
        "score_confiance": score,
        "dans_la_fourchette": metriques.dans_la_fourchette(score, cas),
        "verdict_juge": verdict,
        "erreur_juge": erreur_juge,
        "defauts": metriques.defauts(reponse, cas, verdict),
    }


def evaluer_cas(cas, nb_essais):
    """
    Évalue un avis du jeu de test : le classifieur, puis nb_essais réponses (3).
    Le classifieur et la génération sont mesurés séparément : un avis faussement classé
    hors sujet reçoit quand même ses réponses. Seuls les vrais hors-sujet n'en reçoivent pas.

    avis « mixte_courses » (classé hors sujet à tort)
      ->  {"id": "mixte_courses", "categorie": "mixte", "note": 9, "commentaire": "...",
           "reponse_attendue": "...", "hors_sujet_attendu": False,
           "hors_sujet_obtenu": True, "raison_hors_sujet": "Plainte sur une livraison...",
           "essais": [essai 1, essai 2, essai 3]}      (voir generer_un_essai)
    avis « hs_spam » (vrai hors-sujet)
      ->  même forme, avec "essais": []                  (aucune réponse générée)
    """
    verdict, raison = classer(cas)
    essais = []
    if not cas["hors_sujet_attendu"]:
        essais = [generer_un_essai(cas) for _ in range(nb_essais)]

    return {
        "id": cas["id"],
        "categorie": cas["categorie"],
        "note": cas["note"],
        "commentaire": cas["commentaire"],
        "reponse_attendue": cas["reponse_attendue"],
        "hors_sujet_attendu": cas["hors_sujet_attendu"],
        "hors_sujet_obtenu": verdict,
        "raison_hors_sujet": raison,
        "essais": essais,
    }


# --- Résumer tous les cas ---

def essais_reussis(resultats):
    """
    Tous les essais de tous les avis, sauf ceux dont la génération a échoué.

    avis A : [essai, essai, {"erreur": ...}]   avis B : [essai, essai, essai]
      ->  les 5 essais réussis, dans une seule liste
    """
    return [essai for r in resultats for essai in r["essais"] if "erreur" not in essai]


def series_de_reponses(resultats, nb_essais):
    """
    Range les réponses en séries pour metriques.repetitions_moyennes.
    La série n°1 contient la 1re réponse de chaque avis, la série n°2 la 2e, etc.
    Une réponse en erreur est simplement absente de sa série.

    avis A : réponses A1, A2, A3     avis B : réponses B1, (erreur), B3
      ->  [[A1, B1], [A2], [A3, B3]]
    """
    series = []
    for numero in range(nb_essais):
        serie = []
        for r in resultats:
            if numero < len(r["essais"]) and "erreur" not in r["essais"][numero]:
                serie.append(r["essais"][numero]["reponse"])
        series.append(serie)
    return series


def repetitions(resultats, nb_essais):
    """
    Mesure les répétitions sur tous les avis, puis séparément pour chaque palier de note.
    Le palier compte : « sincèrement désolés » peut être à 50 % sur tous les avis,
    mais à 90 % sur les notes de 1 à 4.

    ->  {"tous_les_avis": {...},                      (voir metriques.repetitions_moyennes)
         "par_palier_de_note": {"1-4": {...}, "5-6": {...}, "7-8": {...}, "9-10": {...}}}
    """
    paliers = sorted({metriques.palier_de_note(r["note"]) for r in resultats},
                     key=lambda palier: int(palier.split("-")[0]))
    par_palier = {}
    for palier in paliers:
        du_palier = [r for r in resultats if metriques.palier_de_note(r["note"]) == palier]
        par_palier[palier] = metriques.repetitions_moyennes(series_de_reponses(du_palier, nb_essais))

    return {
        "tous_les_avis": metriques.repetitions_moyennes(series_de_reponses(resultats, nb_essais)),
        "par_palier_de_note": par_palier,
    }


def verdicts_du_juge(resultats):
    """
    Compte les réponses en défaut pour chaque critère du juge, et garde la raison de chacune
    pour qu'on puisse relire le juge.

    ->  {"nb_reponses_par_critere": {"promesse_action": 12, "compensation": 0, "enquete": 3,
                                     "aveu_faute": 2, "contradiction_ignoree": 4},
         "passages": [{"id": "risque_punaises", "critere": "enquete",
                       "raison": "« allons vérifier le matelas » annonce une vérification."},
                      ...]}
    """
    par_critere = {critere: 0 for critere in PROMPT_JUGE["criteres"]}
    passages = []
    for r in resultats:
        for essai in r["essais"]:
            for critere in metriques.criteres_en_defaut(essai.get("verdict_juge")):
                par_critere[critere] += 1
                passages.append({"id": r["id"], "critere": critere,
                                 "raison": essai["verdict_juge"][critere]["raison"]})
    return {"nb_reponses_par_critere": par_critere, "passages": passages}


def resumer(resultats, nb_essais):
    """
    Tous les chiffres du rapport, calculés à partir des résultats de evaluer_cas.

    ->  {"nb_cas": 39, "nb_reponses": 102, "nb_erreurs_generation": 0, "nb_erreurs_juge": 0,
         "part_reponses_sans_defaut": 0.4,               (le chiffre résumé : 40 %)
         "hors_sujet": {"faux_positifs": [...], ...},    (problème 1)
         "repetitions": {...},                           (problème 2)
         "score": {"statistiques": {...},                (problème 3)
                   "part_dans_la_fourchette": 0.55,
                   "selon_defauts": {...}},
         "juge": {...}}                                  (problème 4 : engagements risqués)
    Les valeurs ci-dessus sont des exemples, pas les vrais chiffres de la v1.
    """
    essais = essais_reussis(resultats)
    nb_erreurs = sum("erreur" in essai for r in resultats for essai in r["essais"])
    # Le % sans défaut et le score selon les défauts ne portent que sur les réponses jugées
    juges = [essai for essai in essais if essai["verdict_juge"] is not None]
    scores = [essai["score_confiance"] for essai in essais]

    return {
        "nb_cas": len(resultats),
        "nb_reponses": len(essais),
        "nb_erreurs_generation": nb_erreurs,
        "nb_erreurs_juge": len(essais) - len(juges),
        "part_reponses_sans_defaut": metriques.part_sans_defaut([essai["defauts"] for essai in juges]),
        "hors_sujet": metriques.erreurs_hors_sujet(resultats),
        "repetitions": repetitions(resultats, nb_essais),
        "score": {
            "statistiques": metriques.statistiques_scores(scores),
            "part_dans_la_fourchette": (
                sum(essai["dans_la_fourchette"] for essai in essais) / len(essais) if essais else None
            ),
            "selon_defauts": metriques.scores_selon_defauts(
                [essai["score_confiance"] for essai in juges], [essai["defauts"] for essai in juges]),
        },
        "juge": verdicts_du_juge(resultats),
    }


def construire_rapport(resultats, nb_essais):
    """
    Le rapport complet : les réglages utilisés, le résumé, puis le détail de chaque cas.
    Les réglages sont notés pour savoir ce qu'on compare : deux rapports ne se comparent
    que s'ils ont le même prompt_juge.

    ->  {"date": "2026-11-10T14:30:00", "prompt_version": "v1", "prompt_hors_sujet": "hors_sujet_v1",
         "modele": "gpt-4o-mini", "temperature": 0.7, "prompt_juge": "juge_v1",
         "modele_juge": "gpt-4o", "nb_essais": 3,
         "resume": {...},       (voir resumer)
         "cas": [...]}          (les 39 résultats de evaluer_cas, avec toutes les réponses)
    """
    return {
        "date": datetime.now().isoformat(timespec="seconds"),
        "prompt_version": CONFIG["llm"]["prompt_version"],
        "prompt_hors_sujet": CONFIG["llm"]["prompt_hors_sujet"],
        "modele": CONFIG["llm"]["model"],
        "temperature": CONFIG["llm"]["temperature"],
        "prompt_juge": REGLAGES_JUGE["prompt_version"],
        "modele_juge": REGLAGES_JUGE["model"],
        "nb_essais": nb_essais,
        "resume": resumer(resultats, nb_essais),
        "cas": resultats,
    }


# --- Écrire et afficher ---

def ecrire_rapport(rapport, dossier=DOSSIER / "resultats"):
    """
    Écrit le rapport complet en JSON, et son résumé en texte à côté, avec le même nom.
    Renvoie le chemin du JSON. De nouveaux fichiers à chaque lancement.

    ->  eval/resultats/v1_2026-11-10_143000.json   (tout le détail, pour creuser un cas)
        eval/resultats/v1_2026-11-10_143000.txt    (le résumé, celui affiché à l'écran)
    """
    dossier.mkdir(exist_ok=True)
    horodatage = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    chemin = dossier / f"{rapport['prompt_version']}_{horodatage}.json"
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2)
    chemin.with_suffix(".txt").write_text(texte_du_resume(rapport), encoding="utf-8")
    return chemin


def pourcentage(part):
    """0.4 -> "40%"     None -> "n/a" """
    return "n/a" if part is None else f"{part:.0%}"


def nombre(valeur):
    """85.333 -> "85.3"     None -> "n/a" """
    return "n/a" if valeur is None else f"{valeur:.1f}"


def texte_du_resume(rapport):
    """
    Le résumé du rapport, problème par problème, en texte. main() l'affiche à l'écran et
    ecrire_rapport l'enregistre dans un .txt : les deux sont donc identiques. Exemple (chiffres inventés) :

    === Prompt v1 : 39 cas, 102 réponses, 0 erreurs ===
    Réponses sans aucun défaut : 40% (sur les réponses jugées ; 0 erreurs du juge)

    1. Hors-sujet
       Faux positifs : 1, faux négatifs : 0, erreurs techniques : 0
       - mixte_courses : Plainte sur une livraison sans lien avec le séjour.

    2. Répétitions : moyenne sur 3 lots de 34 réponses (un lot = une réponse par avis, comme une vraie semaine)
       Commencent par la même formule : 55% des réponses d'un lot (« nous vous remercions »)
       Contiennent « nous vous remercions », n'importe où : 55%
       Contiennent « sincèrement désolés », n'importe où : 30%
       Les mêmes mesures, par palier de note :
       - Notes 1-4 (12 réponses par lot) : même début 80%, « nous vous remercions » 80%, « sincèrement désolés » 75%
       - ...

    3. Score de confiance
       De 70 à 95, écart-type 6.2, 85 dans 45% des cas
       Score dans la fourchette prévue pour son avis (dataset.json) : 55% des réponses
       Score moyen avec défaut : 84.0 (61 réponses), sans défaut : 86.5 (41 réponses)

    4. Juge (gpt-4o, juge_v1) : réponses en défaut
       promesse_action : 12
       ...
    """
    lignes = []
    resume = rapport["resume"]
    rep = resume["repetitions"]
    score = resume["score"]
    hs = resume["hors_sujet"]

    lignes.append(f"\n=== Prompt {rapport['prompt_version']} : {resume['nb_cas']} cas, "
          f"{resume['nb_reponses']} réponses, {resume['nb_erreurs_generation']} erreurs ===")
    lignes.append(f"Réponses sans aucun défaut : {pourcentage(resume['part_reponses_sans_defaut'])} "
          f"(sur les réponses jugées ; {resume['nb_erreurs_juge']} erreurs du juge)")

    lignes.append("\n1. Hors-sujet")
    lignes.append(f"   Faux positifs : {len(hs['faux_positifs'])}, faux négatifs : {len(hs['faux_negatifs'])}, "
          f"erreurs techniques : {hs['nb_erreurs_techniques']}")
    for erreur in hs["faux_positifs"] + hs["faux_negatifs"]:
        lignes.append(f"   - {erreur['id']} : {erreur['raison']}")

    tous = rep["tous_les_avis"]
    if tous:
        lignes.append(f"\n2. Répétitions : moyenne sur {len(tous['ouvertures_par_serie'])} lots de "
              f"{tous['nb_reponses_par_serie']} réponses (un lot = une réponse par avis, "
              f"comme une vraie semaine)")
        formules = sorted(set(tous["ouvertures_par_serie"]))
        precision = "" if len(formules) == 1 else ", la plus fréquente change selon le lot"
        lignes.append(f"   Commencent par la même formule : {pourcentage(tous['part_ouverture_moyenne'])} "
              f"des réponses d'un lot (« {' », « '.join(formules)} »{precision})")
        for expression, part in tous["parts_expressions_surveillees"].items():
            lignes.append(f"   Contiennent « {expression} », n'importe où : {pourcentage(part)}")
        lignes.append("   Les mêmes mesures, par palier de note :")
        for palier, mesure in rep["par_palier_de_note"].items():
            if mesure:
                lignes.append(f"   - Notes {palier} ({mesure['nb_reponses_par_serie']} réponses par lot) : "
                      f"même début {pourcentage(mesure['part_ouverture_moyenne'])}, "
                      + ", ".join(f"« {e} » {pourcentage(p)}"
                                  for e, p in mesure["parts_expressions_surveillees"].items()))
    else:
        lignes.append("\n2. Répétitions : aucune réponse à mesurer")

    lignes.append("\n3. Score de confiance")
    stats = score["statistiques"]
    if stats:
        lignes.append(f"   De {stats['min']} à {stats['max']}, écart-type {stats['ecart_type']:.1f}, "
              f"{stats['valeur_la_plus_frequente']} dans "
              f"{pourcentage(stats['part_valeur_la_plus_frequente'])} des cas")
    lignes.append(f"   Score dans la fourchette prévue pour son avis (dataset.json) : "
          f"{pourcentage(score['part_dans_la_fourchette'])} des réponses")
    selon = score["selon_defauts"]
    lignes.append(f"   Score moyen avec défaut : {nombre(selon['moyenne_avec_defaut'])} ({selon['nb_avec_defaut']} réponses), "
          f"sans défaut : {nombre(selon['moyenne_sans_defaut'])} ({selon['nb_sans_defaut']} réponses)")

    lignes.append(f"\n4. Juge ({rapport['modele_juge']}, {rapport['prompt_juge']}) : réponses en défaut")
    for critere, nb in resume["juge"]["nb_reponses_par_critere"].items():
        lignes.append(f"   {critere} : {nb}")
    lignes.append("   Les raisons du juge sont dans le rapport complet (resume > juge > passages).")
    return "\n".join(lignes)


def main():
    """Le programme complet : évalue les 39 cas un par un, puis écrit et affiche le rapport."""
    cas = charger_cas()
    nb_essais = CRITERES["nb_essais"]

    resultats = []
    for numero, un_cas in enumerate(cas, start=1):
        print(f"[{numero}/{len(cas)}] {un_cas['id']}")
        resultats.append(evaluer_cas(un_cas, nb_essais))

    rapport = construire_rapport(resultats, nb_essais)
    chemin = ecrire_rapport(rapport)
    print(texte_du_resume(rapport))
    print(f"\nRapport complet : {chemin}")
    print(f"Résumé : {chemin.with_suffix('.txt')}")


if __name__ == "__main__":
    main()
