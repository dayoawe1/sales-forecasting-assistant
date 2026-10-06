"""Weekly sales forecasting pipeline: Snowflake -> PySpark -> XGBoost."""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator


def _load_raw():
    from src.ingest.load_raw import main
    main()


def _build_staging():
    from src.transform.run_staging import main
    main()


def _build_features():
    from src.features.build_features import main
    main()


def _evaluate_model():
    from src.models.train_forecast import evaluate
    evaluate()


def _forecast_next_week():
    from src.models.train_forecast import forecast
    forecast()


default_args = {"owner": "dayo", "retries": 1, "retry_delay": timedelta(minutes=5)}

with DAG(
    dag_id="sales_forecasting_pipeline",
    description="Load -> stage -> features -> quality gate -> forecast",
    start_date=datetime(2026, 10, 1),
    schedule="0 6 * * 1",          # every Monday at 06:00 UTC
    catchup=False,
    default_args=default_args,
    tags=["forecasting", "snowflake", "spark", "xgboost"],
) as dag:
    load_raw = PythonOperator(task_id="load_raw", python_callable=_load_raw)
    build_staging = PythonOperator(task_id="build_staging", python_callable=_build_staging)
    build_features = PythonOperator(task_id="build_features", python_callable=_build_features)
    evaluate_model = PythonOperator(task_id="evaluate_model", python_callable=_evaluate_model)
    forecast_next_week = PythonOperator(task_id="forecast_next_week",
                                        python_callable=_forecast_next_week)

    load_raw >> build_staging >> build_features >> evaluate_model >> forecast_next_week
