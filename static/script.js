const form = document.getElementById("predict-form");
const tickerInput = document.getElementById("ticker");
const predictButton = document.getElementById("predict-button");

const loadingSection = document.getElementById("loading-section");
const errorSection = document.getElementById("error-section");
const resultsSection = document.getElementById("results-section");

const progressBar = document.getElementById("progress-bar");
const progressLabel = document.getElementById("progress-label");

const resultTicker = document.getElementById("result-ticker");
const resultSummary = document.getElementById("result-summary");
const resultOutlook = document.getElementById("result-outlook");
const resultLatestClose = document.getElementById("result-latest-close");
const resultPredictedReturn = document.getElementById("result-predicted-return");
const resultEstimatedPrice = document.getElementById("result-estimated-price");
const resultMae = document.getElementById("metric-mae");
const resultRmse = document.getElementById("metric-rmse");
const resultR2 = document.getElementById("metric-r2");
const resultDirectionAccuracy = document.getElementById("metric-direction-accuracy");
const resultDataNote = document.getElementById("result-data-note");

let progressTimer = null;
let progressValue = 0;

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

function clearResultStyles() {
    resultPredictedReturn.classList.remove("value-positive", "value-negative", "value-neutral");
    resultEstimatedPrice.classList.remove("value-positive", "value-negative", "value-neutral");
    resultOutlook.className = "outlook-badge";
}

function clearFeedback() {
    clearResultStyles();
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
        if (progressValue >= 92) {
            return;
        }

        const step = progressValue < 45 ? 7 : progressValue < 75 ? 4 : 1.5;
        setProgress(Math.min(progressValue + step, 92));
    }, 180);
}

async function stopLoading() {
    clearProgressTimer();
    setProgress(100);
    await new Promise((resolve) => window.setTimeout(resolve, 220));
    loadingSection.classList.add("hidden");
    predictButton.disabled = false;
    predictButton.textContent = "Run Prediction";
}

function showError(message) {
    errorSection.textContent = message;
    errorSection.classList.remove("hidden");
}

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
    if (value === null || value === undefined || Number.isNaN(value)) {
        return "N/A";
    }

    return value.toFixed(4);
}

function formatAccuracy(value) {
    if (value === null || value === undefined || Number.isNaN(value)) {
        return "N/A";
    }

    return `${(value * 100).toFixed(1)}%`;
}

function setValueTone(element, value) {
    element.classList.remove("value-positive", "value-negative", "value-neutral");

    if (value > 0) {
        element.classList.add("value-positive");
        return;
    }

    if (value < 0) {
        element.classList.add("value-negative");
        return;
    }

    element.classList.add("value-neutral");
}

function renderResults(data) {
    resultTicker.textContent = data.ticker;
    resultSummary.textContent = data.summary;
    resultLatestClose.textContent = formatCurrency(data.latest_close);
    resultPredictedReturn.textContent = formatPercent(data.predicted_return);
    resultEstimatedPrice.textContent = formatCurrency(data.estimated_price_30d);

    setValueTone(resultPredictedReturn, data.predicted_return);
    setValueTone(resultEstimatedPrice, data.predicted_return);

    resultOutlook.textContent = `${data.outlook} outlook`;
    resultOutlook.classList.add(
        data.predicted_return > 0
            ? "outlook-positive"
            : data.predicted_return < 0
              ? "outlook-negative"
              : "outlook-neutral"
    );

    const metrics = data.metrics || {};
    resultMae.textContent = formatMetric(metrics.mae);
    resultRmse.textContent = formatMetric(metrics.rmse);
    resultR2.textContent = formatMetric(metrics.r2);
    resultDirectionAccuracy.textContent = formatAccuracy(metrics.direction_accuracy);

    resultDataNote.textContent =
        `Latest market data used: ${data.latest_data_date}. ` +
        `Train/Test samples: ${data.samples.train}/${data.samples.test}.`;

    // Show a note when the prediction was auto-saved for a watchlist ticker
    const savedNote = document.getElementById("saved-note");
    if (savedNote) {
        if (data.saved_to_history) {
            savedNote.classList.remove("hidden");
        } else {
            savedNote.classList.add("hidden");
        }
    }

    resultsSection.classList.remove("hidden");
}

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

    try {
        const response = await fetch("/predict", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ ticker }),
        });

        let data = {};

        try {
            data = await response.json();
        } catch (error) {
            data = {};
        }

        await stopLoading();

        if (!response.ok) {
            throw new Error(data.error || "Could not generate a prediction for that ticker.");
        }

        renderResults(data);
    } catch (error) {
        await stopLoading();
        showError(
            error.message || "Something went wrong while generating the prediction."
        );
    }
});
