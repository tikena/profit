"""
Moteur d'analyse financière.

Tout est calculé à la volée à partir des transactions brutes (aucun chiffre
pré-calculé stocké) : ça garantit que le tableau de bord reflète toujours
l'état réel des données de l'utilisateur, même après une modification ou
une suppression de transaction.

Le "conseiller IA" ici est basé sur des règles déterministes (pas d'appel à
un modèle de langage externe) : chaque phrase provient directement d'un
calcul vérifiable sur les données de l'utilisateur, jamais d'un texte
générique. Simple, gratuit à faire tourner, et sans risque d'invention.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from models import Product, Transaction, TransactionType

# Catégories de dépenses considérées comme "publicité" pour les calculs
# et alertes liés aux campagnes publicitaires.
AD_CATEGORIES = {"publicité", "publicite", "ads", "marketing"}


def _to_float(value) -> float:
    """Convertit un Decimal SQL (ou None) en float pour l'affichage/JSON."""
    if value is None:
        return 0.0
    return float(value)


def _date_range_query(user_id, currency, start: date, end: date):
    return Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.currency == currency,
        Transaction.occurred_on >= start,
        Transaction.occurred_on <= end,
    )


def get_summary(user_id, currency: str, start: date, end: date) -> dict:
    """Chiffre d'affaires, dépenses, bénéfice, marge, commandes, panier
    moyen pour une période, plus l'évolution par rapport à la période
    précédente de même durée."""

    period_length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_length - 1)

    def totals_for(range_start, range_end):
        rows = (
            _date_range_query(user_id, currency, range_start, range_end)
            .with_entities(Transaction.type, func.sum(Transaction.amount))
            .group_by(Transaction.type)
            .all()
        )
        revenue = expenses = Decimal("0")
        for t_type, total in rows:
            if t_type == TransactionType.REVENUE:
                revenue = total or Decimal("0")
            else:
                expenses = total or Decimal("0")

        order_count = _date_range_query(user_id, currency, range_start, range_end).filter(
            Transaction.type == TransactionType.REVENUE
        ).count()

        return revenue, expenses, order_count

    revenue, expenses, order_count = totals_for(start, end)
    prev_revenue, prev_expenses, _ = totals_for(prev_start, prev_end)

    profit = revenue - expenses
    prev_profit = prev_revenue - prev_expenses

    margin = float(profit / revenue * 100) if revenue else 0.0
    avg_order = float(revenue / order_count) if order_count else 0.0

    def evolution(current, previous):
        if not previous:
            return None
        return float((current - previous) / abs(previous) * 100)

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "revenue": _to_float(revenue),
        "expenses": _to_float(expenses),
        "profit": _to_float(profit),
        "margin": round(margin, 1),
        "order_count": order_count,
        "avg_order": round(avg_order, 2),
        "revenue_evolution": evolution(revenue, prev_revenue),
        "profit_evolution": evolution(profit, prev_profit),
    }


def get_expense_breakdown(user_id, currency: str, start: date, end: date) -> list:
    """Dépenses regroupées par catégorie, triées de la plus grosse à la
    plus petite — sert au camembert de dépenses et aux alertes publicité."""

    rows = (
        _date_range_query(user_id, currency, start, end)
        .filter(Transaction.type == TransactionType.EXPENSE)
        .with_entities(Transaction.category, func.sum(Transaction.amount))
        .group_by(Transaction.category)
        .order_by(func.sum(Transaction.amount).desc())
        .all()
    )
    return [{"category": category, "amount": _to_float(total)} for category, total in rows]


def get_product_breakdown(user_id, currency: str, start: date, end: date) -> list:
    """Rentabilité par produit : chiffre d'affaires, coût, bénéfice, marge,
    nombre de ventes. Les transactions sans produit associé sont ignorées
    ici (elles restent dans les totaux généraux)."""

    products = Product.query.filter_by(user_id=user_id).all()
    results = []

    for product in products:
        rows = (
            _date_range_query(user_id, currency, start, end)
            .filter(Transaction.product_id == product.id)
            .with_entities(Transaction.type, func.sum(Transaction.amount))
            .group_by(Transaction.type)
            .all()
        )
        revenue = expenses = Decimal("0")
        for t_type, total in rows:
            if t_type == TransactionType.REVENUE:
                revenue = total or Decimal("0")
            else:
                expenses = total or Decimal("0")

        sales_count = _date_range_query(user_id, currency, start, end).filter(
            Transaction.product_id == product.id,
            Transaction.type == TransactionType.REVENUE,
        ).count()

        if revenue == 0 and expenses == 0:
            continue  # produit sans activité sur la période : on l'omet

        profit = revenue - expenses
        margin = float(profit / revenue * 100) if revenue else 0.0

        results.append(
            {
                "product_id": str(product.id),
                "name": product.name,
                "revenue": _to_float(revenue),
                "expenses": _to_float(expenses),
                "profit": _to_float(profit),
                "margin": round(margin, 1),
                "sales_count": sales_count,
            }
        )

    results.sort(key=lambda p: p["profit"], reverse=True)
    return results


