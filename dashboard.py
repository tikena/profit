"""
Routes du tableau de bord "Profit".

Toutes les routes ici exigent un utilisateur connecté (@login_required,
réutilisé depuis routes.py). Aucune donnée financière n'est jamais
accessible sans session valide, et chaque requête est filtrée par
`user_id` — un utilisateur ne peut jamais voir les données d'un autre.
"""

import csv
import io
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, g, jsonify, render_template, request

import analytics
import ai_advisor
from models import Product, Transaction, TransactionType, db
from routes import email_verified_required, login_required

logger = logging.getLogger("profit.dashboard")

dashboard_bp = Blueprint("dashboard", __name__)

# Limites raisonnables pour éviter qu'un import mal formé ne bloque le
# serveur ou ne remplisse la base de données par erreur.
MAX_IMPORT_ROWS = 5000
MAX_DESCRIPTION_LENGTH = 500

_TYPE_ALIASES = {
    "revenu": TransactionType.REVENUE,
    "revenus": TransactionType.REVENUE,
    "vente": TransactionType.REVENUE,
    "ventes": TransactionType.REVENUE,
    "revenue": TransactionType.REVENUE,
    "depense": TransactionType.EXPENSE,
    "dépense": TransactionType.EXPENSE,
    "depenses": TransactionType.EXPENSE,
    "dépenses": TransactionType.EXPENSE,
    "expense": TransactionType.EXPENSE,
    "achat": TransactionType.EXPENSE,
}


def _resolve_period(period_key: str) -> tuple:
    """Traduit un identifiant de période court (venant du frontend) en
    dates de début/fin. Valeur par défaut : 30 derniers jours."""

    today = date.today()
    if period_key == "7d":
        return today - timedelta(days=6), today
    if period_key == "month":
        return today.replace(day=1), today
    if period_key == "year":
        return today.replace(month=1, day=1), today
    # "30d" ou toute valeur inconnue : période par défaut sûre.
    return today - timedelta(days=29), today


def _parse_amount(raw: str) -> Decimal:
    cleaned = raw.strip().replace(" ", "").replace(",", ".")
    return Decimal(cleaned)


def _parse_date(raw: str) -> date:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"format de date non reconnu : {raw!r} (utilisez AAAA-MM-JJ)")


def _get_or_create_product(user_id, name: str) -> Product:
    name = name.strip()
    product = Product.query.filter_by(user_id=user_id, name=name).first()
    if product is None:
        product = Product(user_id=user_id, name=name)
        db.session.add(product)
        db.session.flush()  # obtenir product.id sans committer
    return product


@dashboard_bp.route("/dashboard")
@login_required
@email_verified_required
def dashboard_page():
    return render_template("dashboard.html", user=g.current_user)


@dashboard_bp.route("/products")
@login_required
@email_verified_required
def products_page():
    return render_template("products.html", user=g.current_user)


@dashboard_bp.route("/simulator")
@login_required
@email_verified_required
def simulator_page():
    return render_template("simulator.html", user=g.current_user)


@dashboard_bp.route("/import")
@login_required
@email_verified_required
def import_page():
    return render_template("import.html", user=g.current_user)


@dashboard_bp.route("/api/dashboard/summary", methods=["GET"])
@login_required
@email_verified_required
def api_summary():
    user = g.current_user
    start, end = _resolve_period(request.args.get("period", "30d"))
    currency = user.preferred_currency

    summary = analytics.get_summary(user.id, currency, start, end)
    expense_breakdown = analytics.get_expense_breakdown(user.id, currency, start, end)
    product_breakdown = analytics.get_product_breakdown(user.id, currency, start, end)
    health_score = analytics.compute_health_score(summary, expense_breakdown)
    alerts = analytics.generate_alerts(summary, product_breakdown, expense_breakdown)
    insights = ai_advisor.generate_ai_advisor_text(summary, product_breakdown, expense_breakdown)
    ai_powered = insights is not None
    if insights is None:
        insights = analytics.generate_advisor_insights(summary, product_breakdown, expense_breakdown)
    monthly_trend = analytics.get_monthly_trend(user.id, currency)

    return jsonify(
        {
            "currency": currency,
            "summary": summary,
            "expense_breakdown": expense_breakdown,
            "product_breakdown": product_breakdown,
            "health_score": health_score,
            "alerts": alerts,
            "insights": insights,
            "insights_ai_powered": ai_powered,
            "monthly_trend": monthly_trend,
        }
    ), 200


