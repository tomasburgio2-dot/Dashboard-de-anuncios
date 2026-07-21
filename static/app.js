const state = {
  market: "es",
  range: "today",
  sort: "spend",
  data: [],
  lastFetch: null,
};

const CURRENCY = { es: "EUR", mx: "MXN" };
const LOCALE = { es: "es-ES", mx: "es-MX" };

const RESULT_LABELS = {
  lead: "Leads",
  "offsite_conversion.fb_pixel_lead": "Leads",
  "onsite_conversion.lead_grouped": "Leads",
  "onsite_conversion.messaging_conversation_started_7d": "Conversaciones",
  purchase: "Compras",
  "offsite_conversion.fb_pixel_purchase": "Compras",
  link_click: "Clics al enlace",
};

const el = (id) => document.getElementById(id);
const grid = el("grid");
const summaryEl = el("summary");
const freshnessEl = el("freshness");

function money(value, market) {
  return new Intl.NumberFormat(LOCALE[market], {
    style: "currency",
    currency: CURRENCY[market],
    maximumFractionDigits: 2,
  }).format(value || 0);
}

function number(value, decimals = 0) {
  return new Intl.NumberFormat("es-ES", {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  }).format(value || 0);
}

function setActiveSegment(containerId, value) {
  document.querySelectorAll(`#${containerId} button`).forEach((btn) => {
    const active = btn.dataset.value === value;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
}

function showState(name) {
  ["loading", "error", "empty"].forEach((s) => {
    el(`state-${s}`).hidden = s !== name;
  });
  grid.hidden = name !== null;
}

async function fetchData() {
  showState("loading");
  try {
    const res = await fetch(`/api/creatives?market=${state.market}&range=${state.range}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Error ${res.status}`);
    }
    const json = await res.json();
    state.data = json.creatives || [];
    state.lastFetch = new Date();
    render();
  } catch (err) {
    el("error-detail").textContent = err.message || "Error desconocido";
    showState("error");
  }
}

function sortData(rows) {
  const sorted = [...rows];
  switch (state.sort) {
    case "cost_per_result":
      sorted.sort((a, b) => (a.cost_per_result ?? Infinity) - (b.cost_per_result ?? Infinity));
      break;
    case "result_count":
      sorted.sort((a, b) => b.result_count - a.result_count);
      break;
    case "ctr":
      sorted.sort((a, b) => b.ctr - a.ctr);
      break;
    default:
      sorted.sort((a, b) => b.spend - a.spend);
  }
  return sorted;
}

function renderSummary(rows) {
  const totalSpend = rows.reduce((s, r) => s + r.spend, 0);
  const totalResults = rows.reduce((s, r) => s + (r.result_count || 0), 0);
  const avgCpr = totalResults ? totalSpend / totalResults : null;
  const resultLabel = rows.find((r) => r.result_type)
    ? RESULT_LABELS[rows.find((r) => r.result_type).result_type] || "Resultados"
    : "Resultados";

  summaryEl.innerHTML = `
    <div class="item"><span class="label">Creativos</span><span class="value">${rows.length}</span></div>
    <div class="item"><span class="label">Gastado</span><span class="value">${money(totalSpend, state.market)}</span></div>
    <div class="item"><span class="label">${resultLabel}</span><span class="value">${number(totalResults)}</span></div>
    <div class="item"><span class="label">Costo por resultado</span><span class="value">${avgCpr ? money(avgCpr, state.market) : "—"}</span></div>
  `;
}

function cardHTML(row) {
  const resultLabel = row.result_type ? (RESULT_LABELS[row.result_type] || "Resultados") : "Resultados";
  const isActive = row.status === "ACTIVE";
  const thumb = row.thumbnail_url
    ? `<img src="${row.thumbnail_url}" alt="Pieza creativa: ${escapeHTML(row.ad_name)}" loading="lazy" />`
    : `<div class="no-thumb">Sin miniatura disponible</div>`;

  return `
    <article class="card">
      <div class="card-thumb">
        ${thumb}
        <span class="status-pill ${isActive ? "active" : ""}">${statusLabel(row.status)}</span>
        <div class="cost-tag">
          <span class="n">${row.cost_per_result ? money(row.cost_per_result, state.market) : "—"}</span>
          <span class="l">por resultado</span>
        </div>
      </div>
      <div class="card-body">
        <p class="card-name">${escapeHTML(row.ad_name || "(sin nombre)")}</p>
        <p class="card-context">${escapeHTML(row.campaign_name || "")}</p>
        <div class="metrics">
          <div class="metric"><span class="label">Gastado</span><span class="value">${money(row.spend, state.market)}</span></div>
          <div class="metric"><span class="label">${resultLabel}</span><span class="value">${number(row.result_count)}</span></div>
          <div class="metric"><span class="label">CTR</span><span class="value">${number(row.ctr, 2)}%</span></div>
          <div class="metric"><span class="label">Alcance</span><span class="value">${number(row.reach)}</span></div>
        </div>
      </div>
    </article>
  `;
}

function statusLabel(status) {
  const map = {
    ACTIVE: "Activo",
    PAUSED: "Pausado",
    ARCHIVED: "Archivado",
    DELETED: "Eliminado",
    PENDING_REVIEW: "En revisión",
    DISAPPROVED: "Rechazado",
  };
  return map[status] || status || "—";
}

function escapeHTML(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function updateFreshness() {
  if (!state.lastFetch) return;
  const mins = Math.floor((Date.now() - state.lastFetch.getTime()) / 60000);
  freshnessEl.textContent = mins < 1 ? "Actualizado recién" : `Actualizado hace ${mins} min`;
}

function render() {
  if (!state.data.length) {
    showState("empty");
    renderSummary([]);
    return;
  }
  const rows = sortData(state.data);
  grid.innerHTML = rows.map(cardHTML).join("");
  renderSummary(rows);
  showState(null);
  updateFreshness();
}

// ---- Eventos de UI (todos son filtros de visualización, ninguno escribe nada) ----

document.querySelectorAll("#market-select button").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.market = btn.dataset.value;
    setActiveSegment("market-select", state.market);
    fetchData();
  });
});

document.querySelectorAll("#range-select button").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.range = btn.dataset.value;
    setActiveSegment("range-select", state.range);
    fetchData();
  });
});

el("sort-select").addEventListener("change", (e) => {
  state.sort = e.target.value;
  render();
});

el("retry-btn").addEventListener("click", fetchData);

// Auto-refresh cada 5 minutos para que el dashboard se sienta "en vivo"
// sin perseguir a la API de Meta a cada rato.
setInterval(fetchData, 5 * 60 * 1000);
setInterval(updateFreshness, 30 * 1000);

fetchData();
