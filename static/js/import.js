const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

document.getElementById('csv-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('csv-error');
  const successEl = document.getElementById('csv-success');
  errorEl.textContent = '';
  successEl.textContent = '';

  const fileInput = document.getElementById('csv-file');
  if (!fileInput.files.length) return;

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);

  try {
    const res = await fetch('/api/dashboard/import', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken },
      body: formData,
    });
    const data = await res.json();

    if (!res.ok) {
      if (res.status === 401) { window.location.href = '/login'; return; }
      const details = data.details ? '\n' + data.details.join('\n') : '';
      errorEl.textContent = (data.error || 'Import impossible.') + details;
      return;
    }

    successEl.textContent = `${data.count} transaction(s) importée(s) avec succès.`;
    fileInput.value = '';
  } catch (err) {
    errorEl.textContent = 'Erreur réseau, réessayez.';
  }
});

document.getElementById('manual-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('manual-error');
  const successEl = document.getElementById('manual-success');
  errorEl.textContent = '';
  successEl.textContent = '';

  const payload = {
    type: document.getElementById('m-type').value,
    category: document.getElementById('m-category').value,
    amount: document.getElementById('m-amount').value,
    date: document.getElementById('m-date').value,
    product: document.getElementById('m-product').value,
    description: document.getElementById('m-description').value,
  };

  try {
    const res = await fetch('/api/dashboard/transactions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (!res.ok) {
      if (res.status === 401) { window.location.href = '/login'; return; }
      errorEl.textContent = data.error || 'Ajout impossible.';
      return;
    }

    successEl.textContent = 'Transaction ajoutée.';
    document.getElementById('manual-form').reset();
  } catch (err) {
    errorEl.textContent = 'Erreur réseau, réessayez.';
  }
});