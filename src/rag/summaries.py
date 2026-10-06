"""Turn the latest forecasts into plain-English summaries (documents for RAG)."""
import pandas as pd
from snowflake.connector.pandas_tools import write_pandas
from src.ingest.load_raw import get_connection

QUERY = """
WITH latest AS (
  SELECT * FROM SALES_DB.ANALYTICS.FORECASTS
  WHERE RUN_TS = (SELECT MAX(RUN_TS) FROM SALES_DB.ANALYTICS.FORECASTS)
)
SELECT f.STORE_NBR, f.FAMILY, f.WEEK_START, f.FORECAST,
       x.SALES_LAG_1 AS LAST_WEEK, x.SALES_LAG_52 AS SAME_WEEK_LAST_YEAR,
       x.SALES_ROLL_MEAN_4 AS AVG_4WK, x.PROMO_ITEMS, x.HOLIDAY_DAYS,
       s.CITY, s.STATE, s.STORE_TYPE
FROM latest f
JOIN SALES_DB.ANALYTICS.FEATURES x
  ON x.STORE_NBR = f.STORE_NBR AND x.FAMILY = f.FAMILY AND x.WEEK_START = f.WEEK_START
JOIN SALES_DB.STAGING.STORES s ON s.STORE_NBR = f.STORE_NBR
"""


def _pct(new, old):
    return None if not old or pd.isna(old) else (new - old) / old * 100


def _fmt(p):
    if p is None or pd.isna(p):
        return "n/a"
    return f"{'up' if p >= 0 else 'down'} {abs(p):.1f}%"


def _family_moves(g, n=3):
    d = g.assign(CHANGE=g["FORECAST"] - g["AVG_4WK"]).dropna(subset=["CHANGE"])
    line = lambda r: f"{r.FAMILY} ({r.FORECAST:,.0f} vs {r.AVG_4WK:,.0f}, {_fmt(_pct(r.FORECAST, r.AVG_4WK))})"
    up = "; ".join(line(r) for r in d.nlargest(n, "CHANGE").itertuples())
    down = "; ".join(line(r) for r in d.nsmallest(n, "CHANGE").itertuples())
    return up, down


def build_summaries():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(QUERY)
        df = cur.fetch_pandas_all()
    finally:
        conn.close()

    num = ["FORECAST", "LAST_WEEK", "SAME_WEEK_LAST_YEAR", "AVG_4WK", "PROMO_ITEMS", "HOLIDAY_DAYS"]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    df["HOLIDAY_DAYS"] = df["HOLIDAY_DAYS"].fillna(0)
    week = str(pd.to_datetime(df["WEEK_START"]).max().date())

    docs = []
    for store, g in df.groupby("STORE_NBR"):
        fc, lw = g["FORECAST"].sum(), g["LAST_WEEK"].sum()
        ly, a4 = g["SAME_WEEK_LAST_YEAR"].sum(), g["AVG_4WK"].sum()
        up, down = _family_moves(g)
        first = g.iloc[0]
        text = (f"Store {int(store)} ({first['CITY']}, {first['STATE']}; store type {first['STORE_TYPE']}) - "
                f"forecast for the week of {week}. "
                f"Total forecast sales: {fc:,.0f}, {_fmt(_pct(fc, lw))} vs last week ({lw:,.0f}), "
                f"{_fmt(_pct(fc, a4))} vs its 4-week average ({a4:,.0f}), and "
                f"{_fmt(_pct(fc, ly))} vs the same week last year ({ly:,.0f}). "
                f"Largest expected increases vs the 4-week average: {up}. "
                f"Largest expected decreases: {down}. "
                f"Product families with items on promotion last week: {int((g['PROMO_ITEMS'] > 0).sum())} of {len(g)}. "
                f"Holiday days at this store during the forecast week: {int(g['HOLIDAY_DAYS'].max())}.")
        docs.append({"STORE_NBR": int(store), "WEEK_START": week, "SUMMARY": text})

    # one chain-wide document
    t = df.groupby("STORE_NBR").agg(FC=("FORECAST", "sum"), LW=("LAST_WEEK", "sum"))
    t["PCT"] = (t["FC"] - t["LW"]) / t["LW"] * 100
    ups = ", ".join(f"store {s} ({_fmt(p)})" for s, p in t["PCT"].nlargest(5).items())
    downs = ", ".join(f"store {s} ({_fmt(p)})" for s, p in t["PCT"].nsmallest(5).items())
    total, total_lw = df["FORECAST"].sum(), df["LAST_WEEK"].sum()
    docs.append({"STORE_NBR": 0, "WEEK_START": week, "SUMMARY":
                 f"Chain-wide forecast for the week of {week}: total forecast sales {total:,.0f} across "
                 f"{df['STORE_NBR'].nunique()} stores and {df['FAMILY'].nunique()} product families, "
                 f"{_fmt(_pct(total, total_lw))} vs last week ({total_lw:,.0f}). "
                 f"Stores with the largest expected increase vs last week: {ups}. "
                 f"Stores with the largest expected decrease vs last week: {downs}."})

    out = pd.DataFrame(docs)
    conn = get_connection()
    try:
        ok, _, n, _ = write_pandas(conn, out, table_name="STORE_SUMMARIES", database="SALES_DB",
                                   schema="ANALYTICS", auto_create_table=True,
                                   overwrite=True, quote_identifiers=False)
    finally:
        conn.close()
    print(f"STORE_SUMMARIES {'OK' if ok else 'FAILED'} {n} rows")
    return out
