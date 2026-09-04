"""
Conseiller IA réel.

Envoie les chiffres déjà calculés (jamais l'email ni aucune autre donnée
personnelle de l'utilisateur — uniquement des totaux financiers agrégés)
à l'API Claude (Anthropic) pour obtenir une analyse en langage naturel.

Sécurité et fiabilité :
- Si ANTHROPIC_API_KEY n'est pas configurée, ou si l'appel échoue pour
  quelque raison que ce soit (réseau, quota, timeout), la fonction retourne
  None et l'appelant retombe sur analytics.generate_advisor_insights
  (conseiller basé sur des règles) — le tableau de bord n'est jamais cassé
  par une panne du service IA.
- Le prompt interdit explicitement d'inventer des chiffres absents des
  données fournies, pour éviter les hallucinations sur des sujets financiers.
"""

import logging
import os

logger = logging.getLogger("profit.ai_advisor")

# Modèle utilisé pour l'analyse : bon rapport qualité/coût pour ce type de
# tâche (résumé de chiffres + recommandation courte).
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 400


def _build_prompt(summary: dict, product_breakdown: list, expense_breakdown: list) -> str:
    products_text = "\n".join(
        f"- {p['name']} : CA={p['revenue']}, coûts={p['expenses']}, bénéfice={p['profit']}, "
        f"marge={p['margin']}%, ventes={p['sales_count']}"
        for p in product_breakdown[:10]
    ) or "(aucun produit avec des ventes sur cette période)"

    expenses_text = "\n".join(
        f"- {e['category']} : {e['amount']}" for e in expense_breakdown[:10]
    ) or "(aucune dépense enregistrée sur cette période)"

    revenue_evo = summary["revenue_evolution"]
    profit_evo = summary["profit_evolution"]

    return f"""Tu es un conseiller financier pour une petite entreprise (e-commerce, freelance ou petit commerce). Voici ses chiffres réels sur la période du {summary['period_start']} au {summary['period_end']} :

Chiffre d'affaires : {summary['revenue']}
Dépenses totales : {summary['expenses']}
Bénéfice net : {summary['profit']}
Marge : {summary['margin']}%
Nombre de commandes : {summary['order_count']}
Panier moyen : {summary['avg_order']}
Évolution du chiffre d'affaires vs période précédente : {f"{revenue_evo}%" if revenue_evo is not None else "non disponible (pas de données sur la période précédente)"}
Évolution du bénéfice vs période précédente : {f"{profit_evo}%" if profit_evo is not None else "non disponible"}

Détail par produit :
{products_text}

Détail des dépenses par catégorie :
{expenses_text}

Rédige entre 2 et 4 phrases d'analyse concrète, en français. Identifie le problème ou la tendance la plus importante dans CES chiffres précis, explique sa cause la plus probable, puis propose une action concrète et réalisable. N'invente aucun chiffre absent des données ci-dessus — si une donnée manque pour conclure, dis-le simplement plutôt que de l'estimer. Réponds uniquement avec le texte de l'analyse, sans introduction, sans liste à puces, sans formule de politesse."""


def generate_ai_advisor_text(summary: dict, product_breakdown: list, expense_breakdown: list):
    """Retourne une liste de paragraphes (str), ou None si l'IA n'est pas
    disponible — l'appelant doit alors utiliser le conseiller basé sur
    des règles comme repli."""

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        logger.warning("Le paquet 'anthropic' n'est pas installé (voir requirements.txt) — conseiller IA désactivé.")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": _build_prompt(summary, product_breakdown, expense_breakdown)}],
        )
        text = "".join(block.text for block in message.content if hasattr(block, "text")).strip()
        if not text:
            return None
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        return paragraphs or None
    except Exception as exc:  # volontairement large : toute panne IA ne doit jamais casser le tableau de bord
        logger.warning("Appel à l'API Anthropic échoué, repli sur le conseiller basé sur des règles : %s", exc)
        return None