"""Run sql/03_staging.sql in Snowflake to rebuild the STAGING layer."""
from pathlib import Path
from src.ingest.load_raw import get_connection

SQL_FILE = Path(__file__).resolve().parents[2] / "sql" / "03_staging.sql"


def main():
    conn = get_connection()
    try:
        cursors = conn.execute_string(SQL_FILE.read_text())
        print(f"Staging built: {len(cursors)} statements executed")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
