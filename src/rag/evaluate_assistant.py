"""Score the assistant against SQL-computed ground truth."""
import re
from src.ingest.load_raw import get_connection
from src.rag.assistant import ask

LATEST = "(SELECT MAX(RUN_TS) FROM SALES_DB.ANALYTICS.FORECASTS)"
CASES = [
    ("Which 5 stores have the highest total forecast for next week?",
     f"SELECT STORE_NBR FROM SALES_DB.ANALYTICS.FORECASTS WHERE RUN_TS = {LATEST} "
     "GROUP BY STORE_NBR ORDER BY SUM(FORECAST) DESC LIMIT 5", "ints"),
    ("Which product family has the largest total forecast next week?",
     f"SELECT FAMILY FROM SALES_DB.ANALYTICS.FORECASTS WHERE RUN_TS = {LATEST} "
     "GROUP BY FAMILY ORDER BY SUM(FORECAST) DESC LIMIT 1", "text"),
    ("What was the WAPE in the most recent model accuracy check?",
     "SELECT WAPE FROM SALES_DB.ANALYTICS.ACCURACY_LOG ORDER BY RUN_TS DESC LIMIT 1", "number"),
    ("Which model had the lowest WAPE on the top 10 series in the backtest?",
     "SELECT MODEL FROM SALES_DB.ANALYTICS.MODEL_RESULTS WHERE SCOPE = 'top10' ORDER BY WAPE LIMIT 1", "text"),
    ("How many store and product family combinations have a forecast for next week?",
     f"SELECT COUNT(*) FROM SALES_DB.ANALYTICS.FORECASTS WHERE RUN_TS = {LATEST}", "number"),
    ("Which city has the most stores?",
     "SELECT CITY FROM SALES_DB.STAGING.STORES GROUP BY CITY ORDER BY COUNT(*) DESC LIMIT 1", "text"),
    ("What is the total forecast across all stores for next week?",
     f"SELECT SUM(FORECAST) FROM SALES_DB.ANALYTICS.FORECASTS WHERE RUN_TS = {LATEST}", "number"),
    ("Which 3 stores have the biggest forecasted percentage decline versus last week?",
     "SELECT f.STORE_NBR FROM SALES_DB.ANALYTICS.FORECASTS f JOIN SALES_DB.ANALYTICS.FEATURES x "
     "ON x.STORE_NBR = f.STORE_NBR AND x.FAMILY = f.FAMILY AND x.WEEK_START = f.WEEK_START "
     f"WHERE f.RUN_TS = {LATEST} GROUP BY f.STORE_NBR "
     "ORDER BY (SUM(f.FORECAST) - SUM(x.SALES_LAG_1)) / NULLIF(SUM(x.SALES_LAG_1), 0) ASC LIMIT 3", "ints"),
    ("Did the latest model run pass the quality gate?",
     "SELECT PASSED FROM SALES_DB.ANALYTICS.ACCURACY_LOG ORDER BY RUN_TS DESC LIMIT 1", "yesno"),
]


def _numbers(text):
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))]


def _check(kind, truth, answer):
    a = answer.lower()
    if kind == "ints":
        return {int(v) for v in truth} <= {int(n) for n in _numbers(answer) if n.is_integer()}
    if kind == "text":
        return str(truth[0]).split("_")[0].lower() in a
    if kind == "number":
        v = float(truth[0])
        return any(abs(n - c) <= 0.015 * abs(c) for n in _numbers(answer)
                   for c in (v, v * 100, v / 1e3, v / 1e6) if c)
    if kind == "yesno":
        return ("pass" in a or "yes" in a) if truth[0] else ("fail" in a or "no" in a)


def main():
    conn = get_connection()
    cur = conn.cursor()
    passed = 0
    for question, truth_sql, kind in CASES:
        cur.execute(truth_sql)
        truth = [r[0] for r in cur.fetchall()]
        answer = ask(question, verbose=False)
        ok = _check(kind, truth, answer)
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'} | {question}\n       truth: {truth}\n       answer: {answer[:250]}\n")
    conn.close()
    print(f"Assistant accuracy: {passed}/{len(CASES)} = {passed / len(CASES):.0%}")
    return passed / len(CASES)
