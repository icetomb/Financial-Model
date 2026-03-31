// ---------------------------------------------------------------------------
// Recommendations page – stock screener with filtering, scoring, and actions
// ---------------------------------------------------------------------------

const recForm      = document.getElementById("rec-filters");
const recSubmit    = document.getElementById("rec-submit");
const sectorSelect = document.getElementById("rec-sector");
const industrySelect = document.getElementById("rec-industry");
const minCapSelect = document.getElementById("rec-min-cap");
const limitSelect  = document.getElementById("rec-limit");
const sortSelect   = document.getElementById("rec-sort");
const profitableCheckbox = document.getElementById("rec-profitable");

const recLoading   = document.getElementById("rec-loading");
const recResults   = document.getElementById("rec-results");
const recTable     = document.getElementById("rec-table");
const recBody      = document.getElementById("rec-body");
const recEmpty     = document.getElementById("rec-empty");
const recTitle     = document.getElementById("rec-results-title");
const recCount     = document.getElementById("rec-results-count");

const recMessage   = document.getElementById("rec-message");
const recError     = document.getElementById("rec-error");

const recOverlay   = document.getElementById("rec-overlay");
const recOverlayTitle = document.getElementById("rec-overlay-title");
const recOverlayBody  = document.getElementById("rec-overlay-body");
const recCloseOverlay = document.getElementById("rec-close-overlay");

let knownModels = [];

// -------- Helpers --------

function showRecMessage(text) {
    recMessage.textContent = text;
    recMessage.classList.remove("hidden");
    setTimeout(() => recMessage.classList.add("hidden"), 4000);
}

function showRecError(text) {
    recError.textContent = text;
    recError.classList.remove("hidden");
    setTimeout(() => recError.classList.add("hidden"), 6000);
}

function formatCurrency(value) {
    if (value == null) return "—";
    return new Intl.NumberFormat("en-US", {
        style: "currency", currency: "USD",
    }).format(value);
}

function formatPercent(value, decimals = 1) {
    if (value == null) return "—";
    const pct = value * 100;
    const sign = pct > 0 ? "+" : "";
    return `${sign}${pct.toFixed(decimals)}%`;
}

function formatLargeNumber(value) {
    if (value == null) return "—";
    if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
    if (value >= 1e9)  return `$${(value / 1e9).toFixed(2)}B`;
    if (value >= 1e6)  return `$${(value / 1e6).toFixed(1)}M`;
    return `$${value.toLocaleString()}`;
}

function toneClass(value) {
    if (value > 0) return "value-positive";
    if (value < 0) return "value-negative";
    return "value-neutral";
}

function scoreColorClass(score) {
    if (score >= 60) return "rec-score-high";
    if (score >= 35) return "rec-score-mid";
    return "rec-score-low";
}

// -------- Industry dropdown (depends on sector) --------

sectorSelect.addEventListener("change", async () => {
    const sector = sectorSelect.value;
    industrySelect.innerHTML = '<option value="">All Industries</option>';

    if (!sector) return;

    try {
        const res = await fetch(`/api/industries?sector=${encodeURIComponent(sector)}`);
        const industries = await res.json();
        industries.forEach((ind) => {
            const opt = document.createElement("option");
            opt.value = ind;
            opt.textContent = ind;
            industrySelect.appendChild(opt);
        });
    } catch {
        // silently fall back to "All Industries"
    }
});

// -------- Fetch recommendations --------

recForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    await fetchRecommendations();
});

async function fetchRecommendations() {
    const params = new URLSearchParams();
    if (sectorSelect.value)   params.set("sector", sectorSelect.value);
    if (industrySelect.value) params.set("industry", industrySelect.value);
    params.set("limit", limitSelect.value);
    params.set("min_market_cap", minCapSelect.value);
    params.set("sort_by", sortSelect.value);
    if (profitableCheckbox.checked) params.set("profitable_only", "1");

    recSubmit.disabled = true;
    recSubmit.textContent = "Scanning\u2026";
    recLoading.classList.remove("hidden");
    recResults.classList.add("hidden");

    try {
        const res = await fetch(`/api/recommendations?${params}`);
        const data = await res.json();

        if (!res.ok) {
            showRecError(data.error || "Failed to fetch recommendations.");
            return;
        }

        renderResults(data);
    } catch {
        showRecError("Something went wrong. Please try again.");
    } finally {
        recLoading.classList.add("hidden");
        recSubmit.disabled = false;
        recSubmit.textContent = "Find Recommendations";
    }
}

// -------- Render results table --------

