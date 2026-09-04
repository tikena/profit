// Récupère les chiffres calculés côté serveur (analytics.py) et les
// affiche. Aucun calcul financier n'est refait ici — le frontend se
// contente de mettre en forme ce que le serveur a déjà calculé et validé.

const CURRENCY_SYMBOLS = { eur: '€', usd: '$', gbp: '£', mad: 'DH', cad: '$' };

function formatMoney(amount, currency) {
  const symbol = CURRENCY_SYMBOLS[currency] || currency.toUpperCase();
  const formatted = Number(amount).toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${formatted}\u00A0${symbol}`;
}

function evolutionBadge(value) {
  if (value === null || value === undefined) return '';
  const sign = value >= 0 ? '+' : '';
  const cls = value >= 0 ? 'evo-up' : 'evo-down';
  return `<span class="evo-badge ${cls}">${sign}${Math.round(value)}%</span>`;
}

let trendChart = null;
let expenseChart = null;

async function loadDashboard(period) {
  const res = await fetch(`/api/dashboard/summary?period=${encodeURIComponent(period)}`);
  if (res.status === 401) {
    window.location.href = '/login';
    return;
  }
  const data = await res.json();
  const { currency, summary, expense_breakdown, product_breakdown, health_score, alerts, insights, monthly_trend } = data;

  const hasData = summary.revenue > 0 || summary.expenses > 0;
  document.getElementById('empty-state').style.display = hasData ? 'none' : 'block';
  document.getElementById('dashboard-content').style.display = hasData ? 'block' : 'none';
  if (!hasData) return;

  renderKpis(summary, currency);
  renderHealthScore(health_score);
  renderAlerts(alerts);
  renderInsights(insights, data.insights_ai_powered);
  renderTopProducts(product_breakdown.slice(0, 5), currency);
  renderTrendChart(monthly_trend, currency);
  renderExpenseChart(expense_breakdown, currency);
}

function renderKpis(summary, currency) {
  const cards = [
    { label: 'Chiffre d\'affaires', value: formatMoney(summary.revenue, currency), evo: summary.revenue_evolution },
    { label: 'Dépenses', value: formatMoney(summary.expenses, currency) },
    { label: 'Bénéfice net', value: formatMoney(summary.profit, currency), evo: summary.profit_evolution },
    { label: 'Marge', value: `${summary.margin}%` },
    { label: 'Commandes', value: summary.order_count },
    { label: 'Panier moyen', value: formatMoney(summary.avg_order, currency) },
  ];

  document.getElementById('kpi-grid').innerHTML = cards.map((c) => `
    <div class="kpi-card">
      <span class="kpi-label">${c.label}</span>
      <span class="kpi-value figures">${c.value}</span>
      ${c.evo !== undefined ? evolutionBadge(c.evo) : ''}
    </div>
  `).join('');
}

function renderHealthScore(score) {
  const rows = [
    { label: 'Rentabilité', value: score.rentabilite },
    { label: 'Publicité', value: score.publicite },
    { label: 'Dépenses', value: score.depenses },
  ];

  document.getElementById('health-score').innerHTML = `
    <div class="health-overall figures">${score.overall}<span style="font-size:16px;color:var(--paper-dim);">/100</span></div>
    ${rows.map((r) => `
      <div class="health-row">
        <span>${r.label}</span>
        <div class="health-bar-track"><div class="health-bar-fill" style="width:${r.value}%"></div></div>
        <span class="figures">${r.value}</span>
      </div>
    `).join('')}
  `;
}

function renderAlerts(alerts) {
  const el = document.getElementById('alerts-list');
  if (!alerts.length) {
    el.innerHTML = '<p style="color:var(--paper-dim);">Aucune alerte sur cette période.</p>';
    return;
  }
  const icons = { red: '🔴', orange: '🟠', green: '🟢' };
  el.innerHTML = alerts.map((a) => `
    <div class="alert-item alert-${a.level}">
      <span>${icons[a.level] || ''}</span>
      <span>${a.message}</span>
    </div>
  `).join('');
}

function renderInsights(insights, aiPowered) {
  const badge = aiPowered
    ? '<span class="ai-badge ai-badge-on">Analyse IA</span>'
    : '<span class="ai-badge">Analyse automatique</span>';
  document.getElementById('advisor-insights').innerHTML = badge + insights.map((text) => `<p>${text}</p>`).join('');
}

function renderTopProducts(products, currency) {
  const body = document.getElementById('top-products-body');
  if (!products.length) {
    body.innerHTML = '<tr><td colspan="4" style="color:var(--paper-dim);">Aucun produit avec des ventes sur cette période.</td></tr>';
    return;
  }
  body.innerHTML = products.map((p) => `
    <tr>
      <td>${p.name}</td>
      <td class="figures">${formatMoney(p.revenue, currency)}</td>
      <td class="figures">${formatMoney(p.profit, currency)}</td>
      <td class="figures">${p.margin}%</td>
    </tr>
  `).join('');
}

function renderTrendChart(monthlyTrend, currency) {
  const ctx = document.getElementById('trend-chart');
  const labels = monthlyTrend.map((m) => m.month);
  const revenueData = monthlyTrend.map((m) => m.revenue);
  const profitData = monthlyTrend.map((m) => m.profit);

  if (trendChart) trendChart.destroy();
  trendChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Chiffre d\'affaires', data: revenueData, borderColor: '#c9a227', backgroundColor: 'transparent', tension: 0.2 },
        { label: 'Bénéfice', data: profitData, borderColor: '#4c9a6a', backgroundColor: 'transparent', tension: 0.2 },
      ],
    },
    options: {
      plugins: { legend: { labels: { color: '#b9b6ad' } } },
      scales: {
        x: { ticks: { color: '#b9b6ad' }, grid: { color: '#1f3350' } },
        y: { ticks: { color: '#b9b6ad' }, grid: { color: '#1f3350' } },
      },
    },
  });
}

function renderExpenseChart(expenseBreakdown) {
  const ctx = document.getElementById('expense-chart');
  const palette = ['#c9a227', '#4c9a6a', '#b2503a', '#6b8fb5', '#8a6bb5', '#b58a6b'];

  if (expenseChart) expenseChart.destroy();

  if (!expenseBreakdown.length) {
    ctx.getContext('2d').clearRect(0, 0, ctx.width, ctx.height);
    return;
  }

  expenseChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: expenseBreakdown.map((e) => e.category),
      datasets: [{ data: expenseBreakdown.map((e) => e.amount), backgroundColor: palette }],
    },
    options: { plugins: { legend: { labels: { color: '#b9b6ad' } } } },
  });
}

const periodSelect = document.getElementById('period-select');
periodSelect.addEventListener('change', () => loadDashboard(periodSelect.value));
loadDashboard(periodSelect.value);