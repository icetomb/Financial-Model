// ---------------------------------------------------------------------------
// Predictions page – accordion grouped by ticker + all-model performance
// ---------------------------------------------------------------------------

const perfContainer = document.getElementById("perf-container");
const accordionContainer = document.getElementById("accordion-container");
const predictionsEmpty = document.getElementById("predictions-empty");

const evaluateBtn = document.getElementById("evaluate-btn");
const evaluateStatus = document.getElementById("evaluate-status");

const filterModel = document.getElementById("filter-model");
const filterStatus = document.getElementById("filter-status");
const filterTicker = document.getElementById("filter-ticker");

let allPredictions = [];
let knownModels = [];

// -------- Helpers --------

function formatPercent(value) {
    if (value === null || value === undefined) return "\u2014";
    const pct = value * 100;
    const sign = pct > 0 ? "+" : "";
    return `${sign}${pct.toFixed(2)}%`;
}

function formatCurrency(value) {
    if (value === null || value === undefined) return "\u2014";
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
    }).format(value);
}

function directionBadge(direction) {
    if (!direction) return "\u2014";
    const cls =
        direction === "up"
            ? "badge-up"
            : direction === "down"
              ? "badge-down"
              : "badge-neutral";
    return `<span class="dir-badge ${cls}">${direction}</span>`;
}

function statusBadge(status) {
    const cls = status === "completed" ? "badge-completed" : "badge-pending";
    return `<span class="status-badge ${cls}">${status}</span>`;
}

function resultBadge(pred) {
    if (pred.status !== "completed") return "\u2014";
    if (pred.direction_correct) {
        return '<span class="result-badge badge-correct">Correct</span>';
    }
    return '<span class="result-badge badge-incorrect">Incorrect</span>';
}

function magnitudeText(pred) {
    if (pred.status !== "completed" || !pred.magnitude_comparison) return "\u2014";
    const labels = {
        bigger: "Bigger move",
        smaller: "Smaller move",
        equal: "Equal move",
    };
    return labels[pred.magnitude_comparison] || pred.magnitude_comparison;
}

function toneClass(value) {
    if (value === null || value === undefined) return "";
    if (value > 0) return "value-positive";
    if (value < 0) return "value-negative";
    return "";
}

function modelBadge(modelName) {
    const num = modelName.replace(/\D/g, "") || "1";
    return `<span class="model-badge badge-model${num}">${modelName}</span>`;
}

// -------- Discover available models and populate filter --------

async function loadModels() {
    try {
        const res = await fetch("/api/models");
        knownModels = await res.json();
    } catch {
        knownModels = ["Model 1", "Model 2"];
    }

    filterModel.innerHTML = '<option value="">All Models</option>';
    knownModels.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        filterModel.appendChild(opt);
    });
}

// -------- Performance comparison (one card per model) --------

async function loadAllPerformance() {
    const results = await Promise.all(
        knownModels.map(async (model) => {
            try {
                const res = await fetch(
                    `/api/performance?model=${encodeURIComponent(model)}`
                );
                return await res.json();
            } catch {
                return { model_name: model, total_predictions: 0 };
            }
        })
    );

    perfContainer.innerHTML = "";

    const anyData = results.some((p) => p.total_predictions > 0);
    if (!anyData) {
        perfContainer.innerHTML =
            '<div class="empty-state">No predictions recorded yet.</div>';
        return;
    }

    results.forEach((perf) => {
        const card = document.createElement("div");
        card.className = "perf-card";

        if (perf.total_predictions === 0) {
            card.innerHTML = `
                <div class="perf-card-header">
                    <h4>${perf.model_name}</h4>
                </div>
                <div class="empty-state" style="padding:10px 0;">No predictions yet.</div>
            `;
        } else {
            card.innerHTML = `
                <div class="perf-card-header">
                    <h4>${perf.model_name}</h4>
                    <span class="perf-card-accuracy">${perf.direction_accuracy}%</span>
                </div>
                <div class="perf-card-grid">
                    <div class="perf-row">
                        <span>Total</span><strong>${perf.total_predictions}</strong>
                    </div>
                    <div class="perf-row">
                        <span>Completed</span><strong>${perf.completed_predictions}</strong>
                    </div>
                    <div class="perf-row">
                        <span>Pending</span><strong>${perf.pending_predictions}</strong>
                    </div>
                    <div class="perf-row">
                        <span>Correct</span><strong class="value-positive">${perf.correct_predictions}</strong>
                    </div>
                    <div class="perf-row">
                        <span>Incorrect</span><strong class="value-negative">${perf.incorrect_predictions}</strong>
                    </div>
                    <div class="perf-row">
                        <span>Dir. Accuracy</span><strong>${perf.direction_accuracy}%</strong>
                    </div>
                    <div class="perf-row">
                        <span>Avg Error</span><strong>${perf.avg_prediction_error}%</strong>
                    </div>
                    <div class="perf-row">
                        <span>Avg Predicted</span><strong>${perf.avg_predicted_return}%</strong>
                    </div>
                    <div class="perf-row">
                        <span>Avg Actual</span><strong>${perf.avg_actual_return}%</strong>
                    </div>
                </div>
            `;
        }

        perfContainer.appendChild(card);
    });
}

// -------- Predictions (load + accordion render) --------

async function loadPredictions() {
    try {
        const res = await fetch("/api/predictions");
        allPredictions = await res.json();
        renderAccordion();
    } catch (err) {
        console.error("Failed to load predictions:", err);
    }
}

