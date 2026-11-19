"""
admin/app.py
L'interface de validation (Flask) : l'humain relit chaque réponse avant publication.

Lancement (depuis admin/, le mock PMS doit tourner pour publier) :
  export OPENAI_API_KEY=sk-...          (un rejet régénère la réponse avec le LLM)
  export ADMIN_MOT_DE_PASSE=...         (le seul compte, décision #11)
  flask --app app run --port 5000

Les pages :
  /                    file de validation : valider (tel quel ou modifié) ou rejeter
  /revue               revue manuelle : doublons et hors-sujet, à écarter ou à rédiger à la main
  /manuel              à traiter manuellement : rejetés 3 fois, ou choisis en revue manuelle
  /tableau-de-bord     les chiffres : taux de retouche, % automatisé, délais...

Flask lit et écrit directement agent_internal.db (comme l'agent), et n'appelle le PMS par HTTP
que pour publier une réponse validée. Toute publication passe par un clic de l'humain (décision #1).
"""
import hmac
import os
import secrets
import sys
from datetime import date, datetime
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

sys.path.insert(0, str(Path(__file__).parents[1] / "agent"))  # imports à plat, comme le reste du projet

import pipeline  # noqa: E402
import statistiques  # noqa: E402
from db_models import ActionLog, Acteur, Avis, StatutErreur, StatutPipeline, get_session  # noqa: E402
from guardrails import est_prioritaire  # noqa: E402
from llm_client import CONFIG  # noqa: E402

app = Flask(__name__)
# Sans ADMIN_SECRET_KEY, une clé au hasard : il faut juste se reconnecter après un redémarrage
app.secret_key = os.environ.get("ADMIN_SECRET_KEY") or secrets.token_hex(32)
# Le cookie de session ne part jamais depuis un autre site : un formulaire forgé ailleurs
# (CSRF) arrive ici sans session, donc sans droits
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"


# --- Base de l'agent ---

def ouvrir_session():
    """Une session sur agent_internal.db. pipeline.engine est lu à chaque appel : les tests le remplacent."""
    return get_session(pipeline.engine)


def avis_ou_404(db, avis_id):
    """L'avis demandé, ou une page 404 s'il n'existe pas."""
    avis = db.get(Avis, avis_id)
    if avis is None:
        abort(404)
    return avis


def age_en_jours(avis):
    """Nombre de jours depuis l'avis. Un avis du 1er publié le 11  ->  10"""
    return (date.today() - avis.date_avis).days


def trier_par_urgence(liste):
    """
    Les avis prioritaires d'abord (calculé maintenant, pas lu dans la colonne priorite_delai,
    qui n'est mise à jour qu'à chaque run de l'agent), puis les plus anciens.
    """
    return sorted(liste, key=lambda a: (not est_prioritaire(a.date_avis), a.date_avis))


def _requete_validation(db):
    return db.query(Avis).filter(
        Avis.statut_pipeline == StatutPipeline.EN_VALIDATION,
        Avis.statut_erreur == StatutErreur.OK,
        Avis.reponse_generee.isnot(None),
    )


def _requete_revue(db):
    return db.query(Avis).filter(
        Avis.statut_pipeline == StatutPipeline.EN_VALIDATION,
        Avis.statut_erreur == StatutErreur.OK,
        Avis.reponse_generee.is_(None),
        (Avis.flag_doublon == True) | (Avis.flag_hors_sujet == True),  # noqa: E712
    )


def _requete_manuelle(db):
    return db.query(Avis).filter(Avis.statut_pipeline == StatutPipeline.A_TRAITER_MANUELLEMENT)


def file_de_validation(db):
    """Les réponses de l'agent à relire. Les avis en erreur n'y sont jamais (CLAUDE.md)."""
    return trier_par_urgence(_requete_validation(db).all())


def file_de_revue(db):
    """Les doublons et hors-sujet : l'agent n'a pas écrit de réponse."""
    return trier_par_urgence(_requete_revue(db).all())


def file_manuelle(db):
    return trier_par_urgence(_requete_manuelle(db).all())


def en_revue_manuelle(avis):
    """Vrai si l'avis est bien dans la file de revue (mêmes conditions que _requete_revue)."""
    return (avis.statut_pipeline == StatutPipeline.EN_VALIDATION
            and avis.statut_erreur == StatutErreur.OK
            and avis.reponse_generee is None
            and (avis.flag_doublon or avis.flag_hors_sujet))


def publications_a_refaire(db):
    """Validés par l'humain, mais le PMS n'a pas accepté la publication (PMS arrêté, par exemple)."""
    return db.query(Avis).filter(Avis.statut_pipeline == StatutPipeline.VALIDE).all()


# --- Publication sur le PMS ---