def compute_health_score(summary: dict, expense_breakdown: list) -> dict:
    """Score de santé sur 100, décomposé en sous-scores. Chaque sous-score
    est une fonction simple et documentée d'un ratio réel — pas une boîte
    noire : on peut toujours expliquer pourquoi le score est ce qu'il est."""

    revenue = summary["revenue"]
    margin = summary["margin"]

    # Rentabilité : 0% de marge -> 0, 40%+ de marge -> 100.
    rentabilite = max(0, min(100, margin / 40 * 100))

    # Publicité : ratio dépenses pub / chiffre d'affaires. 0% -> 100,
    # 30%+ -> 0 (une pub qui mange 30% du CA est jugée à surveiller).
    ad_spend = sum(e["amount"] for e in expense_breakdown if e["category"].lower() in AD_CATEGORIES)
    ad_ratio = (ad_spend / revenue * 100) if revenue else 0
    publicite = max(0, min(100, 100 - (ad_ratio / 30 * 100)))

    # Dépenses : ratio dépenses totales / chiffre d'affaires. 50% -> 100
    # (dépenser la moitié du CA est confortable), 100%+ -> 0 (aucune marge).
    expense_ratio = (summary["expenses"] / revenue * 100) if revenue else 100
    depenses = max(0, min(100, 100 - ((expense_ratio - 50) / 50 * 100))) if expense_ratio > 50 else 100

    overall = round((rentabilite + publicite + depenses) / 3)

    return {
        "overall": overall,
        "rentabilite": round(rentabilite),
        "publicite": round(publicite),
        "depenses": round(depenses),
    }


def generate_alerts(summary: dict, product_breakdown: list, expense_breakdown: list) -> list:
    """Alertes textuelles générées à partir de seuils sur les données
    réelles — chaque alerte cite un chiffre calculé, jamais une estimation."""

    alerts = []

    if summary["profit_evolution"] is not None and summary["profit_evolution"] <= -10:
        alerts.append(
            {
                "level": "red",
                "message": f"Votre bénéfice a baissé de {abs(round(summary['profit_evolution']))}% "
                f"par rapport à la période précédente.",
            }
        )

    revenue = summary["revenue"]
    for expense in expense_breakdown:
        if expense["category"].lower() in AD_CATEGORIES and revenue:
            ratio = expense["amount"] / revenue * 100
            if ratio >= 30:
                alerts.append(
                    {
                        "level": "red",
                        "message": f"Votre publicité représente {round(ratio)}% de votre chiffre "
                        f"d'affaires — au-delà de 30%, la marge générée est menacée.",
                    }
                )
            elif ratio >= 20:
                alerts.append(
                    {
                        "level": "orange",
                        "message": f"Le coût publicitaire représente {round(ratio)}% de votre "
                        f"chiffre d'affaires sur cette période.",
                    }
                )

    for product in product_breakdown:
        if product["profit"] < 0:
            alerts.append(
                {
                    "level": "red",
                    "message": f"Le produit « {product['name']} » dépense actuellement plus "
                    f"que la marge qu'il génère.",
                }
            )

    if product_breakdown:
        best = max(product_breakdown, key=lambda p: p["profit"])
        if best["profit"] > 0:
            alerts.append(
                {
                    "level": "green",
                    "message": f"« {best['name']} » est votre produit le plus rentable sur cette période.",
                }
            )

    return alerts


