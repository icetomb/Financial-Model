// ---------------------------------------------------------------------------
// Predictions page – shows all-model performance comparison + prediction history
// ---------------------------------------------------------------------------

const perfContainer = document.getElementById("perf-container");

const predictionsTable = document.getElementById("predictions-table");
const predictionsBody = document.getElementById("predictions-body");
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

    // Populate the model filter dropdown dynamically
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
    // Fetch performance for every model in parallel
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

    // Check if any model has data
    const anyData = results.some((p) => p.total_predictions > 0);
    if (!anyData) {
        perfContainer.innerHTML =
            '<div class="empty-state">No predictions recorded yet.</div>';
        return;
    }

    // Build a card for each model
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

// -------- Predictions table --------

async function loadPredictions() {
    try {
        const res = await fetch("/api/predictions");
        allPredictions = await res.json();
        renderPredictions();
    } catch (err) {
        console.error("Failed to load predictions:", err);
    }
}

function renderPredictions() {
    const modelVal = filterModel.value;
    const statusVal = filterStatus.value;
    const tickerVal = filterTicker.value.trim().toUpperCase();

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

    predictionsBody.innerHTML = "";

    if (filtered.length === 0) {
        predictionsEmpty.classList.remove("hidden");
        predictionsTable.classList.add("hidden");
        return;
    }

    predictionsEmpty.classList.add("hidden");
    predictionsTable.classList.remove("hidden");

    filtered.forEach((p) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${modelBadge(p.model_name)}</td>
            <td><strong>${p.ticker}</strong></td>
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
        `;
        predictionsBody.appendChild(tr);
    });
}

// -------- Delete a prediction (event delegation on table body) --------

predictionsBody.addEventListener("click", async (e) => {
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

// -------- Filters (table only — performance always shows all) --------

filterModel.addEventListener("change", renderPredictions);
filterStatus.addEventListener("change", renderPredictions);
filterTicker.addEventListener("input", renderPredictions);

// -------- Init --------

async function init() {
    // Discover models, auto-evaluate, then load everything
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
