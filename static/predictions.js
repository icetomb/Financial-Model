// ---------------------------------------------------------------------------
// Predictions page – shows prediction history, evaluation, and performance
// ---------------------------------------------------------------------------

const perfGrid = document.getElementById("perf-grid");
const perfEmpty = document.getElementById("perf-empty");

const predictionsTable = document.getElementById("predictions-table");
const predictionsBody = document.getElementById("predictions-body");
const predictionsEmpty = document.getElementById("predictions-empty");

const evaluateBtn = document.getElementById("evaluate-btn");
const evaluateStatus = document.getElementById("evaluate-status");

const filterStatus = document.getElementById("filter-status");
const filterTicker = document.getElementById("filter-ticker");

let allPredictions = [];

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

// -------- Performance summary --------

async function loadPerformance() {
    try {
        const res = await fetch("/api/performance?model=Model+1");
        const perf = await res.json();

        if (perf.total_predictions === 0) {
            perfEmpty.classList.remove("hidden");
            perfGrid.classList.add("hidden");
            return;
        }

        perfEmpty.classList.add("hidden");
        perfGrid.classList.remove("hidden");

        document.getElementById("perf-total").textContent = perf.total_predictions;
        document.getElementById("perf-completed").textContent = perf.completed_predictions;
        document.getElementById("perf-pending").textContent = perf.pending_predictions;
        document.getElementById("perf-correct").textContent = perf.correct_predictions;
        document.getElementById("perf-incorrect").textContent = perf.incorrect_predictions;
        document.getElementById("perf-accuracy").textContent = `${perf.direction_accuracy}%`;
        document.getElementById("perf-avg-error").textContent = `${perf.avg_prediction_error}%`;
        document.getElementById("perf-avg-predicted").textContent = `${perf.avg_predicted_return}%`;
        document.getElementById("perf-avg-actual").textContent = `${perf.avg_actual_return}%`;
    } catch (err) {
        console.error("Failed to load performance:", err);
    }
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
    const statusVal = filterStatus.value;
    const tickerVal = filterTicker.value.trim().toUpperCase();

    let filtered = allPredictions;
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
        loadPerformance();
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
            loadPerformance();
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

filterStatus.addEventListener("change", renderPredictions);
filterTicker.addEventListener("input", renderPredictions);

// -------- Init --------

// Auto-evaluate any overdue predictions on page load, then refresh the data
fetch("/api/predictions/evaluate", { method: "POST" })
    .then(() => {
        loadPerformance();
        loadPredictions();
    })
    .catch(() => {
        loadPerformance();
        loadPredictions();
    });
