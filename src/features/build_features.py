"""Build forecasting features with PySpark from weekly sales."""
from pyspark.sql import SparkSession, functions as F, Window


def get_spark():
    return (SparkSession.builder
            .appName("sales-features")
            .master("local[*]")
            .config("spark.driver.memory", "4g")
            .config("spark.sql.execution.arrow.pyspark.enabled", "true")
            .getOrCreate())


def build_features(sdf):
    """Add lag, rolling, and calendar features per store x family series.

    Rolling windows use only PAST weeks (rows -N to -1), so the model
    never sees the week it is predicting - this prevents data leakage.
    """
    w = Window.partitionBy("STORE_NBR", "FAMILY").orderBy("WEEK_START")

    for k in [1, 2, 4, 52]:
        sdf = sdf.withColumn(f"SALES_LAG_{k}", F.lag("SALES", k).over(w))

    sdf = (sdf
           .withColumn("SALES_ROLL_MEAN_4",  F.avg("SALES").over(w.rowsBetween(-4, -1)))
           .withColumn("SALES_ROLL_MEAN_12", F.avg("SALES").over(w.rowsBetween(-12, -1)))
           .withColumn("SALES_ROLL_STD_12",  F.stddev("SALES").over(w.rowsBetween(-12, -1)))
           .withColumn("WEEK_OF_YEAR", F.weekofyear("WEEK_START"))
           .withColumn("MONTH", F.month("WEEK_START"))
           .withColumn("YEAR", F.year("WEEK_START")))
    return sdf
