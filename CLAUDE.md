# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

Projet de portfolio : un agent IA traite 200 avis clients par semaine pour un gestionnaire de locations saisonnières, de la collecte à la publication, avec validation humaine. Le code, les prompts et la documentation sont en français.

Ce qui compte le plus est la **mesure** (taux de retouche humaine, % automatisé, jeu de cas de test avec réponses attendues), pas la démo. La logique métier reste en Python testable ; n8n ne sert qu'à déclencher le pipeline.

`cdc-notes.md` contient le cadrage et les 12 décisions sur les zones grises du CDC (section 4). Sur ces points de comportement, il fait référence. Ses sections architecture et stack sont en partie dépassées : voir « Architecture » ci-dessous.

## Commandes

Environnement dans `venv/`, dépendances dans `requirements.txt`.

`OPENAI_API_KEY` doit être définie avant de lancer le seed ou tout code de `agent/` : le client OpenAI est créé à l'import et échoue sans clé.

Les imports sont à plat et `mock_pms.db` a un chemin relatif : lancer chaque service **depuis son propre dossier**.

```bash
# Mock PMS (port 8000, doc Swagger sur /docs)
cd mock_pms && uvicorn main:app --port 8000
cd mock_pms && python seed_data.py    # appelle OpenAI et AJOUTE des avis à chaque lancement
cd mock_pms && python decaler_dates.py   # redate les avis en attente (0 à 16 jours), à lancer juste avant un test

# Agent (le mock PMS doit tourner)
cd agent && python pipeline.py             # un run complet
cd agent && python pipeline.py --limite 20 # traite au plus 20 avis, les plus anciens d'abord (les suivants au prochain run)
cd agent && python pipeline.py --rejouer   # rejoue les avis en erreur
cd agent && uvicorn pipeline:app --port 8001   # POST /run-pipeline, POST /rejouer-erreurs, GET /run-status

# Tests sans OpenAI, ni vraie base, ni clé à définir (faux LLM, bases temporaires). Pas de linter configuré.
pytest tests/                                      # depuis la racine du projet
pytest tests/test_pipeline.py -k limite            # un seul fichier, filtré par nom de test

# Auto-tests intégrés
cd agent && python guardrails.py    # règles sans LLM ; le classifieur seulement si la clé commence par "sk-"
cd agent && python llm_client.py    # une génération réelle
```

## Architecture

Deux systèmes séparés, chacun avec sa base SQLite :

- **`mock_pms/`** simule le PMS externe du client. `mock_pms.db` n'est accessible que par son API HTTP, comme un vrai PMS. Il ne connaît que les 3 statuts du CDC. Transitions imposées par le serveur : `en_attente_evaluation` → `en_attente_publication` (`PATCH /reviews/{id}/statut`) → `publié` (uniquement `POST /reviews/{id}/publish`).
- **`agent/`** est le cœur du projet. `agent_internal.db` contient le détail du pipeline : table `avis` (état courant) et table `logs` (journal de chaque action).
- **`admin/`** (Flask, pas encore écrit) lit et écrit directement `agent_internal.db`, et n'appelle le PMS par HTTP qu'après validation humaine, pour publier. Sur un rejet, il appelle `pipeline.regenerer()`.
- **`eval/`** et **`n8n/`** ne sont pas encore écrits. n8n appelle `POST /run-pipeline` sur le port 8001.

Déroulement d'un run (`agent/pipeline.py`) : récupération des avis `en_attente_evaluation`, puis pour chacun, des plus anciens aux plus récents : garde-fous (`guardrails.evaluer_avis`), puis génération (`llm_client.generer_reponse`). L'agent ne modifie jamais le statut côté PMS : un avis déjà traité reste renvoyé par le PMS, et c'est `review_id_pms` dans `agent_internal.db` qui évite de le retraiter.

Comment lire l'état d'un avis dans `avis` :

- **À valider :** `statut_pipeline = en_validation` avec une réponse générée.
- **Revue manuelle :** `en_validation` avec `flag_doublon` ou `flag_hors_sujet`, et `reponse_generee` à NULL (aucune réponse n'est générée pour ces avis).
- **À traiter manuellement :** `statut_pipeline = à_traiter_manuellement`, après 3 rejets : l'humain écrit lui-même la réponse.
- **Écarté :** `statut_pipeline = écarté`, doublon ou hors-sujet écarté en revue manuelle : aucune réponse publiée, et l'avis reste `en_attente_evaluation` côté PMS.
- **File d'erreur :** `statut_erreur = en_erreur`, après 3 échecs ; `statut_pipeline` reste `nouveau`, ou `en_validation` si c'est une régénération qui a échoué. La file de validation doit donc exclure `en_erreur`.
- **Avis vides :** ni stockés ni traités ; ils sont seulement comptés dans le résumé de chaque run.

Les flags sont indépendants et peuvent coexister. Un avis très négatif reçoit quand même une réponse, avec `flag_surveillance`. Toute publication passe par un humain en V1, sans exception.

## Conventions et pièges

- **Valeurs métier :** seuils, tons, retries et modèle sont dans `agent/config.yaml`, jamais en dur dans le code Python.
- **Versions de prompts :** elles sont dans `agent/prompts/` et sélectionnées par `config.yaml`. Une fois l'éval en place, toute modification passe par un nouveau fichier (`v2.yaml`) pour pouvoir comparer les versions.
- **Score de confiance :** il note la réponse générée, pas l'avis, et vient du même appel LLM que la réponse. La difficulté de l'avis est portée par les flags.
- **Enums en SQL brut :** `SAEnum` stocke le **nom** de l'enum, pas sa valeur. Écrire `'VALIDE'`, `'SYNCHRONISE_PMS'`, `'OK'`, et non `'validé'`, `'synchronisé_pms'`, `'ok'`.
- **Statistiques :** les calculer depuis les colonnes structurées, jamais en parsant `logs.details`.
- **Changement de schéma :** `create_all` ne modifie pas une table existante. Après un changement dans `db_models.py`, supprimer `agent/agent_internal.db` (ou migrer si elle contient des données à garder).