def publier_sur_pms(review_id_pms, texte):
    """
    Les deux étapes imposées par le PMS : PATCH vers en_attente_publication, puis POST /publish.
    Renvoie None si la réponse est publiée, sinon le message d'erreur à afficher.

    Un nouvel essai après un échec doit marcher même si une étape était déjà passée :
      - PATCH répond 409 si l'avis est déjà en_attente_publication  ->  on continue ;
      - POST répond 409 si l'avis est déjà publié                   ->  on vérifie, et c'est un succès.
    """
    base = CONFIG["pms"]["base_url"]
    attente = CONFIG["pms"]["timeout_secondes"]
    try:
        r = requests.patch(f"{base}/reviews/{review_id_pms}/statut",
                           json={"statut": "en_attente_publication"}, timeout=attente)
        if r.status_code not in (200, 409):
            return f"Le PMS a refusé le changement de statut ({r.status_code})."

        r = requests.post(f"{base}/reviews/{review_id_pms}/publish",
                          json={"reponse": texte}, timeout=attente)
        if r.status_code == 200:
            return None
        if r.status_code == 409:
            deja = requests.get(f"{base}/reviews/{review_id_pms}", timeout=attente)
            if deja.status_code == 200 and deja.json()["statut"] == "publié":
                return None
        return f"Le PMS a refusé la publication ({r.status_code})."
    except requests.RequestException:
        return "Le PMS ne répond pas : la réponse est validée, publie-la plus tard."


def publier(db, avis):
    """Publie un avis déjà validé, et le marque synchronisé si le PMS l'a accepté."""
    erreur = publier_sur_pms(avis.review_id_pms, avis.reponse_validee)
    if erreur:
        flash(erreur, "erreur")
        return
    avis.statut_pipeline = StatutPipeline.SYNCHRONISE_PMS
    pipeline._log(db, avis.review_id_pms, ActionLog.PUBLIE, Acteur.HUMAIN, avis.reponse_validee)
    db.commit()
    flash(f"Réponse publiée (avis {avis.review_id_pms}).", "ok")


# Utilisables dans toutes les pages, y compris dans les macros de _avis.html
app.jinja_env.globals.update(age_en_jours=age_en_jours, est_prioritaire=est_prioritaire,
                             limite_jours=CONFIG["seuils"]["delai_publication_jours"])


# --- Connexion (un seul compte, décision #11) ---

def connexion_requise(page):
    @wraps(page)
    def verifiee(*args, **kwargs):
        if not session.get("connecte"):
            return redirect(url_for("connexion"))
        return page(*args, **kwargs)
    return verifiee


@app.route("/connexion", methods=["GET", "POST"])
def connexion():
    if request.method == "POST":
        attendu = os.environ.get("ADMIN_MOT_DE_PASSE", "")
        donne = request.form.get("mot_de_passe", "")
        # Sans mot de passe défini, personne ne peut se connecter
        if attendu and hmac.compare_digest(donne.encode(), attendu.encode()):
            session["connecte"] = True
            return redirect(url_for("validation"))
        flash("Mot de passe incorrect." if attendu else
              "ADMIN_MOT_DE_PASSE n'est pas défini : impossible de se connecter.", "erreur")
    return render_template("connexion.html")


@app.route("/deconnexion")
def deconnexion():
    session.clear()
    return redirect(url_for("connexion"))


# --- Les pages ---

@app.context_processor
def compteurs_du_menu():
    """Le nombre d'avis dans chaque file, affiché dans le menu de toutes les pages."""
    if not session.get("connecte"):
        return {}
    db = ouvrir_session()
    try:
        # Seulement des COUNT : pas besoin de charger ni de trier les avis pour trois nombres
        return {"compteurs": {"validation": _requete_validation(db).count(),
                              "revue": _requete_revue(db).count(),
                              "manuel": _requete_manuelle(db).count()}}
    finally:
        db.close()


@app.route("/")
@connexion_requise
def validation():
    db = ouvrir_session()
    try:
        return render_template("validation.html", avis=file_de_validation(db),
                               a_refaire=publications_a_refaire(db))
    finally:
        db.close()


@app.route("/revue")
@connexion_requise
def revue():
    db = ouvrir_session()
    try:
        return render_template("revue.html", avis=file_de_revue(db))
    finally:
        db.close()


@app.route("/manuel")
@connexion_requise
def manuel():
    db = ouvrir_session()
    try:
        return render_template("manuel.html", avis=file_manuelle(db))
    finally:
        db.close()


@app.route("/tableau-de-bord")
@connexion_requise
def tableau_de_bord():
    db = ouvrir_session()
    try:
        return render_template("tableau.html", stats=statistiques.tableau_de_bord(db.query(Avis).all()))
    finally:
        db.close()


# --- Les actions (boutons) ---

