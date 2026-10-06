"""LLM assistant: answers forecast questions with read-only SQL or retrieved summaries."""
import json
import os
import re
import snowflake.connector
from openai import OpenAI
from dotenv import load_dotenv
from src.rag.index import search

load_dotenv()
CHAT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

ALLOWED_TABLES = {
    "SALES_DB.ANALYTICS.FORECASTS", "SALES_DB.ANALYTICS.FEATURES",
    "SALES_DB.ANALYTICS.ACCURACY_LOG", "SALES_DB.ANALYTICS.MODEL_RESULTS",
    "SALES_DB.ANALYTICS.STORE_SUMMARIES", "SALES_DB.STAGING.STORES",
    "SALES_DB.STAGING.SALES_WEEKLY",
}

SCHEMA = """
SALES_DB.ANALYTICS.FORECASTS(STORE_NBR, FAMILY, WEEK_START, FORECAST, MODEL, RUN_TS)
  -- next-week forecasts, one row per store x family per pipeline run. ALWAYS filter to the latest run:
  -- WHERE RUN_TS = (SELECT MAX(RUN_TS) FROM SALES_DB.ANALYTICS.FORECASTS)
SALES_DB.ANALYTICS.FEATURES(STORE_NBR, FAMILY, WEEK_START, STORE_TYPE, STORE_CLUSTER, SALES, PROMO_ITEMS,
  HOLIDAY_DAYS, AVG_OIL_PRICE, STORE_TRANSACTIONS, IS_FUTURE, SALES_LAG_1, SALES_LAG_2, SALES_LAG_4,
  SALES_LAG_52, SALES_ROLL_MEAN_4, SALES_ROLL_MEAN_12, SALES_ROLL_STD_12, WEEK_OF_YEAR, MONTH, YEAR)
  -- weekly history. IS_FUTURE = 1 is the forecast week (SALES is NaN there); use IS_FUTURE = 0 for actual sales.
  -- For the forecast week, SALES_LAG_1 = last week's actual sales, SALES_LAG_52 = same week last year.
  -- Lag/rolling columns can be NaN in early weeks.
SALES_DB.ANALYTICS.ACCURACY_LOG(RUN_TS, MODEL, EVAL_WEEK, WAPE, N_SERIES, THRESHOLD, PASSED)
  -- one row per pipeline quality check; WAPE is a fraction (0.0999 = 9.99%).
SALES_DB.ANALYTICS.MODEL_RESULTS(MODEL, SCOPE, N, WAPE, RMSE)
  -- backtest comparison of naive_52, arima_fourier, prophet, xgboost_global; SCOPE is 'all' or 'top10'.
SALES_DB.ANALYTICS.STORE_SUMMARIES(STORE_NBR, WEEK_START, SUMMARY)
SALES_DB.STAGING.STORES(STORE_NBR, CITY, STATE, STORE_TYPE, STORE_CLUSTER)
SALES_DB.STAGING.SALES_WEEKLY(STORE_NBR, FAMILY, WEEK_START, CITY, STATE, STORE_TYPE, STORE_CLUSTER,
  SALES, PROMO_ITEMS, HOLIDAY_DAYS, AVG_OIL_PRICE, STORE_TRANSACTIONS, DAYS_IN_WEEK)
"""

SYSTEM = f"""You are a sales forecasting assistant for a grocery chain in Ecuador
(54 stores, 33 product families). Answer ONLY from tool results; never invent numbers.
- Use run_sql for numbers, rankings, totals and comparisons. Use fully qualified table names.
- Use search_summaries for "why" or narrative questions about a store's or the chain's forecast.
- Percentage change for a store, family or the chain = (SUM(new) - SUM(old)) / SUM(old) computed on totals, never an average of row-level percentages. For 'vs last week' on the forecast week, old = SUM(SALES_LAG_1) from FEATURES joined on STORE_NBR, FAMILY, WEEK_START.
- Be concise: lead with the answer, include store numbers and figures, and say which data you used.
Tables:
{SCHEMA}"""

TOOLS = [
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Run ONE read-only Snowflake SELECT query and return up to 100 rows.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "search_summaries",
        "description": "Retrieve the most relevant written forecast summaries (per store or chain-wide).",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}},
                       "required": ["question"]}}},
]

BLOCKED = r"\b(insert|update|delete|merge|drop|create|alter|truncate|grant|revoke|copy|put|call|use)\b"


def _ro_connection():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
        private_key_file=os.path.expanduser(os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]),
        role="ANALYST_RO", warehouse="SALES_WH", database="SALES_DB")


def run_sql(query):
    q = query.strip().rstrip(";")
    if ";" in q:
        return {"error": "Only one statement is allowed."}
    if not re.match(r"(?is)^\s*(select|with)\b", q) or re.search(BLOCKED, q, re.I):
        return {"error": "Only read-only SELECT queries are allowed."}
    ctes = {c.upper() for c in re.findall(r"(?i)\b(\w+)\s+as\s*\(", q)}
    for t in re.findall(r"(?i)\b(?:from|join)\s+([A-Za-z0-9_.\"]+)", q):
        name = t.replace('"', "").upper()
        if name not in ALLOWED_TABLES and name not in ctes:
            return {"error": f"Table {t} is not allowed. Use fully qualified names from the schema."}
    if not re.search(r"(?i)\blimit\s+\d+\s*$", q):
        q += " LIMIT 100"
    conn = _ro_connection()
    try:
        cur = conn.cursor()
        cur.execute(q)
        df = cur.fetch_pandas_all()
        return {"rows": json.loads(df.head(100).to_json(orient="records", date_format="iso"))}
    except Exception as e:
        return {"error": str(e)[:500]}
    finally:
        conn.close()


def ask(question, verbose=True, max_steps=6):
    client = OpenAI()
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    for _ in range(max_steps):
        msg = client.chat.completions.create(model=CHAT_MODEL, messages=messages,
                                             tools=TOOLS, temperature=0).choices[0].message
        if not msg.tool_calls:
            return msg.content
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result = run_sql(**args) if tc.function.name == "run_sql" else search(args["question"])
            if verbose:
                print(f"  [{tc.function.name}] {list(args.values())[0][:200]}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, default=str)[:12000]})
    return "Sorry, I couldn't complete that within the step limit."
