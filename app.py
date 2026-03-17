from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from stock_30day_predictor import PredictionError, build_prediction


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/predict")
    def predict():
        payload = request.get_json(silent=True) or {}
        ticker = payload.get("ticker", "")

        try:
            result = build_prediction(ticker)
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

        return jsonify(result)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
