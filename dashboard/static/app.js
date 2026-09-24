(() => {
  'use strict';

  const API = (import.meta.env?.VITE_API_URL || window.UPI_API_URL || window.location.origin).replace(/\/$/, '');
  const state = { alerts: [], transactions: [], stats: null, riskDistribution: null, metrics: null, health: null, charts: {}, transactionPage: 0, transactionPageSize: 10, lastLatency: null };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const number = value => new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Number(value || 0));
  const money = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(Number(value || 0));
  const percent = value => Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(2)}%` : '--';
  const score = value => Number.isFinite(Number(value)) ? Number(value).toFixed(4) : '--';

  async function request(path, options = {}) {
    const started = performance.now();
    let response;
    try {
      response = await fetch(`${API}${path}`, { ...options, headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    } catch (error) {
      throw new Error('Cannot reach the local FastAPI service. Start it with the command in dashboard/README.md.');
    }
    state.lastLatency = Math.round(performance.now() - started);
    const contentType = response.headers.get('content-type') || '';
    const payload = contentType.includes('application/json') ? await response.json() : {};
    if (!response.ok) {
      const detail = Array.isArray(payload.detail) ? payload.detail.map(item => item.msg).join(', ') : payload.detail;
      const message = response.status === 422 ? `Check the transaction fields: ${detail || 'invalid input'}`
        : response.status === 400 ? (detail || 'The request was not valid.')
        : response.status === 500 ? 'The backend encountered an error while scoring this transaction.'
        : response.status === 503 ? (detail || 'The model is unavailable. Check model artifacts.')
        : (detail || `Request failed (HTTP ${response.status}).`);
      throw new Error(message);
    }
    return payload;
  }

  function toast(message, type = 'success') {
    const region = $('#toast-region');
    const item = document.createElement('div');
    item.className = `toast toast-${type}`;
    item.textContent = message;
    region.append(item);
    window.setTimeout(() => item.remove(), 4500);
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }

  function riskClass(tier) { return `risk-${String(tier || 'none').toLowerCase()}`; }
  function riskBadge(tier) { return `<span class="risk-chip ${riskClass(tier)}">${escapeHtml(tier || 'Unknown')}</span>`; }

  function setCell(id, value) { const element = document.getElementById(id); if (element) element.textContent = value; }

  const INDICATORS = [
    ['amount', 'Transaction amount', 'Transfer value in INR.', 'A large transaction may deserve review when it differs from the sender’s usual pattern.'],
    ['amount_vs_avg_ratio', 'Amount vs sender average', 'Transaction amount divided by sender average over 30 days.', 'A high ratio can show unusual spending behavior.'],
    ['sender_txn_count_24h', 'Transaction velocity', 'Sender transaction count during the last 24 hours.', 'An unusually high frequency can indicate rapid or automated transfers.'],
    ['is_new_receiver', 'New receiver', 'Whether the receiver is new to this sender.', 'First-time recipients can merit extra scrutiny alongside other signals.'],
    ['is_odd_hour', 'Odd hour', 'Transactions between 00:00 and 04:59 are marked as odd-hour activity.', 'Unusual timing can add context to a transaction review.'],
    ['is_location_mismatch', 'Location mismatch', 'Whether transaction location differs from sender home city when both are known.', 'A location difference may be expected during travel; missing city data is treated as unknown.'],
    ['log_amount', 'Log amount', 'Logarithm of transaction amount used to reduce the effect of very large values.', 'It helps the model compare amount scale across transactions.'],
  ];

  function renderIndicators() {
    $('#indicator-grid').innerHTML = INDICATORS.map(([key, title, meaning, signal]) => `<article class="panel indicator-card"><span>${escapeHtml(key)}</span><h3>${escapeHtml(title)}</h3><p><b>What it means</b> ${escapeHtml(meaning)}</p><p><b>Why review it</b> ${escapeHtml(signal)}</p></article>`).join('');
  }

  function renderStats(data, distribution) {
    state.stats = data;
    state.riskDistribution = distribution;
    const history = data.history || {};
    setCell('kpi-total', number(distribution.total));
    setCell('kpi-flagged', number(distribution.flagged));
    setCell('kpi-low', number(distribution.low));
    setCell('kpi-medium', number(distribution.medium));
    setCell('kpi-high', number(distribution.high));
    setCell('kpi-critical', number(distribution.critical));
    setCell('chart-source', history.available ? 'Ground truth + live scored session' : 'Live scored session only');
    renderCharts(data, distribution);
    renderRiskScale(distribution.thresholds);
    renderTransactions(state.transactions);
  }

  function renderLatest(item) {
    if (!item) return;
    $('#risk-empty').classList.add('hidden');
    $('#risk-error').classList.add('hidden');
    $('#risk-result').classList.remove('hidden');
    const tier = $('#latest-tier');
    tier.className = `risk-chip ${riskClass(item.risk_tier)}`;
    tier.textContent = item.risk_tier || 'Unknown';
    setCell('latest-score', score(item.ensemble_score));
    setCell('latest-id', item.transaction_id);
    setCell('latest-time', new Date(item.timestamp_scored || item.timestamp).toLocaleString());
    setCell('latest-threshold', score(item.threshold_used));
    setCell('latest-fraud', item.flagged ? 'Flagged' : 'Clear');
    setCell('latest-status', item.flagged ? 'FRAUD RISK FLAGGED' : 'NO FRAUD FLAG');
    const meter = $('#latest-score-meter');
    if (meter) meter.style.width = `${Math.max(0, Math.min(100, Number(item.ensemble_score) * 100))}%`;
    for (const [key, value] of [['iso', item.isolation_forest_score], ['xgb', item.xgboost_score], ['ensemble', item.ensemble_score]]) {
      setCell(`latest-${key}`, score(value));
      const bar = $(`#latest-${key}-bar`);
      if (bar) bar.style.width = `${Math.max(0, Math.min(100, Number(value) * 100))}%`;
    }
  }

  function showRiskError(message) {
    $('#risk-empty').classList.add('hidden');
    $('#risk-result').classList.add('hidden');
    const panel = $('#risk-error');
    panel.textContent = message;
    panel.classList.remove('hidden');
  }

  function transactionReasons(item) {
    const f = item.feature_summary || {};
    const reasons = [];
    if (Number(f.amount_vs_avg_ratio) >= 3) reasons.push(`Amount is ${Number(f.amount_vs_avg_ratio).toFixed(1)}× the sender's 30-day average.`);
    if (f.is_new_receiver) reasons.push('A new receiver was detected.');
    if (Number(f.sender_txn_count_24h) >= 10) reasons.push('Transaction velocity is high for the 24-hour window.');
    if (f.is_odd_hour) reasons.push('The transaction occurred between midnight and 05:00.');
    if (f.is_location_mismatch) reasons.push('Transaction location differs from the sender home city.');
    if (item.flagged && !reasons.length) reasons.push('Combined model scores crossed the configured alert threshold; review the transaction context.');
    if (!reasons.length) reasons.push('No configured review indicator was identified in the supplied features.');
    return reasons;
  }

  function showTransactionDetails(item) {
    if (!item) return;
    const f = item.feature_summary || {};
    const rows = [
      ['Transaction ID', item.transaction_id], ['Amount', money(item.amount)], ['Timestamp', new Date(item.timestamp).toLocaleString()],
      ['Sender average (30 days)', money(item.sender_avg_amount_30d)], ['Sender transactions (24h)', number(item.sender_txn_count_24h)],
      ['New receiver', item.is_new_receiver ? 'Yes' : 'No'], ['Location', item.location || 'Unknown'], ['Sender home city', item.sender_home_city || 'Unknown'],
      ['Location mismatch', f.is_location_mismatch ? 'Yes' : 'No / unknown'], ['Amount vs average', `${score(f.amount_vs_avg_ratio)}×`],
      ['Odd hour', f.is_odd_hour ? `Yes (${String(f.hour).padStart(2, '0')}:00)` : 'No'],
      ['Isolation Forest score', score(item.isolation_forest_score)], ['XGBoost score', score(item.xgboost_score)],
      ['Ensemble score', score(item.ensemble_score)], ['Risk tier', item.risk_tier], ['Alert threshold', score(item.threshold_used)], ['Flagged', item.flagged ? 'Yes' : 'No'],
    ];
    const reasons = transactionReasons(item);
    $('#transaction-detail-content').innerHTML = `<div class="eyebrow">TRANSACTION REVIEW</div><h2 id="dialog-title">${escapeHtml(item.transaction_id)}</h2><p class="detail-summary">${riskBadge(item.risk_tier)} <strong>${score(item.ensemble_score)}</strong> ensemble risk score</p><h3>Transaction details</h3><dl class="detail-grid">${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl><h3>Why was this transaction flagged?</h3><ul class="reason-list">${reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join('')}</ul><p class="detail-disclaimer">These deterministic feature explanations provide review context. A single signal does not prove fraud.</p>`;
    $('#transaction-dialog').showModal();
  }

  function showScoreToast(item) {
    const region = $('#toast-region');
    const node = document.createElement('article');
    const tier = String(item.risk_tier || 'Low').toLowerCase();
    node.className = `score-toast toast-tier-${tier}`;
    const title = item.flagged ? (tier === 'critical' ? 'CRITICAL FRAUD RISK' : `${String(item.risk_tier).toUpperCase()} FRAUD RISK ALERT`) : `${String(item.risk_tier).toUpperCase()} RISK · NO ALERT`;
    node.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(item.transaction_id)} · score ${score(item.ensemble_score)}</span><div class="toast-actions"><button type="button" data-toast-view>View Details</button><button type="button" data-toast-dismiss>Dismiss</button></div>`;
    node.querySelector('[data-toast-view]').addEventListener('click', () => { node.remove(); showTransactionDetails(item); });
    node.querySelector('[data-toast-dismiss]').addEventListener('click', () => node.remove());
    region.append(node);
    window.setTimeout(() => node.remove(), 9000);
  }

  function renderAlerts(items) {
    state.alerts = items || [];
    const filter = $('#alert-filter').value;
    let shown = state.alerts;
    if (filter === 'flagged') shown = shown.filter(item => item.flagged);
    else if (filter !== 'all') shown = shown.filter(item => item.risk_tier === filter);
    setCell('nav-alert-count', number(state.alerts.length));
    const body = $('#alerts-body');
    if (!shown.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty-cell">No matching alerts in this session. Run a demo transaction to create a real model score.</td></tr>';
      setCell('alerts-foot', 'Alerts contain actual predictions recorded by this API session.');
      return;
    }
    body.innerHTML = shown.slice(0, 100).map(item => `<tr>
      <td class="mono">${escapeHtml(item.transaction_id)}</td><td class="amount">${money(item.amount)}</td>
      <td><strong>${score(item.ensemble_score)}</strong></td><td>${riskBadge(item.risk_tier)}</td>
      <td><span class="status-pill status-flagged">${item.flagged ? 'Flagged' : 'Clear'}</span></td>
      <td>${escapeHtml(new Date(item.timestamp_scored || item.timestamp).toLocaleString())}</td>
      <td><button class="text-button" data-view-id="${escapeHtml(item.transaction_id)}">Inspect</button></td></tr>`).join('');
    setCell('alerts-foot', `Showing ${Math.min(shown.length, 100)} of ${shown.length} matching flagged transactions · session memory`);
  }

  function renderTransactions(items) {
    state.transactions = items || [];
    const search = $('#transaction-search').value.trim().toLowerCase();
    const risk = $('#transaction-risk-filter').value;
    const status = $('#transaction-status-filter').value;
    const shown = state.transactions.filter(item => {
      const matchesSearch = !search || `${item.transaction_id} ${item.location || ''}`.toLowerCase().includes(search);
      const matchesRisk = risk === 'all' || item.risk_tier === risk;
      const matchesStatus = status === 'all' || (status === 'flagged' ? item.flagged : !item.flagged);
      return matchesSearch && matchesRisk && matchesStatus;
    });
    const pages = Math.max(1, Math.ceil(shown.length / state.transactionPageSize));
    state.transactionPage = Math.min(state.transactionPage, pages - 1);
    const start = state.transactionPage * state.transactionPageSize;
    const pageItems = shown.slice(start, start + state.transactionPageSize);
    const body = $('#transactions-body');
    if (!pageItems.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty-cell">No scored transactions match these filters. Scan or send a demo transaction first.</td></tr>';
      $('#transactions-foot').innerHTML = '<span>Session history is kept in memory and clears when the API restarts.</span>';
      return;
    }
    body.innerHTML = pageItems.map(item => `<tr>
      <td class="mono">${escapeHtml(item.transaction_id)}</td><td class="amount">${money(item.amount)}</td>
      <td>${escapeHtml(new Date(item.timestamp).toLocaleString())}</td>
      <td><div class="risk-score-cell"><strong>${score(item.ensemble_score)}</strong>${riskScoreTrack(item.ensemble_score)}</div></td><td>${riskBadge(item.risk_tier)}</td>
      <td><span class="status-pill ${item.flagged ? 'status-flagged' : 'status-clear'}">${item.flagged ? 'Yes' : 'No'}</span></td><td><button class="text-button" data-detail-id="${escapeHtml(item.transaction_id)}">View</button></td></tr>`).join('');
    $('#transactions-foot').innerHTML = `<span>${start + 1}-${Math.min(start + pageItems.length, shown.length)} of ${shown.length} session transactions</span><div class="pagination-actions"><button class="button button-small" data-page="prev" ${state.transactionPage === 0 ? 'disabled' : ''}>Previous</button><span>Page ${state.transactionPage + 1} / ${pages}</span><button class="button button-small" data-page="next" ${state.transactionPage >= pages - 1 ? 'disabled' : ''}>Next</button></div>`;
  }

  function riskScoreTrack(value) {
    const thresholds = state.riskDistribution?.thresholds;
    if (!thresholds) return '';
    const a = Number(thresholds.low_medium) * 100;
    const b = Number(thresholds.medium_high) * 100;
    const c = Number(thresholds.high_critical) * 100;
    const fill = Math.max(0, Math.min(100, Number(value) * 100));
    return `<div class="risk-score-track" role="img" aria-label="Ensemble score ${score(value)} on a 0 to 1 risk scale" style="background:linear-gradient(90deg,#56d3a7 0%,#56d3a7 ${a}%,#e8bd59 ${a}%,#e8bd59 ${b}%,#ed8c55 ${b}%,#ed8c55 ${c}%,#ed596c ${c}%,#ed596c 100%)"><i style="left:${fill}%"></i></div>`;
  }

  function renderRiskScale(thresholds) {
    const legend = $('#score-range-legend');
    if (!thresholds) { legend.textContent = 'Validation-derived ranges unavailable'; return; }
    const a = Number(thresholds.low_medium), b = Number(thresholds.medium_high), c = Number(thresholds.high_critical);
    legend.innerHTML = `<span class="legend-low">LOW <b>0-${a.toFixed(3)}</b></span><span class="legend-medium">MEDIUM <b>${a.toFixed(3)}-${b.toFixed(3)}</b></span><span class="legend-high">HIGH <b>${b.toFixed(3)}-${c.toFixed(3)}</b></span><span class="legend-critical">CRITICAL <b>${c.toFixed(3)}-1</b></span>`;
  }

  function renderMetrics(data) {
    state.metrics = data;
    const report = data.classification_report || {};
    const fraud = report['1'] || {};
    setCell('metric-accuracy', percent(report.accuracy));
    setCell('metric-precision', percent(fraud.precision));
    setCell('metric-recall', percent(fraud.recall));
    setCell('metric-f1', percent(fraud['f1-score']));
    setCell('metric-threshold', score(data.alert_threshold));
    setCell('metric-iso-weight', percent(data.ensemble_weights?.isolation_forest));
    setCell('metric-xgb-weight', percent(data.ensemble_weights?.xgboost));
    setCell('metric-auc', score(data.ensemble_auc));
    setCell('metric-ap', score(data.xgboost_avg_precision));
    const weights = data.ensemble_weights || {};
    setCell('explanation-iso-weight', percent(weights.isolation_forest));
    setCell('explanation-xgb-weight', percent(weights.xgboost));
    setCell('explanation-formula', `Ensemble score = ${Number(weights.isolation_forest || 0).toFixed(2)} × Isolation Forest score + ${Number(weights.xgboost || 0).toFixed(2)} × XGBoost score.`);
    const bounds = data.risk_tier_thresholds || {};
    setCell('explanation-thresholds', '');
    const thresholdList = $('#explanation-thresholds');
    if (Object.keys(bounds).length) {
      thresholdList.innerHTML = `<div><span>LOW</span><b>&lt; ${score(bounds.low_medium)}</b></div><div><span>MEDIUM</span><b>${score(bounds.low_medium)} – ${score(bounds.medium_high)}</b></div><div><span>HIGH</span><b>${score(bounds.medium_high)} – ${score(bounds.high_critical)}</b></div><div><span>CRITICAL</span><b>≥ ${score(bounds.high_critical)}</b></div><div><span>FLAGGED</span><b>≥ ${score(data.alert_threshold)}</b></div>`;
    } else thresholdList.textContent = 'Risk tier configuration unavailable.';
    setCell('explanation-method', data.score_calibration?.risk_tier_method || 'Risk tiers use the configured backend cutoffs.');
    const matrix = data.confusion_matrix || [];
    const ids = [['cm-tn', matrix[0]?.[0]], ['cm-fp', matrix[0]?.[1]], ['cm-fn', matrix[1]?.[0]], ['cm-tp', matrix[1]?.[1]]];
    ids.forEach(([id, value]) => setCell(id, value == null ? '--' : number(value)));
    setCell('metrics-source', 'Model metrics + validation calibration');
    const method = data.score_calibration?.risk_tier_method || 'Validation score thresholds are unavailable.';
    setCell('metric-calibration-method', method);
  }

  function setIndicator(id, text, ok) {
    setCell(id.replace('-dot', ''), text);
    const dot = document.getElementById(id);
    if (dot) dot.className = `health-indicator ${ok ? 'indicator-good' : 'indicator-bad'}`;
  }

  function renderHealth(data) {
    state.health = data;
    const modelOk = data.model_status === 'loaded';
    setIndicator('health-api-dot', data.api_status === 'online' ? 'System Online' : 'API Offline', data.api_status === 'online');
    setIndicator('health-model-dot', modelOk ? 'Loaded' : 'Unavailable', modelOk);
    setIndicator('health-fastapi-dot', data.api_status === 'online' ? 'Running' : 'Unavailable', data.api_status === 'online');
    setIndicator('health-artifacts-dot', data.model_artifacts_available ? 'Available' : 'Missing artifacts', data.model_artifacts_available);
    setCell('health-latency', state.lastLatency == null ? '--' : `${state.lastLatency} ms`);
    const overall = $('#health-overall');
    overall.className = `health-overall ${modelOk ? 'overall-good' : 'overall-bad'}`;
    overall.innerHTML = `<i></i><span>${modelOk ? 'SYSTEM ONLINE' : 'MODEL UNAVAILABLE'}</span>`;
    setCell('sidebar-status', modelOk ? 'System Online' : 'Model unavailable');
    $('#sidebar-status-dot').className = `connection-dot ${modelOk ? 'indicator-good' : 'indicator-bad'}`;
    setCell('health-note', data.model_error ? `Model loading error: ${data.model_error}` : `API endpoint: ${API}`);
    setCell('health-url', API);
  }

  function chart(id, emptyId, type, data, options = {}) {
    const canvas = document.getElementById(id);
    const empty = document.getElementById(emptyId);
    const hasData = Boolean(data?.labels?.length || data?.datasets?.some(dataset => dataset.data?.length));
    if (!window.Chart) {
      canvas.classList.add('hidden'); empty.classList.remove('hidden'); empty.textContent = 'Chart library unavailable; all data remains available in the tables.'; return;
    }
    if (!hasData) {
      canvas.classList.add('hidden'); empty.classList.remove('hidden'); return;
    }
    canvas.classList.remove('hidden'); empty.classList.add('hidden');
    if (state.charts[id]) state.charts[id].destroy();
    state.charts[id] = new window.Chart(canvas, { type, data, options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 350 },
      plugins: { legend: { labels: { color: '#aab8ca', usePointStyle: true, boxWidth: 8, font: { family: 'Arial', size: 10 } } } },
      scales: type === 'doughnut' ? {} : {
        x: { ticks: { color: '#8190a4', font: { size: 9 } }, grid: { color: '#1a2738' } },
        y: { beginAtZero: true, ticks: { color: '#8190a4', font: { size: 9 } }, grid: { color: '#1a2738' } },
      }, ...options,
    }});
  }

  function renderCharts(stats, distribution) {
    const history = stats.history || {};
    const session = stats.session || {};
    chart('fraud-chart', 'fraud-chart-empty', 'doughnut', history.available ? {
      labels: ['Legitimate', 'Fraud'], datasets: [{ data: [history.legitimate_transactions, history.fraudulent_transactions], backgroundColor: ['#55d6ae', '#f06472'], borderColor: '#111a26', borderWidth: 4 }],
    } : null, { cutout: '70%' });
    const tierLabels = ['Low', 'Medium', 'High', 'Critical'];
    const tierKeys = ['low', 'medium', 'high', 'critical'];
    const tierValues = tierKeys.map(key => Number(distribution[key] || 0));
    const totalScored = Number(distribution.total || 0);
    $('#risk-chart-caption').innerHTML = totalScored ? tierLabels.map((label, index) => `<span><i class="legend-${label.toLowerCase()}"></i>${label} <b>${number(tierValues[index])}</b> <small>${percent(tierValues[index] / totalScored)}</small></span>`).join('') : '';
    chart('risk-chart', 'risk-chart-empty', 'bar', totalScored ? {
      labels: tierLabels,
      datasets: [{ label: 'Transactions', data: tierValues, backgroundColor: ['#55d6ae', '#e8bd59', '#ed8c55', '#ed596c'], borderRadius: 5, maxBarThickness: 54 }],
    } : null, {
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: context => `${number(context.raw)} transactions (${percent(context.raw / totalScored)})` } } },
      scales: {
        x: { title: { display: true, text: 'Risk Level', color: '#aab8ca', font: { size: 10 } }, ticks: { color: '#8190a4' }, grid: { display: false } },
        y: { beginAtZero: true, title: { display: true, text: 'Number of Transactions', color: '#aab8ca', font: { size: 10 } }, ticks: { color: '#8190a4', precision: 0 }, grid: { color: '#1a2738' } },
      },
    });
    const trend = history.fraud_trend || [];
    chart('trend-chart', 'trend-chart-empty', 'line', trend.length ? {
      labels: trend.map(item => item.month), datasets: [
        { label: 'Fraud', data: trend.map(item => item.fraud), borderColor: '#f06472', backgroundColor: '#f0647225', fill: true, tension: 0.32 },
        { label: 'Legitimate', data: trend.map(item => item.legitimate), borderColor: '#55d6ae', backgroundColor: '#55d6ae20', fill: true, tension: 0.32 },
      ],
    } : null);
    const points = session.amount_risk || [];
    chart('amount-chart', 'amount-chart-empty', 'scatter', points.length ? {
      datasets: [{ label: 'Scored transactions', data: points.map(item => ({ x: item.amount, y: item.risk_score })), backgroundColor: '#7e8fffaa', pointRadius: 5 }],
    } : null, { scales: { x: { title: { display: true, text: 'Amount (INR)', color: '#8190a4' }, ticks: { color: '#8190a4' }, grid: { color: '#1a2738' } }, y: { min: 0, max: 1, title: { display: true, text: 'Ensemble score', color: '#8190a4' }, ticks: { color: '#8190a4' }, grid: { color: '#1a2738' } } } });
    const comparisons = session.model_scores || [];
    chart('model-chart', 'model-chart-empty', 'bar', comparisons.length ? {
      labels: comparisons.slice(0, 12).map(item => item.transaction_id.length > 14 ? `${item.transaction_id.slice(0, 12)}..` : item.transaction_id),
      datasets: [
        { label: 'Isolation Forest', data: comparisons.slice(0, 12).map(item => item.isolation_forest_score), backgroundColor: '#6ed2be' },
        { label: 'XGBoost', data: comparisons.slice(0, 12).map(item => item.xgboost_score), backgroundColor: '#a78bfa' },
        { label: 'Ensemble', data: comparisons.slice(0, 12).map(item => item.ensemble_score), backgroundColor: '#f1b65c' },
      ],
    } : null, { scales: { x: { ticks: { color: '#8190a4', font: { size: 8 } }, grid: { display: false } }, y: { min: 0, max: 1, ticks: { color: '#8190a4' }, grid: { color: '#1a2738' } } } });
  }

  async function refreshTables() {
    const [alertsData, transactionsData] = await Promise.all([request('/alerts?limit=500'), request('/transactions?limit=1000')]);
    renderAlerts(alertsData.items);
    renderTransactions(transactionsData.items);
    if (!$('#risk-result').classList.contains('hidden') || !$('#risk-empty').classList.contains('hidden')) return;
    if (transactionsData.items.length) renderLatest(transactionsData.items[0]);
  }

  async function refreshStats() {
    const [stats, distribution, summary] = await Promise.all([request('/stats'), request('/risk-distribution'), request('/statistics')]);
    setCell('kpi-total', number(summary.total_transactions));
    renderStats(stats, distribution);
  }

  async function refreshHealth() {
    try { renderHealth(await request('/health')); }
    catch (error) {
      renderHealth({ api_status: 'offline', model_status: 'unavailable', model_artifacts_available: false, model_error: error.message });
      $('#health-overall').className = 'health-overall overall-bad';
      $('#health-overall').innerHTML = '<i></i><span>API OFFLINE</span>';
    }
  }

  async function refreshMetrics() {
    try { renderMetrics(await request('/metrics')); $('#metrics-error').classList.add('hidden'); }
    catch (error) {
      setCell('metrics-source', 'Metrics unavailable');
      const panel = $('#metrics-error'); panel.textContent = error.message; panel.classList.remove('hidden');
    }
  }

  function localDateTime(date = new Date()) {
    const pad = value => String(value).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  async function analyze(payload) {
    const button = $('#analyze-button');
    const errorPanel = $('#form-error');
    errorPanel.classList.add('hidden');
    button.disabled = true;
    button.textContent = 'Analyzing with local ML model...';
    try {
      const result = await request('/score', { method: 'POST', body: JSON.stringify(payload) });
      renderLatest(result);
      setCell('health-latency', `${state.lastLatency} ms`);
      showScoreToast(result);
      await Promise.allSettled([refreshTables(), refreshStats(), refreshHealth()]);
      return result;
    } catch (error) {
      errorPanel.textContent = error.message;
      errorPanel.classList.remove('hidden');
      showRiskError(error.message);
      toast(error.message, 'error');
      await refreshHealth();
      return null;
    } finally {
      button.disabled = false;
      button.textContent = 'Analyze Transaction';
    }
  }

  function formPayload() {
    const rawTimestamp = $('#timestamp').value;
    // Preserve the entered wall-clock hour; training features use the naive timestamp hour.
    const timestamp = rawTimestamp || '';
    return {
      transaction_id: $('#transaction-id').value.trim(),
      amount: Number($('#amount').value),
      timestamp,
      sender_avg_amount_30d: Number($('#sender-average').value),
      sender_txn_count_24h: Number($('#sender-count').value),
      is_new_receiver: $('#new-receiver').checked,
      location: $('#location').value.trim() || null,
      sender_home_city: $('#sender-home-city').value.trim() || null,
    };
  }

  function fillDemo(kind) {
    const now = new Date();
    const demos = {
      legitimate: { id: 'demo-legit', amount: 450, average: 500, count: 2, isNew: false, hour: 14, location: 'BHOPAL' },
      'odd-hour': { id: 'demo-odd-hour', amount: 18000, average: 500, count: 1, isNew: true, fixed: '2025-06-15T02:47', location: 'BHOPAL' },
      velocity: { id: 'demo-velocity', amount: 1200, average: 700, count: 27, isNew: true, hour: 11, location: 'BHOPAL' },
    };
    const demo = demos[kind];
    $('#transaction-id').value = `${demo.id}-${Date.now()}`;
    $('#amount').value = demo.amount;
    $('#sender-average').value = demo.average;
    $('#sender-count').value = demo.count;
    $('#new-receiver').checked = demo.isNew;
    $('#location').value = demo.location;
    $('#sender-home-city').value = demo.location;
    if (demo.fixed) $('#timestamp').value = demo.fixed;
    else {
      now.setHours(demo.hour, 30, 0, 0);
      $('#timestamp').value = localDateTime(now);
    }
    $('#scanner-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
    analyze(formPayload());
  }

  function simulateTransaction() {
    const sample = [
      { amount: 390, average: 520, count: 2, isNew: false, hour: 14 },
      { amount: 4200, average: 1400, count: 4, isNew: true, hour: 12 },
      { amount: 7800, average: 900, count: 12, isNew: true, hour: 2 },
    ][Math.floor(Math.random() * 3)];
    const now = new Date();
    now.setHours(sample.hour, Math.floor(Math.random() * 60), 0, 0);
    $('#transaction-id').value = `sim-${Date.now()}`;
    $('#amount').value = sample.amount;
    $('#sender-average').value = sample.average;
    $('#sender-count').value = sample.count;
    $('#new-receiver').checked = sample.isNew;
    $('#location').value = 'BHOPAL';
    $('#sender-home-city').value = 'BHOPAL';
    $('#timestamp').value = localDateTime(now);
    analyze(formPayload());
  }

  function activateSection(id) {
    const valid = ['dashboard', 'scanner', 'alerts', 'transactions', 'performance', 'health', 'explanation', 'indicators'];
    const sectionId = valid.includes(id) ? id : 'dashboard';
    $$('.page-section').forEach(section => section.classList.toggle('section-active', section.id === sectionId));
    $$('.nav-link').forEach(link => link.classList.toggle('active', link.getAttribute('href') === `#${sectionId}`));
    const selected = $(`.nav-link[href="#${sectionId}"]`);
    setCell('current-section', selected ? selected.textContent.trim().replace(/\d+$/, '').trim() : sectionId.replace('-', ' '));
    $('#sidebar').classList.remove('sidebar-open');
    if (window.location.hash !== `#${sectionId}`) window.location.hash = sectionId;
  }

  function openDashboard(section = 'dashboard') {
    $('#landing-page').classList.add('hidden');
    $('#dashboard-app').classList.remove('hidden');
    activateSection(section);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function setupNavigation() {
    $$('.nav-link').forEach(link => link.addEventListener('click', event => { event.preventDefault(); activateSection(link.getAttribute('href').slice(1)); }));
    $$('[data-enter-dashboard]').forEach(button => button.addEventListener('click', () => openDashboard('dashboard')));
    $$('[data-open-section]').forEach(button => button.addEventListener('click', () => openDashboard(button.dataset.openSection)));
    const expandDashboard = () => $('#dashboard').classList.remove('dashboard-overview-collapsed');
    $$('[data-expand-dashboard]').forEach(button => button.addEventListener('click', () => { expandDashboard(); window.setTimeout(() => $('#latest-risk-panel').scrollIntoView({ behavior: 'smooth', block: 'start' }), 60); }));
    $$('[data-focus-chart]').forEach(button => button.addEventListener('click', () => { openDashboard('dashboard'); expandDashboard(); window.setTimeout(() => document.getElementById(button.dataset.focusChart).scrollIntoView({ behavior: 'smooth', block: 'center' }), 80); }));
    window.addEventListener('hashchange', () => {
      const id = window.location.hash.slice(1);
      if (id === 'landing') { $('#dashboard-app').classList.add('hidden'); $('#landing-page').classList.remove('hidden'); }
      else if (id && $('#dashboard-app').classList.contains('hidden')) openDashboard(id);
      else if (id && $(`#${id}.page-section`)) activateSection(id);
    });
    $('#mobile-menu').addEventListener('click', () => $('#sidebar').classList.toggle('sidebar-open'));
  }

  function setupControls() {
    $('#scanner-form').addEventListener('submit', event => {
      event.preventDefault();
      const form = $('#scanner-form');
      if (!form.reportValidity()) return;
      const payload = formPayload();
      if (!Number.isFinite(new Date(payload.timestamp).getTime())) {
        $('#form-error').textContent = 'Enter a valid date and time.';
        $('#form-error').classList.remove('hidden');
        return;
      }
      analyze(payload);
    });
    $$('[data-demo]').forEach(button => button.addEventListener('click', () => fillDemo(button.dataset.demo)));
    $('#simulate-button').addEventListener('click', simulateTransaction);
    $('#transactions-body').addEventListener('click', event => {
      const button = event.target.closest('[data-detail-id]');
      if (button) showTransactionDetails(state.transactions.find(item => item.transaction_id === button.dataset.detailId));
    });
    $('#transaction-dialog').addEventListener('click', event => { if (event.target === $('#transaction-dialog')) $('#transaction-dialog').close(); });
    $('#alert-filter').addEventListener('change', () => renderAlerts(state.alerts));
    ['transaction-search', 'transaction-risk-filter', 'transaction-status-filter'].forEach(id => {
      document.getElementById(id).addEventListener(id === 'transaction-search' ? 'input' : 'change', () => {
        state.transactionPage = 0; renderTransactions(state.transactions);
      });
    });
    $('#transactions-foot').addEventListener('click', event => {
      const button = event.target.closest('[data-page]');
      if (!button || button.disabled) return;
      state.transactionPage += button.dataset.page === 'next' ? 1 : -1;
      renderTransactions(state.transactions);
    });
    $('#alerts-body').addEventListener('click', event => {
      const button = event.target.closest('[data-view-id]');
      if (!button) return;
      const item = state.alerts.find(candidate => candidate.transaction_id === button.dataset.viewId);
      if (item) showTransactionDetails(item);
    });
  }

  async function initialLoad() {
    setCell('today-label', new Date().toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }));
    $('#timestamp').value = localDateTime();
    setupNavigation();
    setupControls();
    renderIndicators();
    const initialSection = window.location.hash.slice(1);
    if (initialSection === 'dashboard' || $(`#${initialSection}.page-section`)) openDashboard(initialSection);
    try {
      await refreshTables();
      const latest = state.transactions[0];
      if (latest) renderLatest(latest);
      setCell('last-sync', new Date().toLocaleTimeString());
    } catch (error) {
      showRiskError(error.message);
      renderAlerts([]); renderTransactions([]);
    }
    await Promise.allSettled([refreshStats(), refreshMetrics(), refreshHealth()]);
    window.setInterval(async () => {
      try {
        await refreshTables();
        setCell('last-sync', new Date().toLocaleTimeString());
      } catch (error) { setCell('last-sync', 'API unavailable'); }
    }, 5000);
    window.setInterval(() => refreshStats().catch(() => {}), 15000);
    window.setInterval(() => { refreshMetrics(); refreshHealth(); }, 15000);
  }

  document.addEventListener('DOMContentLoaded', initialLoad);
})();
