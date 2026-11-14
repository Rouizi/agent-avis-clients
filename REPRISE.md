# Reprise du projet : où on en est et quoi faire ensuite

État au 2026-11-11. Ce fichier sert à reprendre le projet dans une nouvelle session (ou un autre compte Claude) sans rien réexpliquer. Il complète `CLAUDE.md` (commandes, architecture, pièges) et `cdc-notes.md` (cadrage, 12 décisions, journal) : en cas de doute, ces deux fichiers font référence.

## 1. Le projet en bref

Un agent IA répond aux avis clients (200 par semaine) d'un gestionnaire de locations saisonnières. Les avis viennent d'un PMS (simulé par `mock_pms/`), l'agent écrit une réponse avec gpt-4o-mini, un humain la valide dans une interface Flask, puis elle est publiée sur le PMS. Aucune publication sans validation humaine en V1.

Ce que le projet doit prouver (portfolio) : **la mesure**, pas la démo.
- un jeu de cas de test annoté, avec réponses attendues (`eval/`) ;
- le taux de retouche humaine et le % d'avis automatisés (tableau de bord Flask) ;
- une amélioration du prompt mesurée : v1, puis v2, sur le même jeu de test.

## 2. État de chaque partie

| Partie | État | Ce qu'elle fait |
|---|---|---|
| `mock_pms/` | fini | Faux PMS en FastAPI (port 8000), 3 statuts, transitions imposées |
| `agent/` | fini, prompt v1 | Récupère les avis, garde-fous (doublon, hors-sujet, très négatif, délai), génère la réponse et un score de confiance, régénère après un rejet |
| `eval/` | fini, v1 mesurée | 39 cas annotés, 3 essais par cas, règles Python + juge gpt-4o, rapport `.json` + résumé `.txt` |
| `admin/` (Flask) | fini, pas encore utilisé en vrai | Connexion, file de validation, rejet, revue manuelle, file manuelle, tableau de bord |
| Prompt v2 | **à faire** | Voir section 4 |
| `README.md` | **à faire** | Voir section 4 |
| `n8n/` | optionnel | Un simple appel planifié à `POST /run-pipeline` (port 8001) |

Tests : `pytest tests/` depuis la racine, 187 tests, sans OpenAI ni vraie base. Historique : `git log --oneline` (`8e5243d` = eval).

## 3. Les chiffres de la v1 (eval, 2026-11-11)

Rapport : `eval/resultats/v1_2026-11-11_025132.txt` (résumé) et `.json` (détail).

