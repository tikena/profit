"""
Routes liées au paiement.

Points de sécurité appliqués dans ce fichier :
1. Le webhook Stripe est vérifié avec la signature (stripe.Webhook.construct_event) —
   sans ça, n'importe qui pourrait forger une requête "paiement réussi".
2. Les événements sont traités de façon idempotente (voir ProcessedWebhookEvent).
2b. On ignore silencieusement les events qu'on ne gère pas explicitement (allowlist).
3. Le client ne choisit JAMAIS un prix ou un montant : il choisit un identifiant de
   plan ("pro_monthly"...) qui est mappé côté serveur vers un Stripe Price ID.
4. Toutes les routes sensibles exigent une session utilisateur valide (@login_required).
5. Le rate limiting protège /create-checkout-session et /webhook contre l'abus.
6. Aucune donnée de carte bancaire ne transite jamais par notre serveur (Stripe Checkout
   s'en charge intégralement — on ne stocke jamais de PAN/CVV, ce qui nous garde hors
   du scope PCI-DSS le plus lourd).
"""

import logging
from functools import wraps

import stripe
from flask import Blueprint, current_app, g, jsonify, redirect, request, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from models import Plan, ProcessedWebhookEvent, SubscriptionStatus, User, db

logger = logging.getLogger("profit.payments")

payments_bp = Blueprint("payments", __name__)
limiter = Limiter(key_func=get_remote_address)

# Mapping identifiant public -> (clé de config du Price ID Stripe, plan associé)
# C'est la SEULE liste de prix qui compte : jamais de montant envoyé par le client.
PLAN_CATALOG = {
    "pro_monthly": {"config_key": "STRIPE_PRICE_PRO_MONTHLY", "plan": Plan.PRO},
    "pro_yearly": {"config_key": "STRIPE_PRICE_PRO_YEARLY", "plan": Plan.PRO},
    "business_monthly": {"config_key": "STRIPE_PRICE_BUSINESS_MONTHLY", "plan": Plan.BUSINESS},
}

# Montants affichés par plan et par devise, UNIQUEMENT pour l'affichage côté
# frontend (sélecteur de devise). Le montant réellement facturé vient des
# `currency_options` définies sur le Price Stripe correspondant (voir README) :
# si ce tableau et Stripe divergent, c'est TOUJOURS Stripe qui fait foi côté
# facturation — garder les deux synchronisés est une tâche manuelle.
PRICE_DISPLAY = {
    "pro_monthly": {
        "eur": {"amount": "6,99", "symbol": "€", "suffix": "/mois"},
        "usd": {"amount": "7.69", "symbol": "$", "suffix": "/mo"},
        "gbp": {"amount": "6.29", "symbol": "£", "suffix": "/mo"},
        "mad": {"amount": "76", "symbol": "DH", "suffix": "/mois"},
        "cad": {"amount": "10.49", "symbol": "$", "suffix": "/mo"},
    },
    "pro_yearly": {
        "eur": {"amount": "69", "symbol": "€", "suffix": "/an"},
        "usd": {"amount": "75.99", "symbol": "$", "suffix": "/an"},
        "gbp": {"amount": "62.99", "symbol": "£", "suffix": "/an"},
        "mad": {"amount": "753", "symbol": "DH", "suffix": "/an"},
        "cad": {"amount": "103.99", "symbol": "$", "suffix": "/an"},
    },
    "business_monthly": {
        "eur": {"amount": "18,99", "symbol": "€", "suffix": "/mois"},
        "usd": {"amount": "20.89", "symbol": "$", "suffix": "/mo"},
        "gbp": {"amount": "17.09", "symbol": "£", "suffix": "/mo"},
        "mad": {"amount": "208", "symbol": "DH", "suffix": "/mois"},
        "cad": {"amount": "28.50", "symbol": "$", "suffix": "/mo"},
    },
}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = getattr(g, "current_user", None)
        if user is None:
            # Une route /api/... est appelée en JavaScript : elle attend du
            # JSON. Une page (dashboard, produits...) doit rediriger
            # l'utilisateur vers la connexion plutôt que lui afficher un
            # message d'erreur brut.
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentification requise"}), 401
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)

    return wrapped


