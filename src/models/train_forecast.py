"""Evaluate the XGBoost model (quality gate) and forecast next week."""
import os
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import xgboost as xgb
from snowflake.connector.pandas_tools import write_pandas
from src.ingest.load_raw import get_connection

FEATURES = ["SALES_LAG_1", "SALES_LAG_2", "SALES_LAG_4", "SALES_LAG_52",
            "SALES_ROLL_MEAN_4", "SALES_ROLL_MEAN_12", "SALES_ROLL_STD_12",
            "PROMO_ITEMS", "HOLIDAY_DAYS", "WEEK_OF_YEAR", "MONTH",
            "STORE_NBR", "STORE_CLUSTER", "FAMILY_CODE", "STORE_TYPE_CODE"]
MODEL_NAME = "xgboost_global"
WAPE_THRESHOLD = float(os.environ.get("WAPE_THRESHOLD", "0.12"))


def run_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_features():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM SALES_DB.ANALYTICS.FEATURES")
        df = cur.fetch_pandas_all()
    finally:
        conn.close()
    df["WEEK_START"] = pd.to_datetime(df["WEEK_START"])
    num = [c for c in df.columns if c not in ("FAMILY", "STORE_TYPE", "WEEK_START")]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    df["FAMILY_CODE"] = df["FAMILY"].astype("category").cat.codes
    df["STORE_TYPE_CODE"] = df["STORE_TYPE"].astype("category").cat.codes
    return df


def train_model(train):
    model = xgb.XGBRegressor(n_estimators=400, max_depth=8, learning_rate=0.05,
                             subsample=0.8, colsample_bytree=0.8,
                             tree_method="hist", n_jobs=-1)
    model.fit(train[FEATURES], np.log1p(train["SALES"].clip(lower=0)))
    return model


def predict(model, rows):
    return np.expm1(model.predict(rows[FEATURES])).clip(0)


def write(df, table):
    conn = get_connection()
    try:
        ok, _, n, _ = write_pandas(conn, df, table_name=table, database="SALES_DB",
                                   schema="ANALYTICS", auto_create_table=True,
                                   overwrite=False, quote_identifiers=False)
    finally:
        conn.close()
    if not ok:
        raise RuntimeError(f"Writing {table} failed")
    print(f"{table}: appended {n:,} rows")


def evaluate():
    """Hold out the latest actual week, train on earlier weeks, and check WAPE."""
    df = load_features()
    actual = df[df["IS_FUTURE"] == 0]
    eval_week = actual["WEEK_START"].max()
    train = actual[(actual["WEEK_START"] < eval_week) & actual["SALES_LAG_52"].notna()]
    test = actual[actual["WEEK_START"] == eval_week]

    pred = predict(train_model(train), test)
    wape = float(np.abs(test["SALES"].values - pred).sum() / np.abs(test["SALES"].values).sum())

    write(pd.DataFrame([{"RUN_TS": run_ts(), "MODEL": MODEL_NAME,
                         "EVAL_WEEK": str(eval_week.date()), "WAPE": wape,
                         "N_SERIES": int(len(test)), "THRESHOLD": WAPE_THRESHOLD,
                         "PASSED": wape <= WAPE_THRESHOLD}]), "ACCURACY_LOG")
    print(f"Holdout week {eval_week.date()}: WAPE = {wape:.4f} (threshold {WAPE_THRESHOLD})")
    if wape > WAPE_THRESHOLD:
        raise ValueError(f"Quality gate failed: WAPE {wape:.4f} > {WAPE_THRESHOLD}")


def forecast():
    """Train on all actual weeks and forecast the next week for every series."""
    df = load_features()
    train = df[(df["IS_FUTURE"] == 0) & df["SALES_LAG_52"].notna()]
    future = df[df["IS_FUTURE"] == 1]
    if future.empty:
        raise ValueError("No future rows found in FEATURES")

    out = pd.DataFrame({"STORE_NBR": future["STORE_NBR"].values,
                        "FAMILY": future["FAMILY"].values,
                        "WEEK_START": future["WEEK_START"].dt.date.values,
                        "FORECAST": predict(train_model(train), future),
                        "MODEL": MODEL_NAME,
                        "RUN_TS": run_ts()})
    write(out, "FORECASTS")
    print(f"Forecast week {out['WEEK_START'].iloc[0]}: total = {out['FORECAST'].sum():,.0f}")