function renderResults(results) {
    recResults.classList.remove("hidden");
    recBody.innerHTML = "";

    if (results.length === 0) {
        recEmpty.classList.remove("hidden");
        recTable.classList.add("hidden");
        recCount.textContent = "";
        recTitle.textContent = "Recommendations";
        return;
    }

    recEmpty.classList.add("hidden");
    recTable.classList.remove("hidden");

    const label = sectorSelect.value || "All Sectors";
    recTitle.textContent = `Recommendations — ${label}`;
    recCount.textContent = `${results.length} stock${results.length !== 1 ? "s" : ""}`;

    results.forEach((stock, idx) => {
        const tr = document.createElement("tr");

        const reasonsHtml = stock.reasons
            .slice(0, 4)
            .map((r) => `<span class="rec-reason-tag">${r}</span>`)
            .join("");
        const moreCount = stock.reasons.length - 4;
        const moreTag = moreCount > 0
            ? `<span class="rec-reason-tag rec-reason-more">+${moreCount} more</span>`
            : "";

        tr.innerHTML = `
            <td><strong>${idx + 1}</strong></td>
            <td><strong>${stock.ticker}</strong></td>
            <td class="rec-company-cell">${stock.company_name || "\u2014"}</td>
            <td>${formatCurrency(stock.current_price)}</td>
            <td>${formatCurrency(stock.week52_high)}</td>
            <td><span class="${toneClass(-stock.pct_below_high)}">${formatPercent(-stock.pct_below_high)}</span></td>
            <td><span class="rec-score-badge ${scoreColorClass(stock.recommendation_score)}">${stock.recommendation_score}</span></td>
            <td class="rec-reasons-cell">${reasonsHtml}${moreTag}</td>
            <td class="action-cell">
                <button class="btn-small btn-primary"
                        data-action="details"
                        data-idx="${idx}">
                    Details
                </button>
                <button class="btn-small btn-primary"
                        data-action="add-watchlist"
                        data-ticker="${stock.ticker}">
                    + Watchlist
                </button>
            </td>
        `;
        tr.dataset.stock = JSON.stringify(stock);
        recBody.appendChild(tr);
    });
}

// -------- Table action delegation --------

recBody.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;

    const action = btn.dataset.action;
    if (action === "add-watchlist") {
        addToWatchlist(btn.dataset.ticker, btn);
    } else if (action === "details") {
        const tr = btn.closest("tr");
        const stock = JSON.parse(tr.dataset.stock);
        showDetails(stock);
    }
});

// -------- Add to watchlist --------

async function addToWatchlist(ticker, btn) {
    btn.disabled = true;
    btn.textContent = "Adding\u2026";

    try {
        const res = await fetch("/api/watchlist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker }),
        });
        const data = await res.json();

        if (res.status === 409) {
            btn.textContent = "Already Added";
            btn.disabled = true;
            return;
        }
        if (!res.ok) {
            showRecError(data.error || "Failed to add to watchlist.");
            btn.disabled = false;
            btn.textContent = "+ Watchlist";
            return;
        }

        btn.textContent = "Added \u2713";
        btn.disabled = true;
        showRecMessage(`${ticker} added to your watchlist.`);
    } catch {
        showRecError("Failed to add to watchlist.");
        btn.disabled = false;
        btn.textContent = "+ Watchlist";
    }
}

// -------- Stock details overlay --------

