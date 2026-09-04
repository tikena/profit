"""
Authentification.

Points de sécurité appliqués :
1. Mots de passe jamais stockés en clair : hachage avec Werkzeug (PBKDF2-SHA256,
   salé automatiquement). Migration possible vers argon2 plus tard si besoin.
2. Réponses volontairement identiques en cas d'email inconnu ou de mauvais mot de
   passe ("email ou mot de passe incorrect") pour ne pas révéler quels emails existent.
3. Limitation du débit sur /login et /register pour freiner le brute-force.
4. Session Flask signée (SECRET_KEY) + cookies HttpOnly/Secure/SameSite (voir config.py).
5. Régénération de session à la connexion pour éviter la fixation de session.
6. Validation stricte des entrées (email, longueur du mot de passe) avant tout accès BDD.
"""

import logging
import re
import uuid

from flask import Blueprint, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from models import User, db
from routes import limiter

logger = logging.getLogger("profit.auth")

auth_bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 10


def _validate_credentials(email: str, password: str) -> str | None:
    """Retourne un message d'erreur si invalide, sinon None."""
    if not email or not EMAIL_RE.match(email):
        return "adresse email invalide"
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return f"le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères"
    return None


@auth_bp.route("/register", methods=["POST"])
@limiter.limit("5 per minute")
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    error = _validate_credentials(email, password)
    if error:
        return jsonify({"error": error}), 400

    if User.query.filter_by(email=email).first() is not None:
        # Message générique : on ne confirme jamais qu'un email est déjà pris,
        # pour éviter l'énumération de comptes.
        return jsonify({"error": "impossible de créer ce compte"}), 400

    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash=generate_password_hash(password),
    )
    db.session.add(user)
    db.session.commit()

    _start_session(user)
    logger.info("Nouveau compte créé user_id=%s", user.id)
    return jsonify({"status": "compte créé", "email": user.email}), 201


@auth_bp.route("/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    generic_error = {"error": "email ou mot de passe incorrect"}, 401

    user = User.query.filter_by(email=email).first()
    if user is None or not check_password_hash(user.password_hash, password):
        return generic_error

    _start_session(user)
    return jsonify({"status": "connecté", "email": user.email}), 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status": "déconnecté"}), 200


@auth_bp.route("/me", methods=["GET"])
def me():
    user = g.current_user
    if user is None:
        return jsonify({"error": "non connecté"}), 401
    return jsonify(
        {
            "email": user.email,
            "plan": user.plan.value,
            "subscription_status": user.subscription_status.value,
            "is_premium": user.has_active_premium(),
        }
    ), 200


def _start_session(user: User) -> None:
    # session.clear() avant d'écrire le nouvel id : évite la fixation de session
    # (un ancien identifiant de session ne doit jamais être réutilisé après login).
    session.clear()
    session["user_id"] = str(user.id)
    session.permanent = True


def load_logged_in_user() -> None:
    """À enregistrer avec app.before_request. Peuple g.current_user pour
    toute la durée de la requête, à partir du seul identifiant stocké
    dans la session signée côté serveur (jamais depuis une donnée client)."""
    user_id = session.get("user_id")
    g.current_user = User.query.get(user_id) if user_id else None