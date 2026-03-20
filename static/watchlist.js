// ---------------------------------------------------------------------------
// Watchlist page – manages adding/removing tickers and running predictions
// ---------------------------------------------------------------------------

const addForm = document.getElementById("add-watchlist-form");
const tickerInput = document.getElementById("watchlist-ticker");
const addButton = document.getElementById("add-button");

const messageDiv = document.getElementById("watchlist-message");
const errorDiv = document.getElementById("watchlist-error");

const watchlistTable = document.getElementById("watchlist-table");
const watchlistBody = document.getElementById("watchlist-body");
const emptyState = document.getElementById("watchlist-empty");

const watchlistModel = document.getElementById("watchlist-model");

const overlay = document.getElementById("prediction-overlay");
const overlayBody = document.getElementById("overlay-body");
const closeOverlayBtn = document.getElementById("close-overlay");

// -------- Helpers --------

function showMessage(text) {
    messageDiv.textContent = text;
    messageDiv.classList.remove("hidden");
    setTimeout(() => messageDiv.classList.add("hidden"), 4000);
}

function showError(text) {
    errorDiv.textContent = text;
    errorDiv.classList.remove("hidden");
    setTimeout(() => errorDiv.classList.add("hidden"), 5000);
}

function formatCurrency(value) {
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
    }).format(value);
}

function formatPercent(value) {
    const pct = value * 100;
    const sign = pct > 0 ? "+" : "";
    return `${sign}${pct.toFixed(2)}%`;
}

function toneClass(value) {
    if (value > 0) return "value-positive";
    if (value < 0) return "value-negative";
    return "value-neutral";
}

// -------- Load watchlist --------

async function loadWatchlist() {
    try {
        const res = await fetch("/api/watchlist");
        const items = await res.json();
        renderWatchlist(items);
    } catch {
        showError("Failed to load watchlist.");
    }
}

function renderWatchlist(items) {
    watchlistBody.innerHTML = "";

    if (items.length === 0) {
        emptyState.classList.remove("hidden");
        watchlistTable.classList.add("hidden");
        return;
    }

    emptyState.classList.add("hidden");
    watchlistTable.classList.remove("hidden");

    items.forEach((item) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><strong>${item.ticker}</strong></td>
            <td>${item.company_name || "\u2014"}</td>
            <td>
                <button
                    class="badge-btn ${item.is_owned ? "badge-owned" : "badge-not-owned"}"
                    data-action="toggle-owned"
                    data-id="${item.id}"
                    data-owned="${item.is_owned}"
                >
                    ${item.is_owned ? "Owned" : "Not Owned"}
                </button>
            </td>
            <td>${item.date_added}</td>
            <td class="action-cell">
                <button class="btn-small btn-primary"
                        data-action="run-prediction"
                        data-ticker="${item.ticker}">
                    Run Prediction
                </button>
                <button class="btn-small btn-danger"
                        data-action="remove"
                        data-id="${item.id}">
                    Remove
                </button>
            </td>
        `;
        watchlistBody.appendChild(tr);
    });
}

// -------- Event delegation for table buttons --------

watchlistBody.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;

    const action = btn.dataset.action;
    if (action === "toggle-owned") {
        toggleOwned(Number(btn.dataset.id), Number(btn.dataset.owned), btn);
    } else if (action === "run-prediction") {
        runPrediction(btn.dataset.ticker, btn);
    } else if (action === "remove") {
        removeFromWatchlist(Number(btn.dataset.id));
    }
});

// -------- Add to watchlist --------

addForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const ticker = tickerInput.value.trim().toUpperCase();
    tickerInput.value = ticker;

    if (!ticker) {
        showError("Please enter a ticker symbol.");
        return;
    }

    addButton.disabled = true;
    addButton.textContent = "Adding\u2026";

    try {
        const res = await fetch("/api/watchlist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker }),
        });
        const data = await res.json();

        if (!res.ok) {
            showError(data.error || "Failed to add ticker.");
        } else {
            showMessage(`${ticker} added to watchlist.`);
            tickerInput.value = "";
            loadWatchlist();
        }
    } catch {
        showError("Failed to add ticker.");
    } finally {
        addButton.disabled = false;
        addButton.textContent = "Add to Watchlist";
    }
});

// -------- Toggle owned --------

async function toggleOwned(id, currentlyOwned, btn) {
    btn.disabled = true;
    try {
        await fetch(`/api/watchlist/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_owned: currentlyOwned ? 0 : 1 }),
        });
        loadWatchlist();
    } catch {
        showError("Failed to update.");
    }
}

// -------- Remove from watchlist --------

async function removeFromWatchlist(id) {
    if (!confirm("Remove this stock from your watchlist?")) return;

    try {
        await fetch(`/api/watchlist/${id}`, { method: "DELETE" });
        loadWatchlist();
    } catch {
        showError("Failed to remove.");
    }
}

// -------- Run prediction (uses the selected model) --------

async function runPrediction(ticker, btn) {
    const modelName = watchlistModel.value;

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
            showError(data.error || "Failed to run prediction.");
            return;
        }

        const result = data.result;
        const pred = data.prediction;

        // Update overlay heading to reflect which model was used
        document.getElementById("overlay-model-name").textContent = modelName;

        overlayBody.innerHTML = `
            <div class="results-grid" style="grid-template-columns: repeat(2, 1fr);">
                <article class="result-box">
                    <span class="box-label">Ticker</span>
                    <strong>${result.ticker}</strong>
                </article>
                <article class="result-box">
                    <span class="box-label">Latest Close</span>
                    <strong>${formatCurrency(result.latest_close)}</strong>
                </article>
                <article class="result-box">
                    <span class="box-label">Predicted 30-Day Return</span>
                    <strong class="${toneClass(result.predicted_return)}">
                        ${formatPercent(result.predicted_return)}
                    </strong>
                </article>
                <article class="result-box">
                    <span class="box-label">Estimated Price in 30 Days</span>
                    <strong>${formatCurrency(result.estimated_price_30d)}</strong>
                </article>
            </div>
            <p class="hint" style="margin-top:14px;">
                Prediction saved as <strong>${pred.status}</strong> (${modelName}). View all
                predictions on the <a href="/predictions">Predictions</a> page.
            </p>
        `;
        overlay.classList.remove("hidden");
    } catch {
        showError("Something went wrong while running the prediction.");
    } finally {
        btn.disabled = false;
        btn.textContent = "Run Prediction";
    }
}

// -------- Close overlay --------

closeOverlayBtn.addEventListener("click", () => overlay.classList.add("hidden"));
overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.classList.add("hidden");
});

// -------- Init --------

loadWatchlist();