function showDetails(stock) {
    recOverlayTitle.textContent = `${stock.ticker} — ${stock.company_name || "Details"}`;

    const metricsGrid = `
        <div class="results-grid" style="grid-template-columns: repeat(3, 1fr);">
            <article class="result-box">
                <span class="box-label">Current Price</span>
                <strong>${formatCurrency(stock.current_price)}</strong>
            </article>
            <article class="result-box">
                <span class="box-label">52-Week High</span>
                <strong>${formatCurrency(stock.week52_high)}</strong>
            </article>
            <article class="result-box">
                <span class="box-label">52-Week Low</span>
                <strong>${formatCurrency(stock.week52_low)}</strong>
            </article>
            <article class="result-box">
                <span class="box-label">% Below High</span>
                <strong class="${toneClass(-stock.pct_below_high)}">${formatPercent(-stock.pct_below_high)}</strong>
            </article>
            <article class="result-box">
                <span class="box-label">Market Cap</span>
                <strong>${formatLargeNumber(stock.market_cap)}</strong>
            </article>
            <article class="result-box">
                <span class="box-label">1-Month Return</span>
                <strong class="${toneClass(stock.month_return)}">${formatPercent(stock.month_return)}</strong>
            </article>
        </div>

        <h4 style="margin: 20px 0 8px;">Financial Health</h4>
        <div class="results-grid" style="grid-template-columns: repeat(3, 1fr);">
            <article class="result-box">
                <span class="box-label">Net Income</span>
                <strong class="${toneClass(stock.net_income)}">${formatLargeNumber(stock.net_income)}</strong>
            </article>
            <article class="result-box">
                <span class="box-label">Operating Cash Flow</span>
                <strong class="${toneClass(stock.operating_cashflow)}">${formatLargeNumber(stock.operating_cashflow)}</strong>
            </article>
            <article class="result-box">
                <span class="box-label">Free Cash Flow</span>
                <strong class="${toneClass(stock.free_cashflow)}">${formatLargeNumber(stock.free_cashflow)}</strong>
            </article>
            <article class="result-box">
                <span class="box-label">Revenue Growth</span>
                <strong class="${toneClass(stock.revenue_growth)}">${formatPercent(stock.revenue_growth)}</strong>
            </article>
            <article class="result-box">
                <span class="box-label">Debt / Equity</span>
                <strong>${stock.debt_to_equity != null ? stock.debt_to_equity.toFixed(2) : "\u2014"}</strong>
            </article>
            <article class="result-box">
                <span class="box-label">Return on Equity</span>
                <strong class="${toneClass(stock.roe)}">${formatPercent(stock.roe)}</strong>
            </article>
        </div>
    `;

    const reasonsList = stock.reasons
        .map((r) => `<li>${r}</li>`)
        .join("");

    const modelSelect = knownModels.length > 0
        ? `<select id="detail-model-select" class="filter-select" style="margin-right:8px;">
            ${knownModels.map((m) => `<option value="${m}">${m}</option>`).join("")}
           </select>`
        : "";

    recOverlayBody.innerHTML = `
        <div style="display:flex; align-items:center; gap:10px; margin-bottom:16px;">
            <span class="rec-score-badge ${scoreColorClass(stock.recommendation_score)}" style="font-size:1.1rem; padding:8px 14px;">
                Score: ${stock.recommendation_score} / 100
            </span>
            <span class="hint">${stock.sector} · ${stock.industry}</span>
        </div>

        ${metricsGrid}

        <h4 style="margin: 20px 0 8px;">Why Recommended</h4>
        <ul class="rec-reasons-list">${reasonsList}</ul>

        <div style="display:flex; align-items:center; gap:8px; margin-top:20px; flex-wrap:wrap;">
            ${modelSelect}
            <button id="detail-run-prediction" class="btn-small btn-primary" data-ticker="${stock.ticker}">
                Run Prediction
            </button>
            <button id="detail-add-watchlist" class="btn-small btn-primary" data-ticker="${stock.ticker}">
                + Add to Watchlist
            </button>
        </div>
        <div id="detail-prediction-result" style="margin-top:12px;"></div>
    `;

    recOverlay.classList.remove("hidden");

    document.getElementById("detail-run-prediction").addEventListener("click", (e) => {
        runPredictionFromDetail(e.target);
    });
    document.getElementById("detail-add-watchlist").addEventListener("click", (e) => {
        addToWatchlist(e.target.dataset.ticker, e.target);
    });
}

// -------- Run prediction from details overlay --------

async function runPredictionFromDetail(btn) {
    const ticker = btn.dataset.ticker;
    const modelSelect = document.getElementById("detail-model-select");
    const modelName = modelSelect ? modelSelect.value : "Model 1";
    const resultDiv = document.getElementById("detail-prediction-result");

    btn.disabled = true;
    btn.textContent = "Running\u2026";

    try {
        const res = await fetch("/api/predictions/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker, model_name: modelName }),
        });
        const data = await res.json();

        if (!res.ok) {
            resultDiv.innerHTML = `<div class="panel panel-error">${data.error || "Prediction failed."}</div>`;
            return;
        }

        const result = data.result;
        resultDiv.innerHTML = `
            <div class="panel panel-success">
                <strong>${modelName}</strong> prediction saved for ${ticker}:
                <span class="${toneClass(result.predicted_return)}">
                    ${formatPercent(result.predicted_return)}
                </span> predicted 30-day return
                (est. ${formatCurrency(result.estimated_price_30d)}).
                View on <a href="/predictions">Predictions</a> page.
            </div>
        `;
    } catch {
        resultDiv.innerHTML = '<div class="panel panel-error">Something went wrong.</div>';
    } finally {
        btn.disabled = false;
        btn.textContent = "Run Prediction";
    }
}

// -------- Close overlay --------

recCloseOverlay.addEventListener("click", () => recOverlay.classList.add("hidden"));
recOverlay.addEventListener("click", (e) => {
    if (e.target === recOverlay) recOverlay.classList.add("hidden");
});

// -------- Load model list (for prediction dropdown in details) --------

async function loadModels() {
    try {
        const res = await fetch("/api/models");
        knownModels = await res.json();
    } catch {
        knownModels = ["Model 1"];
    }
}

// -------- Init --------

loadModels();
