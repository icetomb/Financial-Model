// ---------------------------------------------------------------------------
// Predict page – runs both Model 1 and Model 2 in parallel for comparison
// ---------------------------------------------------------------------------

const form = document.getElementById("predict-form");
const tickerInput = document.getElementById("ticker");
const predictButton = document.getElementById("predict-button");

const loadingSection = document.getElementById("loading-section");
const errorSection = document.getElementById("error-section");
const resultsSection = document.getElementById("results-section");

const progressBar = document.getElementById("progress-bar");
const progressLabel = document.getElementById("progress-label");

let progressTimer = null;
let progressValue = 0;

// -------- Progress helpers --------

function setProgress(value) {
    progressValue = value;
    progressBar.style.width = `${value}%`;
    progressLabel.textContent = `${Math.round(value)}%`;
}

function clearProgressTimer() {
    if (progressTimer) {
        window.clearInterval(progressTimer);
        progressTimer = null;
    }
}

function clearFeedback() {
    errorSection.textContent = "";
    errorSection.classList.add("hidden");
    resultsSection.classList.add("hidden");
}

function startLoading() {
    clearFeedback();
    setProgress(0);
    loadingSection.classList.remove("hidden");
    predictButton.disabled = true;
    predictButton.textContent = "Working...";

    clearProgressTimer();
    progressTimer = window.setInterval(() => {
        if (progressValue >= 92) return;
        const step = progressValue < 45 ? 5 : progressValue < 75 ? 3 : 1;
        setProgress(Math.min(progressValue + step, 92));
    }, 250);
}

async function stopLoading() {
    clearProgressTimer();
    setProgress(100);
    await new Promise((resolve) => window.setTimeout(resolve, 220));
    loadingSection.classList.add("hidden");
    predictButton.disabled = false;
    predictButton.textContent = "Run Both Models";
}

function showError(message) {
    errorSection.textContent = message;
    errorSection.classList.remove("hidden");
}

// -------- Formatters --------

function formatCurrency(value) {
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
    }).format(value);
}

function formatPercent(value) {
    const percent = value * 100;
    const sign = percent > 0 ? "+" : "";
    return `${sign}${percent.toFixed(2)}%`;
}

function formatMetric(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
    return value.toFixed(4);
}

function formatAccuracy(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
    return `${(value * 100).toFixed(1)}%`;
}

function setValueTone(element, value) {
    element.classList.remove("value-positive", "value-negative", "value-neutral");
    if (value > 0) element.classList.add("value-positive");
    else if (value < 0) element.classList.add("value-negative");
    else element.classList.add("value-neutral");
}

function setOutlookBadge(element, predicted_return, outlook) {
    element.className = "outlook-badge";
    element.textContent = `${outlook} outlook`;
    if (predicted_return > 0) element.classList.add("outlook-positive");
    else if (predicted_return < 0) element.classList.add("outlook-negative");
    else element.classList.add("outlook-neutral");
}

// -------- Render one model column --------

function renderModelColumn(prefix, data) {
    const errorEl = document.getElementById(`${prefix}-error`);
    const resultsEl = document.getElementById(`${prefix}-results`);

    if (data.error) {
        errorEl.textContent = data.error;
        errorEl.classList.remove("hidden");
        resultsEl.style.opacity = "0.3";
        return;
    }

    errorEl.classList.add("hidden");
    resultsEl.style.opacity = "1";

    setOutlookBadge(
        document.getElementById(`${prefix}-outlook`),
        data.predicted_return,
        data.outlook
    );
    document.getElementById(`${prefix}-summary`).textContent = data.summary;

    const returnEl = document.getElementById(`${prefix}-return`);
    returnEl.textContent = formatPercent(data.predicted_return);
    setValueTone(returnEl, data.predicted_return);

    const priceEl = document.getElementById(`${prefix}-price`);
    priceEl.textContent = formatCurrency(data.estimated_price_30d);
    setValueTone(priceEl, data.predicted_return);

    const metrics = data.metrics || {};
    document.getElementById(`${prefix}-mae`).textContent = formatMetric(metrics.mae);
    document.getElementById(`${prefix}-rmse`).textContent = formatMetric(metrics.rmse);
    document.getElementById(`${prefix}-r2`).textContent = formatMetric(metrics.r2);
    document.getElementById(`${prefix}-dir`).textContent = formatAccuracy(metrics.direction_accuracy);

    document.getElementById(`${prefix}-data-note`).textContent =
        `Data: ${data.latest_data_date}. Samples: ${data.samples.train}/${data.samples.test}.`;
}

// -------- Fetch one model prediction --------

async function fetchPrediction(ticker, modelName) {
    try {
        const response = await fetch("/predict", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker, model_name: modelName }),
        });
        const data = await response.json();
        if (!response.ok) {
            return { error: data.error || `${modelName} failed.` };
        }
        return data;
    } catch {
        return { error: `${modelName}: network error.` };
    }
}

// -------- Form submit --------

form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const ticker = tickerInput.value.trim().toUpperCase();
    tickerInput.value = ticker;

    if (!ticker) {
        clearFeedback();
        showError("Please enter a ticker symbol before running a prediction.");
        return;
    }

    startLoading();

    // Run both models in parallel
    const [m1Result, m2Result] = await Promise.all([
        fetchPrediction(ticker, "Model 1"),
        fetchPrediction(ticker, "Model 2"),
    ]);

    await stopLoading();

    // If both failed, show a single error
    if (m1Result.error && m2Result.error) {
        showError(m1Result.error);
        return;
    }

    // Shared header — use whichever model succeeded for ticker / close price
    const successResult = m1Result.error ? m2Result : m1Result;
    document.getElementById("result-ticker").textContent = successResult.ticker;
    document.getElementById("result-latest-close").textContent =
        formatCurrency(successResult.latest_close);

    // Render each model column
    renderModelColumn("m1", m1Result);
    renderModelColumn("m2", m2Result);

    // Saved-to-history note (check if either result was auto-saved)
    const savedNote = document.getElementById("saved-note");
    if (savedNote) {
        const anySaved = m1Result.saved_to_history || m2Result.saved_to_history;
        savedNote.classList.toggle("hidden", !anySaved);
    }

    resultsSection.classList.remove("hidden");
});