def email_verified_required(view):
    """À empiler après @login_required. Bloque l'accès au produit tant que
    l'email n'a pas été confirmé par le code envoyé à l'inscription."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        user = g.current_user
        if not user.email_verified:
            if request.path.startswith("/api/"):
                return jsonify({"error": "email non vérifié"}), 403
            return redirect(url_for("verify_email_page"))
        return view(*args, **kwargs)

    return wrapped


@payments_bp.route("/create-checkout-session", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def create_checkout_session():
    """Crée une session Stripe Checkout pour l'utilisateur connecté."""
    data = request.get_json(silent=True) or {}
    plan_id = data.get("plan_id")
    requested_currency = (data.get("currency") or "").strip().lower()

    if plan_id not in PLAN_CATALOG:
        return jsonify({"error": "plan_id invalide"}), 400

    supported_currencies = current_app.config["SUPPORTED_CURRENCIES"]
    if requested_currency and requested_currency not in supported_currencies:
        return jsonify({"error": "devise non supportée"}), 400
    # Devise validée contre l'allowlist serveur, jamais transmise telle
    # quelle à Stripe sans ce contrôle. Si rien n'est demandé, on laisse
    # Stripe géolocaliser automatiquement la devise du client (Adaptive
    # Pricing), sauf si l'utilisateur a déjà une préférence enregistrée.
    currency = requested_currency or None

    user: User = g.current_user
    catalog_entry = PLAN_CATALOG[plan_id]
    price_id = current_app.config[catalog_entry["config_key"]]

    try:
        # On réutilise le customer Stripe existant si l'utilisateur en a déjà un,
        # pour ne pas dupliquer les clients et garder l'historique de facturation.
        if not user.stripe_customer_id:
            customer = stripe.Customer.create(
                email=user.email,
                metadata={"internal_user_id": str(user.id)},
            )
            user.stripe_customer_id = customer.id
            db.session.commit()

        session_kwargs = dict(
            mode="subscription",
            customer=user.stripe_customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=current_app.config["CHECKOUT_SUCCESS_URL"],
            cancel_url=current_app.config["CHECKOUT_CANCEL_URL"],
            # On relie explicitement la session à notre utilisateur interne :
            # utile dans le webhook pour retrouver le bon compte sans jamais
            # faire confiance à un identifiant fourni par le client.
            client_reference_id=str(user.id),
            # plan_id vient de PLAN_CATALOG (validé ci-dessus), jamais renvoyé
            # tel quel depuis l'entrée brute du client sans passer par l'allowlist.
            metadata={"internal_user_id": str(user.id), "plan_id": plan_id},
        )
        if currency:
            # Le Price Stripe doit avoir cette devise dans ses currency_options
            # (voir README), sinon Stripe rejette la session avec une erreur claire.
            session_kwargs["currency"] = currency

        session = stripe.checkout.Session.create(**session_kwargs)

        if currency:
            user.preferred_currency = currency
            db.session.commit()
    except stripe.error.StripeError as exc:
        logger.warning("Erreur Stripe lors de la création de la session: %s", exc)
        return jsonify({"error": "impossible de créer la session de paiement"}), 502

    return jsonify({"checkout_url": session.url}), 200


@payments_bp.route("/currencies", methods=["GET"])
def list_currencies():
    """Expose au frontend la liste des devises supportées et les montants
    d'affichage associés, pour construire le sélecteur de devise."""
    return jsonify(
        {
            "supported": current_app.config["SUPPORTED_CURRENCIES"],
            "default": current_app.config["DEFAULT_CURRENCY"],
            "prices": PRICE_DISPLAY,
        }
    ), 200


