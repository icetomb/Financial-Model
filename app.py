from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf
from flask import Flask, jsonify, render_template, request

import database as db
from models.model_1 import PredictionError
from models.model_1 import build_prediction as build_prediction_m1
from models.model_2 import build_prediction as build_prediction_m2
from services.downside_risk import get_downside_risk_stocks, get_stock_news
from services.recommendations import get_recommendations, get_ticker_news
from services.stock_universe import get_industries, get_sectors

# Maps model names to their build_prediction functions.
# Adding a future Model 3 means importing it and adding one entry here.
MODEL_BUILDERS = {
    "Model 1": build_prediction_m1,
    "Model 2": build_prediction_m2,
}


def _return_direction(value: float) -> str:
    """Map a return value to 'up', 'down', or 'neutral'."""
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "neutral"


def _run_model(model_name: str, ticker: str) -> dict:
    """Look up the correct model builder and run it."""
    build_fn = MODEL_BUILDERS.get(model_name, build_prediction_m1)
    return build_fn(ticker)


def create_app() -> Flask:
    app = Flask(__name__)

    # Create / verify database tables on startup
    db.init_db()

    # ------------------------------------------------------------------
    # Page routes
    # ------------------------------------------------------------------

    @app.get("/")
    def index():
        return render_template("index.html", active_page="predict")

    @app.get("/watchlist")
    def watchlist_page():
        return render_template("watchlist.html", active_page="watchlist")

    @app.get("/predictions")
    def predictions_page():
        return render_template("predictions.html", active_page="predictions")

    @app.get("/recommendations")
    def recommendations_page():
        return render_template(
            "recommendations.html",
            active_page="recommendations",
            sectors=get_sectors(),
        )

    # ------------------------------------------------------------------
    # Recommendations API
    # ------------------------------------------------------------------

    @app.get("/api/recommendations")
    def api_get_recommendations():
        """Run the recommendation engine with optional filters."""
        sector = request.args.get("sector") or None
        industry = request.args.get("industry") or None
        sort_by = request.args.get("sort_by", "score")

        try:
            limit = int(request.args.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20

        try:
            min_market_cap = float(request.args.get("min_market_cap", 0))
        except (TypeError, ValueError):
            min_market_cap = 0

        profitable_only = request.args.get("profitable_only", "").lower() in ("1", "true", "yes")

        try:
            results = get_recommendations(
                sector=sector,
                industry=industry,
                limit=limit,
                min_market_cap=min_market_cap,
                profitable_only=profitable_only,
                sort_by=sort_by,
            )
        except Exception:
            app.logger.exception("Recommendation engine error")
            return jsonify({"error": "Failed to generate recommendations."}), 500

        return jsonify(results)

    @app.get("/api/industries")
    def api_get_industries():
        """Return industries for a given sector (or all if none specified)."""
        sector = request.args.get("sector") or None
        return jsonify(get_industries(sector))

    @app.get("/api/news/<ticker>")
    def api_get_news(ticker: str):
        """Return recent news headlines for a ticker."""
        try:
            news = get_ticker_news(ticker.upper())
        except Exception:
            app.logger.exception("News fetch error for %s", ticker)
            return jsonify([])
        return jsonify(news)

    # ------------------------------------------------------------------
    # Downside Risk Scanner API  (Likely Decliners)
    # ------------------------------------------------------------------

    @app.get("/api/downside-risk")
    def api_get_downside_risk():
        """Run the downside-risk scanner with optional filters.

        Returns ranked "likely decliners" – stocks showing technical
        weakness, negative model forecasts, or other downside signals.
        News sentiment is included as contextual information only and
        does NOT distort the quantitative score.
        """
        sector = request.args.get("sector") or None
        industry = request.args.get("industry") or None

        try:
            limit = int(request.args.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20

        try:
            min_market_cap = float(request.args.get("min_market_cap", 0))
        except (TypeError, ValueError):
            min_market_cap = 0

        # Allow callers to skip the slow Model 1 pass (default is to use it).
        use_model = request.args.get("use_model", "1").lower() in ("1", "true", "yes")

        try:
            results = get_downside_risk_stocks(
                sector=sector,
                industry=industry,
                limit=limit,
                min_market_cap=min_market_cap,
                use_model=use_model,
            )
        except Exception:
            app.logger.exception("Downside risk scanner error")
            return jsonify({"error": "Failed to scan for downside risk."}), 500

        return jsonify(results)

    @app.get("/api/downside-risk/news/<ticker>")
    def api_get_downside_risk_news(ticker: str):
        """Recent news for a decliner with relative-time strings included."""
        try:
            news = get_stock_news(ticker.upper())
        except Exception:
            app.logger.exception("Downside news fetch error for %s", ticker)
            return jsonify([])
        return jsonify(news)

    # ------------------------------------------------------------------
    # Predict API  (supports model selection via model_name in payload)
    # ------------------------------------------------------------------

    @app.post("/predict")
    def predict():
        payload = request.get_json(silent=True) or {}
        ticker = payload.get("ticker", "")
        model_name = payload.get("model_name", "Model 1")

        try:
            result = _run_model(model_name, ticker)
        except PredictionError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            app.logger.exception("Unexpected error while generating prediction")
            return (
                jsonify(
                    {
                        "error": (
                            "Something went wrong while generating the prediction. "
                            "Please try again."
                        )
                    }
                ),
                500,
            )

        # Tag the result with the model that produced it
        result["model_name"] = model_name

        # If the ticker is on the watchlist, persist (skip if duplicate)
        watchlist_item = db.get_watchlist_item_by_ticker(result["ticker"])
        pred_date = result["latest_data_date"]
        if watchlist_item and not db.prediction_exists(model_name, result["ticker"], pred_date):
            db.save_prediction(
                model_name=model_name,
                ticker=result["ticker"],
                prediction_date=pred_date,
                latest_close=result["latest_close"],
                predicted_return=result["predicted_return"],
                predicted_price=result["estimated_price_30d"],
                predicted_direction=_return_direction(result["predicted_return"]),
                forecast_horizon_days=result.get("forecast_horizon_days", 30),
            )
            result["saved_to_history"] = True

        return jsonify(result)

    # ------------------------------------------------------------------
    # Watchlist API
    # ------------------------------------------------------------------

    @app.get("/api/watchlist")
    def api_get_watchlist():
        return jsonify(db.get_watchlist())

    @app.post("/api/watchlist")
    def api_add_to_watchlist():
        payload = request.get_json(silent=True) or {}
        ticker = payload.get("ticker", "").strip().upper()
        if not ticker:
            return jsonify({"error": "Ticker is required."}), 400

        # Best-effort company name lookup via yfinance
        company_name = ""
        try:
            info = yf.Ticker(ticker).info
            company_name = info.get("shortName", "") or info.get("longName", "") or ""
        except Exception:
            pass

        item = db.add_to_watchlist(ticker, company_name)
        if item is None:
            return jsonify({"error": f"{ticker} is already on your watchlist."}), 409

        return jsonify(item), 201

    @app.put("/api/watchlist/<int:item_id>")
    def api_update_watchlist(item_id):
        payload = request.get_json(silent=True) or {}
        success = db.update_watchlist_item(item_id, **payload)
        if not success:
            return jsonify({"error": "Nothing to update."}), 400
        return jsonify({"ok": True})

    @app.delete("/api/watchlist/<int:item_id>")
    def api_remove_from_watchlist(item_id):
        db.remove_from_watchlist(item_id)
        return jsonify({"ok": True})

    # ------------------------------------------------------------------
    # Predictions API
    # ------------------------------------------------------------------

    @app.get("/api/predictions")
    def api_get_predictions():
        model_name = request.args.get("model")
        ticker = request.args.get("ticker")
        status = request.args.get("status")
        return jsonify(db.get_predictions(model_name=model_name, ticker=ticker, status=status))

    @app.post("/api/predictions/run")
    def api_run_prediction():
        """Run a prediction for a ticker and persist it to the database."""
        payload = request.get_json(silent=True) or {}
        ticker = payload.get("ticker", "")
        model_name = payload.get("model_name", "Model 1")

        try:
            result = _run_model(model_name, ticker)
        except PredictionError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            app.logger.exception("Unexpected error while generating prediction")
            return jsonify({"error": "Something went wrong. Please try again."}), 500

        pred_date = result["latest_data_date"]
        if db.prediction_exists(model_name, result["ticker"], pred_date):
            return jsonify({
                "error": f"{model_name} prediction for {result['ticker']} on {pred_date} already exists.",
                "duplicate": True,
            }), 409

        saved = db.save_prediction(
            model_name=model_name,
            ticker=result["ticker"],
            prediction_date=pred_date,
            latest_close=result["latest_close"],
            predicted_return=result["predicted_return"],
            predicted_price=result["estimated_price_30d"],
            predicted_direction=_return_direction(result["predicted_return"]),
            forecast_horizon_days=result.get("forecast_horizon_days", 30),
        )

        return jsonify({"prediction": saved, "result": result}), 201

    @app.delete("/api/predictions/<int:pred_id>")
    def api_delete_prediction(pred_id):
        db.delete_prediction(pred_id)
        return jsonify({"ok": True})

    @app.post("/api/predictions/evaluate")
    def api_evaluate_predictions():
        """Evaluate every pending prediction whose horizon has elapsed."""
        pending = db.get_pending_predictions()
        today = date.today()
        evaluated: list[int] = []
        errors: list[str] = []

        for pred in pending:
            pred_date = datetime.strptime(pred["prediction_date"], "%Y-%m-%d").date()
            target_date = pred_date + timedelta(days=pred["forecast_horizon_days"])

            if today < target_date:
                continue

            try:
                # Download the close price on (or just after) the target date
                ticker_data = yf.download(
                    pred["ticker"],
                    start=target_date.isoformat(),
                    end=(target_date + timedelta(days=7)).isoformat(),
                    progress=False,
                )
                if isinstance(ticker_data.columns, pd.MultiIndex):
                    ticker_data.columns = ticker_data.columns.get_level_values(0)

                if ticker_data.empty:
                    errors.append(f"No price data for {pred['ticker']} around {target_date}")
                    continue

                actual_price = float(ticker_data["Close"].iloc[0])
                actual_return = (actual_price - pred["latest_close"]) / pred["latest_close"]
                actual_direction = _return_direction(actual_return)

                direction_correct = pred["predicted_direction"] == actual_direction

                abs_predicted = abs(pred["predicted_return"])
                abs_actual = abs(actual_return)
                if abs(abs_actual - abs_predicted) < 0.0001:
                    magnitude = "equal"
                elif abs_actual > abs_predicted:
                    magnitude = "bigger"
                else:
                    magnitude = "smaller"

                prediction_error = actual_return - pred["predicted_return"]

                db.evaluate_prediction(
                    pred["id"],
                    actual_price,
                    actual_return,
                    actual_direction,
                    direction_correct,
                    magnitude,
                    prediction_error,
                )
                evaluated.append(pred["id"])

            except Exception as exc:
                errors.append(f"Error evaluating {pred['ticker']}: {exc}")

        return jsonify(
            {
                "evaluated_count": len(evaluated),
                "evaluated_ids": evaluated,
                "errors": errors,
            }
        )

    @app.get("/api/models")
    def api_get_models():
        """Return the list of available model names (drives dynamic UI)."""
        return jsonify(list(MODEL_BUILDERS.keys()))

    @app.get("/api/performance")
    def api_get_performance():
        model_name = request.args.get("model", "Model 1")
        return jsonify(db.get_model_performance(model_name))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