| Problème | Chiffre v1 |
|---|---|
| Réponses sans aucun défaut (chiffre résumé) | **49 %** |
| Faux hors-sujet | 2 (les deux avis mixtes qui parlent d'une livraison) |
| Répétitions, notes 1-4 | 100 % commencent par « nous vous remercions », 86 % contiennent « sincèrement désolés » |
| Score de confiance | 50 à 95, moyenne 82,4 avec défaut contre 86,1 sans : il ne repère presque pas les mauvaises réponses |
| Engagements risqués (juge) | promesse_action 43 réponses sur 102, enquete 12, contradiction_ignoree 8, aveu_faute 2, compensation 0 |

Avant de s'appuyer sur le 43, relire quelques raisons du juge dans le JSON (`resume` > `juge` > `passages`) pour vérifier qu'il n'est pas trop sévère.

Le test réel sur 20 avis (copie figée : `eval/run_reel_v1.json`) avait déjà montré les mêmes problèmes : 13 réponses sur 19 qui commencent par « Nous vous remercions », 10 avec « sincèrement désolés », des scores presque toujours à 85.

## 4. Ce qui reste, dans l'ordre

### 4.1 Utiliser Flask en vrai (environ 1 h)

```bash
cd mock_pms && uvicorn main:app --port 8000          # terminal 1
cd admin && ADMIN_MOT_DE_PASSE=... flask --app app run --port 5000   # terminal 2 (OPENAI_API_KEY définie)
```

La base contient déjà les 20 avis du test réel : 19 à valider et 1 en revue manuelle (le faux hors-sujet des courses). Les valider (certaines telles quelles, d'autres corrigées, en rejeter une ou deux) donne un **vrai taux de retouche** pour le README. Corriger chaque promesse ou formule répétée, comme le ferait le vrai gestionnaire.

### 4.2 Prompt v2 (environ 2-3 h, le plus important pour le portfolio)

Règle du projet : **ne jamais modifier `v1.yaml`**. Créer de nouveaux fichiers et les sélectionner dans `agent/config.yaml`.

Dans `agent/prompts/v2.yaml` (copie de `v1.yaml`, puis) :
- retirer la consigne « engagement d'amélioration » pour les avis négatifs : c'est la cause probable des 43 promesses (le ton de `config.yaml` dit déjà « sans rien promettre ») ;
- retirer ou réécrire les exemples qui promettent (« pour que cela ne se reproduise pas », « afin d'y remédier », « Nous allons reprendre ») ;
- l'exemple noté 2/10 contient mot pour mot « Nous vous remercions pour votre retour et sommes sincèrement désolés » : varier les débuts des exemples, et demander de ne pas toujours commencer par un remerciement ;
- la consigne de score cite « une réponse à 85 » : la retirer, et demander de baisser le score quand la réponse est délicate (avis ambigu, très court, contradiction note/texte).

Dans `agent/prompts/hors_sujet_v2.yaml` : la définition cite « plainte sur un colis ou un autre service », ce qui pousse à rejeter les avis mixtes. Préciser qu'un avis qui parle **aussi** du logement n'est pas hors-sujet, et ajouter un exemple d'avis mixte.

Puis :
1. dans `config.yaml` : `prompt_version: v2` et `prompt_hors_sujet: hors_sujet_v2` ;
2. `pytest tests/` (un test vérifie qu'aucun avis du jeu de test n'est copié dans les prompts : ne pas utiliser les avis de `dataset.json` comme exemples) ;
3. `cd eval && python run_eval.py` ;
4. comparer les deux `.txt` de `eval/resultats/` (même juge `juge_v1`, donc comparables) ;
5. noter le résultat dans le journal de `cdc-notes.md`.

Ne pas changer la grille du juge en même temps, sinon les chiffres ne se comparent plus.

### 4.3 README.md (environ 1-2 h)

Pour un recruteur, en français :
- le problème et la solution en 5 lignes ;
- un schéma du flux : PMS → agent → validation Flask → publication ;
- comment lancer (reprendre les commandes de `CLAUDE.md`) ;
- **les chiffres** : tableau v1 contre v2 (eval), taux de retouche et % automatisé (Flask) ;
- les choix et leurs raisons : validation humaine obligatoire, juge LLM plutôt que listes de mots, 3 lots, le jeu chargé en cas difficiles ;
- les limites : jeu de 39 cas écrit à la main, juge non validé sur un jeu annoté à part, un seul compte Flask.

### 4.4 Optionnel

- n8n : un workflow planifié (lundi 8 h, `planification` dans `config.yaml`) qui appelle `POST http://localhost:8001/run-pipeline`. Ne pas exposer l'API de l'agent hors de la machine (pas d'authentification).
- Petits restes listés dans `cdc-notes.md` (« À traiter plus tard ») : `datetime.utcnow` déprécié, compteurs `vides`/`invalides`.
- Revue manuelle : aujourd'hui un faux hors-sujet reçoit une réponse écrite à la main ; on pourrait ajouter un bouton « faire générer par l'agent ».

## 5. À sauvegarder tout de suite

**Le dépôt git n'a aucun remote : tout est seulement sur cet ordinateur.** Le pousser sur GitHub (le dépôt servira de portfolio) :

```bash
git remote add origin git@github.com:<ton-compte>/agent-avis-client.git
git push -u origin master
```

Vérifier avant que `.env` et les `.db` ne partent pas (ils sont dans `.gitignore`).

## 6. Façon de travailler (pour le prochain Claude)

- Tout en français : code, commentaires, prompts, documentation.
- L'utilisateur relit tout et veut comprendre : expliquer simplement, avec des exemples concrets d'entrée et de sortie. Chaque fonction a un docstring avec un exemple de ce qu'elle reçoit et renvoie : garder ce style.
- Tests simples et lisibles : un test = un cas, pas de boucle ni de fixture compliquée (voir `tests/test_guardrails.py`).
- Pas de commit sans demande explicite.
- L'utilisateur a suivi le cours d'Anthropic sur les évaluations : ne pas insister pour valider le juge sur un jeu annoté à part (décision du 2026-11-05).
- Temps disponible : 4-5 h par jour pendant les vacances.

## 7. Phrase pour démarrer la prochaine session

> Lis CLAUDE.md, cdc-notes.md et REPRISE.md. On reprend à la section 4 de REPRISE.md : [étape choisie].
