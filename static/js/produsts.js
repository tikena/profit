const CURRENCY_SYMBOLS = { eur: '€', usd: '$', gbp: '£', mad: 'DH', cad: '$' };

function formatMoney(amount, currency) {
  const symbol = CURRENCY_SYMBOLS[currency] || currency.toUpperCase();
  const formatted = Number(amount).toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${formatted}\u00A0${symbol}`;
}

async function loadProducts(period) {
  const res = await fetch(`/api/dashboard/summary?period=${encodeURIComponent(period)}`);
  if (res.status === 401) {
    window.location.href = '/login';
    return;
  }
  const data = await res.json();
  const { currency, product_breakdown } = data;

  const hasProducts = product_breakdown.length > 0;
  document.getElementById('empty-state').style.display = hasProducts ? 'none' : 'block';
  document.getElementById('products-panel').style.display = hasProducts ? 'block' : 'none';
  if (!hasProducts) return;

  document.getElementById('products-body').innerHTML = product_breakdown.map((p) => `
    <tr>
      <td>${p.name}</td>
      <td class="figures">${p.sales_count}</td>
      <td class="figures">${formatMoney(p.revenue, currency)}</td>
      <td class="figures">${formatMoney(p.expenses, currency)}</td>
      <td class="figures" style="color:${p.profit >= 0 ? 'var(--ledger-green)' : 'var(--ledger-red)'}">${formatMoney(p.profit, currency)}</td>
      <td class="figures">${p.margin}%</td>
    </tr>
  `).join('');
}

const periodSelect = document.getElementById('period-select');
periodSelect.addEventListener('change', () => loadProducts(periodSelect.value));
loadProducts(periodSelect.value);