@payments_bp.route("/billing-portal", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def create_billing_portal_session():
    """Permet à l'utilisateur de gérer son abonnement (annuler, changer de
    formule, mettre à jour sa carte, voir ses factures) via le portail Stripe,
    qui gère lui-même l'aspect sécurité/PCI de ces opérations."""
    user: User = g.current_user
    if not user.stripe_customer_id:
        return jsonify({"error": "aucun abonnement associé à ce compte"}), 400

    portal_session = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=current_app.config["CHECKOUT_SUCCESS_URL"],
    )
    return jsonify({"portal_url": portal_session.url}), 200


@payments_bp.route("/webhook/stripe", methods=["POST"])
@limiter.limit("100 per minute")
def stripe_webhook():
    payload = request.get_data()  # corps brut, requis pour la vérification de signature
    sig_header = request.headers.get("Stripe-Signature", "")
    webhook_secret = current_app.config["STRIPE_WEBHOOK_SECRET"]

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        # Signature invalide ou payload corrompu -> on ne fait CONFIANCE À RIEN.
        logger.warning("Webhook Stripe rejeté : signature invalide")
        return jsonify({"error": "signature invalide"}), 400

    event_id = event["id"]
    event_type = event["type"]

    # Idempotence : si on a déjà traité cet event_id, on répond 200 sans rien refaire.
    if ProcessedWebhookEvent.query.get(event_id) is not None:
        return jsonify({"status": "déjà traité"}), 200

    handler = _WEBHOOK_HANDLERS.get(event_type)
    if handler is not None:
        handler(event["data"]["object"])

    db.session.add(ProcessedWebhookEvent(stripe_event_id=event_id))
    db.session.commit()

    return jsonify({"status": "ok"}), 200


def _find_user_for_object(stripe_object) -> "User | None":
    """Retrouve l'utilisateur interne à partir d'un objet Stripe, en se basant
    sur le customer_id Stripe stocké côté serveur — jamais sur une donnée
    envoyée par le navigateur."""
    customer_id = stripe_object.get("customer")
    if not customer_id:
        return None
    return User.query.filter_by(stripe_customer_id=customer_id).first()


def _handle_checkout_completed(session_obj):
    user = _find_user_for_object(session_obj)
    if user is None:
        logger.error("checkout.session.completed reçu pour un client Stripe inconnu")
        return

    user.stripe_subscription_id = session_obj.get("subscription")
    user.subscription_status = SubscriptionStatus.ACTIVE

    plan_id = (session_obj.get("metadata") or {}).get("plan_id")
    if plan_id in PLAN_CATALOG:
        user.plan = PLAN_CATALOG[plan_id]["plan"]

    db.session.commit()
    logger.info("Abonnement activé pour user_id=%s", user.id)


def _handle_subscription_updated(subscription_obj):
    user = _find_user_for_object(subscription_obj)
    if user is None:
        return

    status_map = {
        "active": SubscriptionStatus.ACTIVE,
        "past_due": SubscriptionStatus.PAST_DUE,
        "canceled": SubscriptionStatus.CANCELED,
        "unpaid": SubscriptionStatus.UNPAID,
    }
    user.subscription_status = status_map.get(subscription_obj.get("status"), SubscriptionStatus.NONE)
    db.session.commit()


def _handle_subscription_deleted(subscription_obj):
    user = _find_user_for_object(subscription_obj)
    if user is None:
        return

    user.plan = Plan.FREE
    user.subscription_status = SubscriptionStatus.CANCELED
    db.session.commit()
    logger.info("Abonnement annulé/expiré pour user_id=%s -> retour au plan FREE", user.id)


def _handle_payment_failed(invoice_obj):
    user = _find_user_for_object(invoice_obj)
    if user is None:
        return

    user.subscription_status = SubscriptionStatus.PAST_DUE
    db.session.commit()
    logger.warning("Échec de paiement pour user_id=%s", user.id)


# Allowlist explicite : seuls ces types d'événements déclenchent une action.
# Tout le reste est reçu (200 renvoyé à Stripe) mais ignoré, par sécurité et simplicité.
_WEBHOOK_HANDLERS = {
    "checkout.session.completed": _handle_checkout_completed,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "invoice.payment_failed": _handle_payment_failed,
}