function renderAccordion() {
    const modelVal = filterModel.value;
    const statusVal = filterStatus.value;
    const tickerVal = filterTicker.value.trim().toUpperCase();

    // Apply filters at the prediction level
    let filtered = allPredictions;
    if (modelVal) {
        filtered = filtered.filter((p) => p.model_name === modelVal);
    }
    if (statusVal) {
        filtered = filtered.filter((p) => p.status === statusVal);
    }
    if (tickerVal) {
        filtered = filtered.filter((p) => p.ticker.includes(tickerVal));
    }

    accordionContainer.innerHTML = "";

    if (filtered.length === 0) {
        predictionsEmpty.classList.remove("hidden");
        accordionContainer.classList.add("hidden");
        return;
    }

    predictionsEmpty.classList.add("hidden");
    accordionContainer.classList.remove("hidden");

    // Group by ticker
    const groups = {};
    filtered.forEach((p) => {
        if (!groups[p.ticker]) groups[p.ticker] = [];
        groups[p.ticker].push(p);
    });

    // Sort each group's predictions newest first
    Object.values(groups).forEach((preds) => {
        preds.sort((a, b) => b.prediction_date.localeCompare(a.prediction_date));
    });

    // Sort tickers by the most recent prediction date (newest first)
    const sortedTickers = Object.keys(groups).sort((a, b) => {
        return groups[b][0].prediction_date.localeCompare(
            groups[a][0].prediction_date
        );
    });

    sortedTickers.forEach((ticker) => {
        const preds = groups[ticker];
        const uniqueModels = [...new Set(preds.map((p) => p.model_name))];
        const latestDate = preds[0].prediction_date;

        // Accordion item wrapper
        const item = document.createElement("div");
        item.className = "accordion-item";

        // Collapsed header row
        const header = document.createElement("button");
        header.className = "accordion-header";
        header.innerHTML = `
            <span class="accordion-chevron">\u25B6</span>
            <strong class="accordion-ticker">${ticker}</strong>
            <span class="accordion-badges">${uniqueModels.map((m) => modelBadge(m)).join(" ")}</span>
            <span class="accordion-meta">
                ${preds.length} prediction${preds.length !== 1 ? "s" : ""} 
                &middot; latest ${latestDate}
            </span>
        `;

        header.addEventListener("click", () => {
            const isOpen = item.classList.toggle("open");
            header.querySelector(".accordion-chevron").textContent = isOpen
                ? "\u25BC"
                : "\u25B6";
        });

        // Expanded body with sub-table
        const body = document.createElement("div");
        body.className = "accordion-body";

        let rows = "";
        preds.forEach((p) => {
            rows += `
                <tr>
                    <td>${modelBadge(p.model_name)}</td>
                    <td>${p.prediction_date}</td>
                    <td>${formatCurrency(p.latest_close)}</td>
                    <td class="${toneClass(p.predicted_return)}">${formatPercent(p.predicted_return)}</td>
                    <td>${directionBadge(p.predicted_direction)}</td>
                    <td>${statusBadge(p.status)}</td>
                    <td class="${toneClass(p.actual_return)}">${formatPercent(p.actual_return)}</td>
                    <td>${directionBadge(p.actual_direction)}</td>
                    <td>${resultBadge(p)}</td>
                    <td>${magnitudeText(p)}</td>
                    <td>
                        <button class="btn-small btn-danger"
                                data-action="delete"
                                data-id="${p.id}">Delete</button>
                    </td>
                </tr>
            `;
        });

        body.innerHTML = `
            <div class="table-wrap">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Model</th>
                            <th>Date</th>
                            <th>Close</th>
                            <th>Pred. Return</th>
                            <th>Pred. Dir.</th>
                            <th>Status</th>
                            <th>Actual Return</th>
                            <th>Actual Dir.</th>
                            <th>Result</th>
                            <th>Magnitude</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;

        item.appendChild(header);
        item.appendChild(body);
        accordionContainer.appendChild(item);
    });
}

// -------- Delete a prediction (event delegation on accordion container) --------

accordionContainer.addEventListener("click", async (e) => {
    const btn = e.target.closest('button[data-action="delete"]');
    if (!btn) return;

    if (!confirm("Delete this prediction?")) return;

    btn.disabled = true;
    try {
        await fetch(`/api/predictions/${btn.dataset.id}`, { method: "DELETE" });
        loadPredictions();
        loadAllPerformance();
    } catch {
        alert("Failed to delete prediction.");
    }
});

// -------- Evaluate pending predictions --------

evaluateBtn.addEventListener("click", async () => {
    evaluateBtn.disabled = true;
    evaluateBtn.textContent = "Evaluating\u2026";
    evaluateStatus.textContent = "";

    try {
        const res = await fetch("/api/predictions/evaluate", { method: "POST" });
        const data = await res.json();

        if (data.evaluated_count > 0) {
            evaluateStatus.textContent =
                `Evaluated ${data.evaluated_count} prediction(s).`;
            loadPredictions();
            loadAllPerformance();
        } else {
            evaluateStatus.textContent = "No predictions ready for evaluation yet.";
        }
    } catch {
        evaluateStatus.textContent = "Error during evaluation.";
    } finally {
        evaluateBtn.disabled = false;
        evaluateBtn.textContent = "Evaluate Pending Predictions";
    }
});

// -------- Filters --------

filterModel.addEventListener("change", renderAccordion);
filterStatus.addEventListener("change", renderAccordion);
filterTicker.addEventListener("input", renderAccordion);

// -------- Init --------

async function init() {
    await loadModels();

    try {
        await fetch("/api/predictions/evaluate", { method: "POST" });
    } catch {
        // evaluation is best-effort
    }

    loadAllPerformance();
    loadPredictions();
}

init();
