"""
Modèles de données.

Principe de sécurité clé : le statut Premium (`plan`, `subscription_status`)
n'est JAMAIS modifié directement par une requête venant du navigateur.
Il n'est modifié que par le serveur, en réaction à un événement Stripe
vérifié (webhook signé). Voir routes.py -> stripe_webhook().
"""

import enum
import uuid
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import UUID

db = SQLAlchemy()


class Plan(enum.Enum):
    FREE = "free"
    PRO = "pro"
    BUSINESS = "business"


class SubscriptionStatus(enum.Enum):
    NONE = "none"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = db.Column(db.String(320), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    # Lien vers Stripe : c'est la SOURCE DE VÉRITÉ pour tout ce qui est facturation.
    stripe_customer_id = db.Column(db.String(255), unique=True, nullable=True, index=True)
    stripe_subscription_id = db.Column(db.String(255), unique=True, nullable=True, index=True)

    plan = db.Column(db.Enum(Plan), nullable=False, default=Plan.FREE)
    subscription_status = db.Column(
        db.Enum(SubscriptionStatus), nullable=False, default=SubscriptionStatus.NONE
    )
    current_period_end = db.Column(db.DateTime, nullable=True)

    # Devise choisie par l'utilisateur pour la facturation (code ISO 4217,
    # minuscules, ex: "eur", "usd", "mad"). N'affecte que la présentation
    # et la devise de la session Stripe Checkout ; validée à chaque usage
    # contre Config.SUPPORTED_CURRENCIES, jamais utilisée telle quelle.
    preferred_currency = db.Column(db.String(3), nullable=False, default="eur")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def has_active_premium(self) -> bool:
        """Seule fonction à utiliser dans le reste de l'app pour savoir
        si l'utilisateur a droit aux fonctionnalités payantes."""
        return (
            self.plan in (Plan.PRO, Plan.BUSINESS)
            and self.subscription_status == SubscriptionStatus.ACTIVE
        )


class ProcessedWebhookEvent(db.Model):
    """Table d'idempotence : Stripe peut renvoyer le même événement plusieurs
    fois. On enregistre chaque event_id traité pour ne jamais appliquer
    deux fois la même mise à jour (ex : double activation, double crédit)."""

    __tablename__ = "processed_webhook_events"

    stripe_event_id = db.Column(db.String(255), primary_key=True)
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)


class TransactionType(enum.Enum):
    REVENUE = "revenue"
    EXPENSE = "expense"


class Product(db.Model):
    """Un produit vendu par l'utilisateur. Sert à regrouper les
    transactions pour l'analyse de rentabilité par produit."""

    __tablename__ = "products"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "name", name="uq_product_user_name"),)


class Transaction(db.Model):
    """Une ligne de revenu ou de dépense. C'est la table brute à partir de
    laquelle tous les calculs du tableau de bord sont dérivés — rien n'est
    stocké en cache/pré-calculé, pour garantir que les chiffres reflètent
    toujours les données réelles de l'utilisateur."""

    __tablename__ = "transactions"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False, index=True)
    product_id = db.Column(UUID(as_uuid=True), db.ForeignKey("products.id"), nullable=True, index=True)

    type = db.Column(db.Enum(TransactionType), nullable=False)
    # Catégorie libre : "vente", "publicité", "livraison", "commission",
    # "frais de paiement", "remboursement", "salaire", "autre"...
    category = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="eur")
    occurred_on = db.Column(db.Date, nullable=False, index=True)
    description = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship("Product", backref="transactions")