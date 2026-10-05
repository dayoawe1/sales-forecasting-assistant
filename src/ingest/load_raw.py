"""Load the Kaggle Store Sales CSVs into Snowflake SALES_DB.RAW."""
import os
import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = "data/raw"
FILES = {
    "train.csv": "TRAIN",
    "test.csv": "TEST",
    "stores.csv": "STORES",
    "oil.csv": "OIL",
    "holidays_events.csv": "HOLIDAYS_EVENTS",
    "transactions.csv": "TRANSACTIONS",
}


def get_connection():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key_file=os.path.expanduser(os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]),
        role="SYSADMIN",
        warehouse="SALES_WH",
        database="SALES_DB",
        schema="RAW",
    )


def main():
    conn = get_connection()
    try:
        for filename, table in FILES.items():
            df = pd.read_csv(os.path.join(DATA_DIR, filename), dtype=str)
            df.columns = [c.upper() for c in df.columns]
            success, _, nrows, _ = write_pandas(
                conn, df, table_name=table,
                auto_create_table=True, overwrite=True, quote_identifiers=False,
            )
            print(f"{table:<16} {'OK' if success else 'FAILED'}  {nrows:>10,} rows")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
