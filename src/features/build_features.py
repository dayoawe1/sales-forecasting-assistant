"""Build forecasting features with PySpark, including a row for next week."""
from datetime import timedelta
import pandas as pd
from pyspark.sql import SparkSession, functions as F, Window

INPUT_COLS = ["STORE_NBR", "FAMILY", "WEEK_START", "STORE_TYPE", "STORE_CLUSTER", "SALES",
              "PROMO_ITEMS", "HOLIDAY_DAYS", "AVG_OIL_PRICE", "STORE_TRANSACTIONS", "IS_FUTURE"]


def get_spark():
    return (SparkSession.builder
            .appName("sales-features")
            .master("local[*]")
            .config("spark.driver.memory", "4g")
            .config("spark.sql.execution.arrow.pyspark.enabled", "true")
            .getOrCreate())


def build_features(sdf):
    """Lag, rolling and calendar features per store x family.
    Rolling windows use only PAST weeks (rows -N to -1) to prevent leakage."""
    w = Window.partitionBy("STORE_NBR", "FAMILY").orderBy("WEEK_START")
    for k in [1, 2, 4, 52]:
        sdf = sdf.withColumn(f"SALES_LAG_{k}", F.lag("SALES", k).over(w))
    return (sdf
            .withColumn("SALES_ROLL_MEAN_4",  F.avg("SALES").over(w.rowsBetween(-4, -1)))
            .withColumn("SALES_ROLL_MEAN_12", F.avg("SALES").over(w.rowsBetween(-12, -1)))
            .withColumn("SALES_ROLL_STD_12",  F.stddev("SALES").over(w.rowsBetween(-12, -1)))
            .withColumn("WEEK_OF_YEAR", F.weekofyear("WEEK_START"))
            .withColumn("MONTH", F.month("WEEK_START"))
            .withColumn("YEAR", F.year("WEEK_START")))


def load_weekly_with_future():
    from src.ingest.load_raw import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM SALES_DB.STAGING.SALES_WEEKLY")
        pdf = cur.fetch_pandas_all()
        cur.execute("SELECT STORE_NBR, DATE FROM SALES_DB.STAGING.STORE_HOLIDAYS")
        hol = cur.fetch_pandas_all()
    finally:
        conn.close()

    pdf["WEEK_START"] = pd.to_datetime(pdf["WEEK_START"])
    # drop partial first/last weeks
    pdf = pdf[(pdf["WEEK_START"] > pdf["WEEK_START"].min()) &
              (pdf["WEEK_START"] < pdf["WEEK_START"].max())].copy()
    pdf["IS_FUTURE"] = 0

    # one future row per series for the week we want to forecast
    next_week = pdf["WEEK_START"].max() + timedelta(days=7)
    future = pdf.sort_values("WEEK_START").groupby(["STORE_NBR", "FAMILY"]).tail(1).copy()
    future["WEEK_START"] = next_week
    future["SALES"] = float("nan")
    future["STORE_TRANSACTIONS"] = float("nan")
    future["IS_FUTURE"] = 1
    # PROMO_ITEMS: assume next week's promotions match last week's (planned promos would replace this)
    hol["DATE"] = pd.to_datetime(hol["DATE"])
    in_week = hol[(hol["DATE"] >= next_week) & (hol["DATE"] < next_week + timedelta(days=7))]
    future["HOLIDAY_DAYS"] = future["STORE_NBR"].map(in_week.groupby("STORE_NBR").size()).fillna(0)

    return pd.concat([pdf, future], ignore_index=True)[INPUT_COLS]


def main():
    from snowflake.connector.pandas_tools import write_pandas
    from src.ingest.load_raw import get_connection

    pdf = load_weekly_with_future()
    spark = get_spark()
    try:
        features = build_features(spark.createDataFrame(pdf)).toPandas()
    finally:
        spark.stop()
    features["WEEK_START"] = pd.to_datetime(features["WEEK_START"]).dt.date

    conn = get_connection()
    try:
        ok, _, n, _ = write_pandas(conn, features, table_name="FEATURES",
                                   database="SALES_DB", schema="ANALYTICS",
                                   auto_create_table=True, overwrite=True,
                                   quote_identifiers=False)
    finally:
        conn.close()
    print(f"FEATURES {'OK' if ok else 'FAILED'} {n:,} rows")
    if not ok:
        raise RuntimeError("Writing FEATURES failed")


if __name__ == "__main__":
    main()
