# Sales Forecasting & Insights Assistant

## Results

Models were evaluated with an 8-week rolling-origin backtest (1-week-ahead forecasts,
each trained only on prior weeks). Metric: WAPE (weighted absolute percentage error),
chosen over MAPE because many weeks have zero sales.

| Model | WAPE (top 10 series) | vs. baseline | WAPE (all 1,782 series) |
|---|---|---|---|
| Seasonal naive (same week last year) | 13.6% | — | 16.0% |
| Prophet (+ promotions, holidays) | 9.0% | -34% | — |
| ARIMA(1,1,1) + Fourier seasonality | 6.8% | -50% | — |
| **XGBoost, global model** | **5.3%** | **-61%** | **7.3% (-54%)** |

**Key takeaways**
- A single global XGBoost model outperformed per-series statistical models and scaled
  to all 1,782 store-category series without per-series tuning.
- Recent sales momentum (last week, 4-week average) drives 1-week-ahead accuracy;
  last year's value adds little because the business grew ~2.5x over the period.
- Oil price was excluded from forecasting models to avoid look-ahead leakage, since
  next week's price is unknown at forecast time.

![Model comparison](docs/images/05_model_comparison.png)
![Forecast example](docs/images/06_forecast_example.png)
![Feature importance](docs/images/07_feature_importance.png)



## Pipeline (Airflow)

A weekly Airflow DAG (Docker, runs Mondays 06:00 UTC) automates the full workflow:

`load_raw` → `build_staging` → `build_features` → `evaluate_model` → `forecast_next_week`

| Task | What it does |
|---|---|
| load_raw | Loads Kaggle CSVs into Snowflake `RAW` |
| build_staging | Runs `sql/03_staging.sql`: type casting, oil fill-forward, store-level holidays, weekly aggregation |
| build_features | PySpark window functions build lag/rolling/calendar features, plus a "next week" row per series |
| evaluate_model | **Quality gate:** retrains XGBoost on all but the latest week, scores that week, logs WAPE to `ACCURACY_LOG`, and fails the run if WAPE exceeds 12% |
| forecast_next_week | Retrains on all history and writes 1,782 store-category forecasts to `ANALYTICS.FORECASTS` |

**Latest run:** holdout WAPE 9.99% (passed); next-week forecast for 1,782 series.

Run locally: `docker compose build && docker compose up -d`, then open http://localhost:8080.


## AI Assistant (LLM + RAG + text-to-SQL)

An OpenAI tool-calling agent answers business questions about the forecasts:

- **Text-to-SQL:** writes Snowflake queries for numbers, rankings and comparisons, executed
  through a read-only role with a table allow-list and write-statement blocking.
- **Hybrid RAG:** retrieves auto-generated weekly forecast summaries per store, combining a
  store-number metadata filter with embedding similarity (text-embedding-3-small).
- **Evaluation:** scored against SQL-computed ground truth. Two failures found in the first
  run (retrieval missing store-specific documents; an ambiguous percent-change definition)
  were fixed, raising accuracy from 8/9 to 9/9.
- **Automated:** Airflow regenerates summaries and embeddings after each weekly forecast.

Example: *"Which 3 stores have the biggest forecasted decline vs last week?"* returns
stores 25 (-24.0%), 18 (-19.4%) and 20 (-6.4%), computed live from Snowflake.