def generate_advisor_insights(summary: dict, product_breakdown: list, expense_breakdown: list) -> list:
    """Paragraphes du conseiller : explique un chiffre, sa cause probable,
    puis une action concrète — jamais juste une statistique brute."""

    insights = []
    revenue = summary["revenue"]
    profit_evo = summary["profit_evolution"]
    revenue_evo = summary["revenue_evolution"]

    if revenue_evo is not None and profit_evo is not None:
        if revenue_evo > 0 and profit_evo < revenue_evo - 5:
            ad_spend = sum(e["amount"] for e in expense_breakdown if e["category"].lower() in AD_CATEGORIES)
            ad_ratio = (ad_spend / revenue * 100) if revenue else 0
            insights.append(
                f"Votre chiffre d'affaires a augmenté de {round(revenue_evo)}%, mais votre bénéfice "
                f"n'a progressé que de {round(profit_evo)}%. "
                + (
                    f"La publicité représente {round(ad_ratio)}% de votre chiffre d'affaires sur "
                    f"cette période — c'est la principale piste à surveiller."
                    if ad_ratio >= 15
                    else "Vérifiez vos autres postes de dépenses, qui ont probablement augmenté plus vite que vos ventes."
                )
            )
        elif profit_evo is not None and profit_evo > 0:
            insights.append(
                f"Votre bénéfice a progressé de {round(profit_evo)}% sur cette période — "
                f"la tendance est positive, continuez sur cette lancée."
            )

    if expense_breakdown:
        top_expense = expense_breakdown[0]
        if revenue:
            ratio = top_expense["amount"] / revenue * 100
            insights.append(
                f"Votre plus grosse dépense est « {top_expense['category']} » "
                f"({round(ratio)}% du chiffre d'affaires) — c'est le premier levier à examiner "
                f"si vous cherchez à améliorer votre marge."
            )

    if product_breakdown:
        losing_products = [p for p in product_breakdown if p["profit"] < 0]
        if losing_products:
            names = ", ".join(f"« {p['name']} »" for p in losing_products[:3])
            insights.append(
                f"{len(losing_products)} produit(s) ne sont pas rentables sur cette période : {names}. "
                f"Envisagez de revoir leur prix, leurs coûts, ou d'arrêter leur commercialisation."
            )

    if not insights:
        insights.append(
            "Pas assez de données sur cette période pour une analyse détaillée. "
            "Importez vos ventes et dépenses pour obtenir des recommandations."
        )

    return insights


def simulate_scenario(
    summary: dict,
    expense_breakdown: list,
    price_change_pct: float = 0,
    volume_change_pct: float = 0,
    ad_spend_change_pct: float = 0,
    other_costs_change_pct: float = 0,
) -> dict:
    """Simulateur « Et si ? ». Modèle volontairement simple et transparent :
    - le chiffre d'affaires varie avec le prix ET le volume (effet multiplicatif)
    - les dépenses publicité varient indépendamment des autres coûts
    Ce n'est pas une prédiction garantie, seulement une estimation linéaire,
    ce qui est indiqué explicitement dans la réponse au frontend."""

    revenue = summary["revenue"]
    ad_spend = sum(e["amount"] for e in expense_breakdown if e["category"].lower() in AD_CATEGORIES)
    other_costs = summary["expenses"] - ad_spend

    new_revenue = revenue * (1 + price_change_pct / 100) * (1 + volume_change_pct / 100)
    new_ad_spend = ad_spend * (1 + ad_spend_change_pct / 100)
    new_other_costs = other_costs * (1 + other_costs_change_pct / 100)
    new_expenses = new_ad_spend + new_other_costs
    new_profit = new_revenue - new_expenses
    new_margin = (new_profit / new_revenue * 100) if new_revenue else 0.0

    return {
        "current": {
            "revenue": round(revenue, 2),
            "expenses": round(summary["expenses"], 2),
            "profit": round(summary["profit"], 2),
            "margin": round(summary["margin"], 1),
        },
        "simulated": {
            "revenue": round(new_revenue, 2),
            "expenses": round(new_expenses, 2),
            "profit": round(new_profit, 2),
            "margin": round(new_margin, 1),
        },
        "profit_change": round(new_profit - summary["profit"], 2),
    }


def get_monthly_trend(user_id, currency: str, months: int = 6) -> list:
    """Chiffre d'affaires / dépenses / bénéfice par mois, pour le graphique
    de tendance. Retourne les `months` derniers mois, du plus ancien au
    plus récent."""

    today = date.today()
    results = []

    for i in range(months - 1, -1, -1):
        month_index = today.month - i
        year = today.year + (month_index - 1) // 12
        month = (month_index - 1) % 12 + 1
        start = date(year, month, 1)
        end_month = month + 1
        end_year = year
        if end_month > 12:
            end_month = 1
            end_year += 1
        end = date(end_year, end_month, 1) - timedelta(days=1)

        rows = (
            _date_range_query(user_id, currency, start, end)
            .with_entities(Transaction.type, func.sum(Transaction.amount))
            .group_by(Transaction.type)
            .all()
        )
        revenue = expenses = Decimal("0")
        for t_type, total in rows:
            if t_type == TransactionType.REVENUE:
                revenue = total or Decimal("0")
            else:
                expenses = total or Decimal("0")

        results.append(
            {
                "month": start.strftime("%Y-%m"),
                "revenue": _to_float(revenue),
                "expenses": _to_float(expenses),
                "profit": _to_float(revenue - expenses),
            }
        )

    return results