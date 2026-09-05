// Gère le paywall de la page /pricing : sélection de devise (affichage) et
// lancement de Stripe Checkout dans la devise choisie.
//
// Le navigateur n'envoie jamais de montant : uniquement un plan_id interne
// ("pro_monthly"...) et un code devise ISO 4217 pris dans la liste renvoyée
// par le serveur. Le vrai tarif/devise facturé est résolu et validé côté
// serveur (voir routes.py), à partir des currency_options configurées sur
// le Price Stripe correspondant.

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

const CURRENCY_LABELS = {
  eur: 'EUR — Euro',
  usd: 'USD — Dollar américain',
  gbp: 'GBP — Livre sterling',
  mad: 'MAD — Dirham marocain',
  cad: 'CAD — Dollar canadien',
};

let priceTable = {};
let selectedCurrency = 'eur';

async function loadCurrencies() {
  const select = document.getElementById('currency-select');
  try {
    const res = await fetch('/api/payments/currencies');
    const data = await res.json();
    priceTable = data.prices;
    selectedCurrency = data.default;

    select.innerHTML = '';
    data.supported.forEach((code) => {
      const option = document.createElement('option');
      option.value = code;
      option.textContent = CURRENCY_LABELS[code] || code.toUpperCase();
      if (code === selectedCurrency) option.selected = true;
      select.appendChild(option);
    });

    applyCurrency(selectedCurrency);
  } catch (err) {
    // Si la liste des devises ne charge pas, on reste sur les valeurs
    // par défaut déjà présentes dans le HTML plutôt que de bloquer la page.
    select.innerHTML = '<option value="eur" selected>EUR — Euro</option>';
  }

  select.addEventListener('change', (e) => applyCurrency(e.target.value));
}

function applyCurrency(currency) {
  selectedCurrency = currency;

  document.querySelectorAll('[data-plan]').forEach((el) => {
    const planId = el.dataset.plan;
    const priceInfo = priceTable[planId] && priceTable[planId][currency];
    const amountEl = el.querySelector('[data-price-amount]');
    const suffixEl = el.querySelector('[data-price-suffix]');
    if (priceInfo && amountEl && suffixEl) {
      amountEl.textContent = `${priceInfo.amount}\u00A0${priceInfo.symbol}`;
      suffixEl.textContent = ` ${priceInfo.suffix}`;
    }
  });

  // Le plan Free n'a pas de tarif par devise à chercher : on affiche juste
  // le suffixe "/mois" ou "/mo" cohérent avec la devise choisie.
  const freeSuffix = document.querySelector('[data-currency-suffix]');
  if (freeSuffix) {
    freeSuffix.textContent = currency === 'usd' || currency === 'cad' || currency === 'gbp' ? ' /mo' : ' /mois';
  }
}

// Bascule mensuel/annuel : ne concerne que la carte Pro (Business n'a pas
// encore d'offre annuelle). Change le plan_id affiché et facturé, puis
// redemande l'affichage des prix dans la devise déjà sélectionnée.
document.querySelectorAll('.billing-toggle').forEach((toggle) => {
  const priceBlock = toggle.parentElement.querySelector('[data-plan]');
  const buyButton = toggle.parentElement.querySelector('[data-plan-id]');

  toggle.querySelectorAll('.toggle-option').forEach((option) => {
    option.addEventListener('click', () => {
      toggle.querySelectorAll('.toggle-option').forEach((o) => o.classList.remove('active'));
      option.classList.add('active');

      const billing = option.dataset.billing; // "monthly" ou "yearly"
      const newPlanId = billing === 'yearly' ? priceBlock.dataset.planYearly : priceBlock.dataset.planMonthly;
      priceBlock.dataset.plan = newPlanId;
      buyButton.dataset.planId = newPlanId;

      applyCurrency(selectedCurrency);
    });
  });
});

document.querySelectorAll('[data-plan-id]').forEach((button) => {
  button.addEventListener('click', async () => {
    const errorEl = document.getElementById('pricing-error');
    errorEl.textContent = '';
    button.disabled = true;
    const originalLabel = button.textContent;
    button.textContent = 'Redirection…';

    try {
      const res = await fetch('/api/payments/create-checkout-session', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({
          plan_id: button.dataset.planId,
          currency: selectedCurrency,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        if (res.status === 401) {
          window.location.href = '/login';
          return;
        }
        errorEl.textContent = data.error || "Impossible de lancer le paiement pour le moment.";
        return;
      }

      window.location.href = data.checkout_url;
    } catch (err) {
      errorEl.textContent = 'Erreur réseau, réessayez.';
    } finally {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  });
});

loadCurrencies();