@dashboard_bp.route("/api/dashboard/simulate", methods=["POST"])
@login_required
@email_verified_required
def api_simulate():
    user = g.current_user
    data = request.get_json(silent=True) or {}
    start, end = _resolve_period(request.args.get("period", "30d"))
    currency = user.preferred_currency

    summary = analytics.get_summary(user.id, currency, start, end)
    expense_breakdown = analytics.get_expense_breakdown(user.id, currency, start, end)

    def _pct(key):
        try:
            return float(data.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    result = analytics.simulate_scenario(
        summary,
        expense_breakdown,
        price_change_pct=_pct("price_change_pct"),
        volume_change_pct=_pct("volume_change_pct"),
        ad_spend_change_pct=_pct("ad_spend_change_pct"),
        other_costs_change_pct=_pct("other_costs_change_pct"),
    )
    result["currency"] = currency
    return jsonify(result), 200


@dashboard_bp.route("/api/dashboard/transactions", methods=["POST"])
@login_required
@email_verified_required
def api_add_transaction():
    """Ajout manuel d'une transaction unique (formulaire du tableau de
    bord, alternative à l'import CSV)."""

    user = g.current_user
    data = request.get_json(silent=True) or {}

    type_key = str(data.get("type", "")).strip().lower()
    t_type = _TYPE_ALIASES.get(type_key)
    if t_type is None:
        return jsonify({"error": "type invalide (attendu : revenu ou depense)"}), 400

    category = str(data.get("category", "")).strip()
    if not category:
        return jsonify({"error": "catégorie requise"}), 400

    try:
        amount = _parse_amount(str(data.get("amount", "")))
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return jsonify({"error": "montant invalide"}), 400

    try:
        occurred_on = _parse_date(str(data.get("date", "")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    product_name = str(data.get("product", "")).strip()
    product = _get_or_create_product(user.id, product_name) if product_name else None

    description = str(data.get("description", "")).strip()[:MAX_DESCRIPTION_LENGTH]

    transaction = Transaction(
        user_id=user.id,
        product_id=product.id if product else None,
        type=t_type,
        category=category,
        amount=amount,
        currency=user.preferred_currency,
        occurred_on=occurred_on,
        description=description or None,
    )
    db.session.add(transaction)
    db.session.commit()

    return jsonify({"status": "ajouté"}), 201


@dashboard_bp.route("/api/dashboard/import", methods=["POST"])
@login_required
@email_verified_required
def api_import_csv():
    """Import CSV. Colonnes attendues : type,category,amount,date,product,description
    (product et description optionnelles). L'import est atomique : si une
    seule ligne est invalide, rien n'est enregistré, pour éviter un import
    partiel qui fausserait les calculs sans que l'utilisateur le sache."""

    user = g.current_user

    if "file" not in request.files:
        return jsonify({"error": "aucun fichier reçu"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "aucun fichier sélectionné"}), 400

    try:
        raw_bytes = file.read()
        text = raw_bytes.decode("utf-8-sig")  # gère le BOM Excel
    except UnicodeDecodeError:
        return jsonify({"error": "encodage de fichier non supporté (utilisez UTF-8)"}), 400

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return jsonify({"error": "fichier CSV vide ou invalide"}), 400

    normalized_fields = {name.strip().lower() for name in reader.fieldnames}
    required = {"type", "category", "amount", "date"}
    missing = required - normalized_fields
    if missing:
        return jsonify({"error": f"colonnes manquantes : {', '.join(sorted(missing))}"}), 400

    rows_to_insert = []
    errors = []
    product_cache = {}

    for line_number, row in enumerate(reader, start=2):  # ligne 1 = en-tête
        if line_number - 1 > MAX_IMPORT_ROWS:
            errors.append(f"trop de lignes (maximum {MAX_IMPORT_ROWS})")
            break

        normalized_row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}

        type_key = normalized_row.get("type", "").lower()
        t_type = _TYPE_ALIASES.get(type_key)
        if t_type is None:
            errors.append(f"ligne {line_number} : type invalide {normalized_row.get('type', '')!r}")
            continue

        category = normalized_row.get("category", "")
        if not category:
            errors.append(f"ligne {line_number} : catégorie manquante")
            continue

        try:
            amount = _parse_amount(normalized_row.get("amount", ""))
            if amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            errors.append(f"ligne {line_number} : montant invalide {normalized_row.get('amount', '')!r}")
            continue

        try:
            occurred_on = _parse_date(normalized_row.get("date", ""))
        except ValueError as exc:
            errors.append(f"ligne {line_number} : {exc}")
            continue

        product_name = normalized_row.get("product", "") or normalized_row.get("produit", "")
        product_id = None
        if product_name:
            if product_name not in product_cache:
                product_cache[product_name] = _get_or_create_product(user.id, product_name)
            product_id = product_cache[product_name].id

        description = normalized_row.get("description", "")[:MAX_DESCRIPTION_LENGTH] or None

        rows_to_insert.append(
            Transaction(
                user_id=user.id,
                product_id=product_id,
                type=t_type,
                category=category,
                amount=amount,
                currency=user.preferred_currency,
                occurred_on=occurred_on,
                description=description,
            )
        )

    if errors:
        db.session.rollback()  # annule les Product créés en cache le temps de la validation
        return jsonify({"error": "import annulé, corrigez les lignes suivantes", "details": errors[:50]}), 400

    if not rows_to_insert:
        db.session.rollback()
        return jsonify({"error": "aucune ligne valide trouvée dans le fichier"}), 400

    db.session.add_all(rows_to_insert)
    db.session.commit()

    logger.info("Import CSV réussi pour user_id=%s : %d lignes", user.id, len(rows_to_insert))
    return jsonify({"status": "importé", "count": len(rows_to_insert)}), 201