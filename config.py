"""
Configuration de l'application.
RÈGLE DE SÉCURITÉ #1 : aucune clé secrète n'est écrite en dur dans le code.
Tout vient des variables d'environnement (fichier .env en local, secrets manager en prod).
"""

import os


def _require_env(name: str) -> str:
    """Force la présence d'une variable d'environnement critique au démarrage
    plutôt que de planter plus tard (ou pire, de tourner en mode non sécurisé)."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Variable d'environnement manquante : {name}. "
            f"L'application refuse de démarrer sans elle."
        )
    return value


class Config:
    # --- Base de données ---
    SQLALCHEMY_DATABASE_URI = _require_env("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Flask ---
    SECRET_KEY = _require_env("FLASK_SECRET_KEY")  # utilisé pour signer les sessions/cookies

    # --- Stripe ---
    STRIPE_SECRET_KEY = _require_env("STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET = _require_env("STRIPE_WEBHOOK_SECRET")

    # IDs des Price créés dans le dashboard Stripe (jamais des montants en dur envoyés par le client)
    STRIPE_PRICE_PRO_MONTHLY = _require_env("STRIPE_PRICE_PRO_MONTHLY")
    STRIPE_PRICE_PRO_YEARLY = _require_env("STRIPE_PRICE_PRO_YEARLY")
    STRIPE_PRICE_BUSINESS_MONTHLY = _require_env("STRIPE_PRICE_BUSINESS_MONTHLY")

    # URLs de redirection après paiement
    CHECKOUT_SUCCESS_URL = os.environ.get(
        "CHECKOUT_SUCCESS_URL", "https://app.profit.example/dashboard?checkout=success"
    )
    CHECKOUT_CANCEL_URL = os.environ.get(
        "CHECKOUT_CANCEL_URL", "https://app.profit.example/pricing?checkout=cancelled"
    )

    # --- Multi-devises ---
    # Ces codes doivent correspondre aux currency_options configurées sur
    # chaque Price Stripe (voir routes.py -> PRICE_DISPLAY et le README).
    SUPPORTED_CURRENCIES = ["eur", "usd", "gbp", "mad", "cad"]
    DEFAULT_CURRENCY = "eur"

    # --- Sécurité générale ---
    # SESSION_COOKIE_SECURE=True bloque l'envoi du cookie en HTTP simple :
    # nécessaire en production (HTTPS), mais empêcherait toute connexion
    # de fonctionner en local sur http://127.0.0.1. On l'active donc
    # seulement quand FLASK_ENV=production (valeur par défaut).
    _is_production = os.environ.get("FLASK_ENV", "production") == "production"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = _is_production
    SESSION_COOKIE_SAMESITE = "Lax"
    PREFERRED_URL_SCHEME = "https" if _is_production else "http"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 7  # 7 jours

    # CSRF (Flask-WTF) : protège toutes les routes POST/PUT/DELETE utilisant
    # la session cookie. Le webhook Stripe est exempté explicitement (voir
    # app.py), car il est authentifié autrement (signature Stripe).
    WTF_CSRF_TIME_LIMIT = None