@app.route("/avis/<int:avis_id>/valider", methods=["POST"])
@connexion_requise
def valider(avis_id):
    """
    Valide la réponse du formulaire (telle quelle ou modifiée), puis la publie.
    Le texte validé est gardé dans reponse_validee : le comparer à reponse_generee donne le
    taux de retouche.
    """
    # Seule notre page /manuel est un retour valable : toute autre valeur (notamment une URL
    # externe glissée dans le formulaire) serait une redirection ouverte
    retour = url_for("manuel") if request.form.get("retour") == url_for("manuel") else url_for("validation")
    # Le navigateur envoie les sauts de ligne en \r\n : sans normalisation, une réponse
    # multi-lignes validée telle quelle serait comptée « modifiée » (fausse retouche)
    texte = request.form.get("reponse", "").replace("\r\n", "\n").strip()
    if not texte:
        flash("Une réponse vide ne peut pas être publiée.", "erreur")
        return redirect(retour)

    db = ouvrir_session()
    try:
        avis = avis_ou_404(db, avis_id)
        if (avis.statut_pipeline not in (StatutPipeline.EN_VALIDATION, StatutPipeline.A_TRAITER_MANUELLEMENT)
                or avis.statut_erreur != StatutErreur.OK):
            flash("Cet avis n'est plus à valider.", "erreur")
            return redirect(retour)

        if not avis.reponse_generee:
            facon = "écrite à la main"
        elif texte == avis.reponse_generee.strip():
            facon = "telle quelle"
        else:
            facon = "modifiée"
        avis.reponse_validee = texte
        avis.statut_pipeline = StatutPipeline.VALIDE
        avis.date_validation = datetime.utcnow()
        pipeline._log(db, avis.review_id_pms, ActionLog.VALIDE, Acteur.HUMAIN, f"Réponse {facon}")
        db.commit()

        publier(db, avis)
        return redirect(retour)
    finally:
        db.close()


@app.route("/avis/<int:avis_id>/publier", methods=["POST"])
@connexion_requise
def republier(avis_id):
    """Nouvel essai de publication d'une réponse déjà validée."""
    db = ouvrir_session()
    try:
        avis = avis_ou_404(db, avis_id)
        if avis.statut_pipeline == StatutPipeline.VALIDE:
            publier(db, avis)
        return redirect(url_for("validation"))
    finally:
        db.close()


@app.route("/avis/<int:avis_id>/rejeter", methods=["POST"])
@connexion_requise
def rejeter(avis_id):
    """Rejet : l'agent régénère (2 fois au plus), puis l'avis passe à traiter manuellement."""
    db = ouvrir_session()
    try:
        avis = avis_ou_404(db, avis_id)
        if (avis.statut_pipeline != StatutPipeline.EN_VALIDATION or not avis.reponse_generee
                or avis.statut_erreur != StatutErreur.OK):
            flash("Cet avis n'est plus à valider.", "erreur")
        elif pipeline.regenerer(db, avis):
            flash(f"Nouvelle réponse générée ({avis.nb_tentatives_regen}/"
                  f"{CONFIG['generation']['max_regenerations']}).", "ok")
        elif avis.statut_pipeline == StatutPipeline.A_TRAITER_MANUELLEMENT:
            flash("Limite de régénérations atteinte : l'avis est à traiter manuellement.", "erreur")
        else:
            flash("La génération a échoué : l'avis est en file d'erreur "
                  "(cd agent && python pipeline.py --rejouer).", "erreur")
        return redirect(url_for("validation"))
    finally:
        db.close()


@app.route("/avis/<int:avis_id>/ecarter", methods=["POST"])
@connexion_requise
def ecarter(avis_id):
    """Revue manuelle : le doublon ou le hors-sujet est confirmé, aucune réponse ne sera publiée."""
    db = ouvrir_session()
    try:
        avis = avis_ou_404(db, avis_id)
        # Garde-fou : un onglet resté ouvert (ou un POST forgé) ne doit pas écarter un avis
        # déjà validé, publié ou sans signalement
        if not en_revue_manuelle(avis):
            flash("Cet avis n'est pas (ou plus) en revue manuelle.", "erreur")
            return redirect(url_for("revue"))
        avis.statut_pipeline = StatutPipeline.ECARTE
        pipeline._log(db, avis.review_id_pms, ActionLog.ECARTE, Acteur.HUMAIN,
                      "doublon" if avis.flag_doublon else "hors-sujet")
        db.commit()
        flash(f"Avis {avis.review_id_pms} écarté.", "ok")
        return redirect(url_for("revue"))
    finally:
        db.close()


@app.route("/avis/<int:avis_id>/repondre", methods=["POST"])
@connexion_requise
def repondre_a_la_main(avis_id):
    """Revue manuelle : le signalement était faux (un avis mixte, par exemple), l'humain répond."""
    db = ouvrir_session()
    try:
        avis = avis_ou_404(db, avis_id)
        if not en_revue_manuelle(avis):
            flash("Cet avis n'est pas (ou plus) en revue manuelle.", "erreur")
            return redirect(url_for("revue"))
        avis.statut_pipeline = StatutPipeline.A_TRAITER_MANUELLEMENT
        db.commit()
        flash(f"Avis {avis.review_id_pms} déplacé dans « à traiter manuellement ».", "ok")
        return redirect(url_for("manuel"))
    finally:
        db.close()


if __name__ == "__main__":
    app.run(port=5000, debug=True)
