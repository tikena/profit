const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const CURRENCY_SYMBOLS = { eur: '€', usd: '$', gbp: '£', mad: 'DH', cad: '$' };

function formatMoney(amount, currency) {
  const symbol = CURRENCY_SYMBOLS[currency] || currency.toUpperCase();
  const formatted = Number(amount).toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${formatted}\u00A0${symbol}`;
}

const sliders = {
  price: document.getElementById('s-price'),
  volume: document.getElementById('s-volume'),
  ad: document.getElementById('s-ad'),
  costs: document.getElementById('s-costs'),
};

function updateSliderLabels() {
  document.getElementById('s-price-value').textContent = `${sliders.price.value}%`;
  document.getElementById('s-volume-value').textContent = `${sliders.volume.value}%`;
  document.getElementById('s-ad-value').textContent = `${sliders.ad.value}%`;
  document.getElementById('s-costs-value').textContent = `${sliders.costs.value}%`;
}

function renderBlock(elId, values, currency) {
  document.getElementById(elId).innerHTML = `
    <div class="health-row"><span>Chiffre d'affaires</span><span class="figures">${formatMoney(values.revenue, currency)}</span></div>
    <div class="health-row"><span>Dépenses</span><span class="figures">${formatMoney(values.expenses, currency)}</span></div>
    <div class="health-row"><span>Bénéfice</span><span class="figures">${formatMoney(values.profit, currency)}</span></div>
    <div class="health-row"><span>Marge</span><span class="figures">${values.margin}%</span></div>
  `;
}

let debounceTimer = null;

async function runSimulation() {
  const payload = {
    price_change_pct: Number(sliders.price.value),
    volume_change_pct: Number(sliders.volume.value),
    ad_spend_change_pct: Number(sliders.ad.value),
    other_costs_change_pct: Number(sliders.costs.value),
  };

  const res = await fetch('/api/dashboard/simulate?period=30d', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify(payload),
  });

  if (res.status === 401) {
    window.location.href = '/login';
    return;
  }

  const data = await res.json();
  renderBlock('sim-current', data.current, data.currency);
  renderBlock('sim-simulated', data.simulated, data.currency);

  const impactEl = document.getElementById('sim-impact');
  const sign = data.profit_change >= 0 ? '+' : '';
  impactEl.style.color = data.profit_change >= 0 ? 'var(--ledger-green)' : 'var(--ledger-red)';
  impactEl.textContent = `${sign}${formatMoney(data.profit_change, data.currency)} de bénéfice estimé`;
}

Object.values(sliders).forEach((slider) => {
  slider.addEventListener('input', () => {
    updateSliderLabels();
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(runSimulation, 200);
  });
});

updateSliderLabels();
runSimulation();