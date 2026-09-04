"""
Point d'entrée de l'application.
"""

import os

from dotenv import load_dotenv

load_dotenv()  # charge le fichier .env AVANT que Config ne lise les variables d'environnement

import stripe
from flask import Flask, g, render_template
from flask_talisman import Talisman
from flask_wtf import CSRFProtect

from auth import auth_bp, load_logged_in_user
from config import Config
from models import db
from routes import limiter, payments_bp

csrf = CSRFProtect()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    stripe.api_key = app.config["STRIPE_SECRET_KEY"]

    db.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)

    # Force HTTPS en production uniquement. En local (FLASK_ENV=development
    # dans .env), pas de certificat SSL sur 127.0.0.1 : forcer HTTPS ferait
    # échouer toutes les requêtes avec une erreur de connexion sécurisée.
    is_production = os.environ.get("FLASK_ENV", "production") == "production"
    Talisman(app, force_https=is_production, strict_transport_security=is_production)

    app.before_request(load_logged_in_user)

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(payments_bp, url_prefix="/api/payments")

    # Le webhook Stripe est vérifié par signature (voir routes.py), pas par
    # jeton CSRF : Stripe ne peut pas fournir de cookie de session, donc on
    # exempte UNIQUEMENT cette route précise, jamais tout le blueprint.
    csrf.exempt(app.view_functions["payments.stripe_webhook"])

    @app.get("/pricing")
    def pricing_page():
        return render_template("pricing.html", user=g.current_user)

    @app.get("/login")
    def login_page():
        return render_template("login.html")

    @app.get("/register")
    def register_page():
        return render_template("register.html")

    with app.app_context():
        db.create_all()

    return app


if __name__ == "__main__":
    application = create_app()
    # debug=False en toutes circonstances hors développement local :
    # le mode debug expose un débogueur interactif = exécution de code arbitraire.
    application.run(debug=False)