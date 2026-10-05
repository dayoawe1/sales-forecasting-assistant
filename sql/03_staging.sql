USE ROLE SYSADMIN;
USE WAREHOUSE SALES_WH;
USE DATABASE SALES_DB;

-------------------------------------------------------------------
-- 1. Daily sales: convert text to real dates and numbers
-------------------------------------------------------------------
CREATE OR REPLACE TABLE STAGING.SALES_DAILY AS
SELECT
    TRY_TO_DATE(DATE)              AS DATE,
    TRY_TO_NUMBER(STORE_NBR)       AS STORE_NBR,
    FAMILY,
    TRY_TO_DOUBLE(SALES)           AS SALES,
    TRY_TO_NUMBER(ONPROMOTION)     AS ONPROMOTION
FROM RAW.TRAIN;

-------------------------------------------------------------------
-- 2. Stores: location and store attributes
-------------------------------------------------------------------
CREATE OR REPLACE TABLE STAGING.STORES AS
SELECT
    TRY_TO_NUMBER(STORE_NBR) AS STORE_NBR,
    CITY,
    STATE,
    TYPE                     AS STORE_TYPE,
    TRY_TO_NUMBER(CLUSTER)   AS STORE_CLUSTER
FROM RAW.STORES;

-------------------------------------------------------------------
-- 3. Oil prices: fill missing days (weekends/holidays) with the
--    most recent known price, using a full calendar of dates
-------------------------------------------------------------------
CREATE OR REPLACE TABLE STAGING.OIL_DAILY AS
WITH calendar AS (
    SELECT DATEADD(DAY, SEQ4(), (SELECT MIN(DATE) FROM STAGING.SALES_DAILY)) AS DATE
    FROM TABLE(GENERATOR(ROWCOUNT => 2000))
),
oil AS (
    SELECT TRY_TO_DATE(DATE) AS DATE, TRY_TO_DOUBLE(DCOILWTICO) AS OIL_PRICE
    FROM RAW.OIL
)
SELECT
    c.DATE,
    LAST_VALUE(o.OIL_PRICE IGNORE NULLS) OVER (
        ORDER BY c.DATE ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS OIL_PRICE
FROM calendar c
LEFT JOIN oil o ON c.DATE = o.DATE
WHERE c.DATE <= (SELECT MAX(DATE) FROM STAGING.SALES_DAILY);

-------------------------------------------------------------------
-- 4. Holidays per store: national holidays apply to every store,
--    regional ones to stores in that state, local ones to that city.
--    Transferred holidays were not celebrated that day; Work Days
--    are extra working days, so both are excluded.
-------------------------------------------------------------------
CREATE OR REPLACE TABLE STAGING.STORE_HOLIDAYS AS
WITH h AS (
    SELECT TRY_TO_DATE(DATE) AS DATE, LOCALE, LOCALE_NAME
    FROM RAW.HOLIDAYS_EVENTS
    WHERE UPPER(TRANSFERRED) <> 'TRUE'
      AND TYPE <> 'Work Day'
)
SELECT DISTINCT s.STORE_NBR, h.DATE, 1 AS IS_HOLIDAY
FROM h
JOIN STAGING.STORES s
  ON  h.LOCALE = 'National'
  OR (h.LOCALE = 'Regional' AND h.LOCALE_NAME = s.STATE)
  OR (h.LOCALE = 'Local'    AND h.LOCALE_NAME = s.CITY);

-------------------------------------------------------------------
-- 5. Daily store traffic (number of transactions)
-------------------------------------------------------------------
CREATE OR REPLACE TABLE STAGING.TRANSACTIONS_DAILY AS
SELECT
    TRY_TO_DATE(DATE)            AS DATE,
    TRY_TO_NUMBER(STORE_NBR)     AS STORE_NBR,
    TRY_TO_NUMBER(TRANSACTIONS)  AS TRANSACTIONS
FROM RAW.TRANSACTIONS;

-------------------------------------------------------------------
-- 6. One enriched daily table: sales + store + oil + holiday + traffic
-------------------------------------------------------------------
CREATE OR REPLACE TABLE STAGING.SALES_DAILY_ENRICHED AS
SELECT
    d.DATE, d.STORE_NBR, d.FAMILY, d.SALES, d.ONPROMOTION,
    s.CITY, s.STATE, s.STORE_TYPE, s.STORE_CLUSTER,
    o.OIL_PRICE,
    COALESCE(h.IS_HOLIDAY, 0) AS IS_HOLIDAY,
    t.TRANSACTIONS
FROM STAGING.SALES_DAILY d
LEFT JOIN STAGING.STORES s              ON d.STORE_NBR = s.STORE_NBR
LEFT JOIN STAGING.OIL_DAILY o           ON d.DATE = o.DATE
LEFT JOIN STAGING.STORE_HOLIDAYS h      ON d.STORE_NBR = h.STORE_NBR AND d.DATE = h.DATE
LEFT JOIN STAGING.TRANSACTIONS_DAILY t  ON d.STORE_NBR = t.STORE_NBR AND d.DATE = t.DATE;

-------------------------------------------------------------------
-- 7. Weekly table for forecasting: store x product family x week
-------------------------------------------------------------------
CREATE OR REPLACE TABLE STAGING.SALES_WEEKLY AS
SELECT
    STORE_NBR,
    FAMILY,
    DATE_TRUNC('WEEK', DATE)  AS WEEK_START,
    ANY_VALUE(CITY)           AS CITY,
    ANY_VALUE(STATE)          AS STATE,
    ANY_VALUE(STORE_TYPE)     AS STORE_TYPE,
    ANY_VALUE(STORE_CLUSTER)  AS STORE_CLUSTER,
    SUM(SALES)                AS SALES,
    SUM(ONPROMOTION)          AS PROMO_ITEMS,
    SUM(IS_HOLIDAY)           AS HOLIDAY_DAYS,
    AVG(OIL_PRICE)            AS AVG_OIL_PRICE,
    SUM(TRANSACTIONS)         AS STORE_TRANSACTIONS,
    COUNT(*)                  AS DAYS_IN_WEEK   -- under 7 = partial week (first/last)
FROM STAGING.SALES_DAILY_ENRICHED
GROUP BY STORE_NBR, FAMILY, DATE_TRUNC('WEEK', DATE);


-- Total sales should match between daily and weekly (nothing lost)
SELECT
  (SELECT ROUND(SUM(SALES)) FROM STAGING.SALES_DAILY)  AS daily_total,
  (SELECT ROUND(SUM(SALES)) FROM STAGING.SALES_WEEKLY) AS weekly_total;

-- Any dates that failed to convert? Should be 0
SELECT COUNT(*) FROM STAGING.SALES_DAILY WHERE DATE IS NULL;

-- Oil price should have no gaps after the first few days
SELECT COUNT(*) FROM STAGING.OIL_DAILY WHERE OIL_PRICE IS NULL;

-- Preview the final table
SELECT * FROM STAGING.SALES_WEEKLY ORDER BY STORE_NBR, FAMILY, WEEK_START LIMIT 20;