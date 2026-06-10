from pathlib import Path
import os
import struct
import time

import pandas as pd
import pymysql
import psycopg2
from psycopg2.extras import execute_values


MYSQL_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "neware_reader",
    "password": "NewareRead123!",
    "database": "bts63",
    "cursorclass": pymysql.cursors.DictCursor,
}

DATABASE_URL = os.getenv("DATABASE_URL")

NEWARE_ROOT = Path(r"C:\Program Files (x86)\NEWARE\BTSServer80")
SCAN_EVERY_SECONDS = 300  # 5 minutes
MAX_RECORDS_PER_TEST = 300000
MIN_VALID_CAPACITY = 1


def get_mysql_conn():
    return pymysql.connect(**MYSQL_CONFIG)


def get_pg_conn():
    if not DATABASE_URL:
        return psycopg2.connect(
            host="localhost",
            dbname="neware_lab",
            user="neware",
            password="neware123",
        )
    return psycopg2.connect(DATABASE_URL)


def resolve_ndc_path(ndc_path):
    rel = ndc_path.replace("/", "\\")
    candidates = [
        NEWARE_ROOT / rel,
        Path(str(NEWARE_ROOT / rel) + ".ndc"),
        Path(str(NEWARE_ROOT / rel) + ".NDC"),
    ]

    for p in candidates:
        if p.exists():
            return p

    return None


def read_ndc(path: Path, limit=MAX_RECORDS_PER_TEST):
    data = path.read_bytes()
    pattern = bytes.fromhex("ea07")
    hits = []
    pos = 0

    while True:
        idx = data.find(pattern, pos)
        if idx == -1:
            break
        if idx + 87 <= len(data):
            hits.append(idx)
        pos = idx + 1

    if not hits:
        return []

    step = max(1, len(hits) // limit)
    rows = []

    for h in hits[::step]:
        rec = data[h:h + 87]

        year = struct.unpack("<H", rec[0:2])[0]
        month = rec[2]
        day = rec[3]
        hour = rec[4]
        minute = rec[5]
        second = rec[6]

        if not (2020 <= year <= 2035 and 1 <= month <= 12 and 1 <= day <= 31):
            continue
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            continue

        voltage_v = struct.unpack("<i", rec[48:52])[0] / 1000.0
        if voltage_v < -0.1 or voltage_v > 5.0:
            continue

        current_a = struct.unpack("<i", rec[47:51])[0] / 1_000_000.0
        capacity_ah = struct.unpack("<I", rec[56:60])[0] / 1000.0
        step_index = struct.unpack("<I", rec[20:24])[0]
        cycle_index = struct.unpack("<I", rec[24:28])[0]

        if cycle_index <= 0:
            continue

        timestamp = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

        rows.append({
            "time": timestamp,
            "cycle_index": cycle_index,
            "step_index": step_index,
            "voltage_v": voltage_v,
            "current_a": current_a,
            "capacity_ah": capacity_ah,
            "energy_wh": None,
        })

    return rows


def get_active_neware_tests(mysql_cur):
    mysql_cur.execute("""
        SELECT
            test_id,
            dev_uid,
            unit_id,
            chl_id,
            rec_num,
            ndc_path,
            start_time,
            end_time
        FROM test
        WHERE ndc_path IS NOT NULL
          AND end_time IS NULL
        ORDER BY dev_uid, unit_id, chl_id
    """)
    return mysql_cur.fetchall()


def get_or_create_pg_test(pg_cur, r):
    test_name = f"Neware {r['dev_uid']}-{r['unit_id']}-{r['chl_id']} | test_id {r['test_id']}"

    pg_cur.execute("""
        SELECT id
        FROM tests
        WHERE test_name = %s
    """, (test_name,))

    existing = pg_cur.fetchone()

    if existing:
        pg_test_id = existing[0]

        pg_cur.execute("""
            UPDATE tests
            SET
                device_id = %s,
                module_no = %s,
                channel_no = %s,
                start_time = %s,
                end_time = %s
            WHERE id = %s
        """, (
            str(r["dev_uid"]),
            r["unit_id"],
            r["chl_id"],
            r["start_time"],
            r["end_time"],
            pg_test_id,
        ))

        return pg_test_id, test_name

    pg_cur.execute("""
        INSERT INTO tests (
            experiment_id,
            source_file_id,
            test_name,
            device_id,
            module_no,
            channel_no,
            start_time,
            end_time
        )
        VALUES (NULL, NULL, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        test_name,
        str(r["dev_uid"]),
        r["unit_id"],
        r["chl_id"],
        r["start_time"],
        r["end_time"],
    ))

    return pg_cur.fetchone()[0], test_name


def import_one_test(mysql_test, pg_conn):
    pg_cur = pg_conn.cursor()

    pg_test_id, test_name = get_or_create_pg_test(pg_cur, mysql_test)

    ndc_file = resolve_ndc_path(mysql_test["ndc_path"])

    if not ndc_file:
        print("NDC not found:", test_name)
        pg_cur.close()
        return

    print()
    print("Importing:", test_name)
    print("PG test id:", pg_test_id)
    print("NDC:", ndc_file)

    rows = read_ndc(ndc_file)

    if not rows:
        print("No records parsed")
        pg_cur.close()
        return

    df = pd.DataFrame(rows)

    print("Records:", len(df))
    print("Cycles:", int(df["cycle_index"].nunique()))
    print("Cycle max:", int(df["cycle_index"].max()))

    pg_cur.execute("DELETE FROM records_plain WHERE test_id = %s", (pg_test_id,))
    pg_cur.execute("DELETE FROM cycles WHERE test_id = %s", (pg_test_id,))

    record_values = [
        (
            row["time"],
            pg_test_id,
            int(row["cycle_index"]),
            int(row["step_index"]),
            float(row["voltage_v"]),
            float(row["current_a"]),
            float(row["capacity_ah"]),
            None,
        )
        for _, row in df.iterrows()
    ]

    execute_values(
        pg_cur,
        """
        INSERT INTO records_plain (
            time,
            test_id,
            cycle_index,
            step_index,
            voltage_v,
            current_a,
            capacity_ah,
            energy_wh
        )
        VALUES %s
        """,
        record_values,
        page_size=5000,
    )

    cycle_values = []

    for cycle_index, g in df.groupby("cycle_index"):
        max_cap = float(g["capacity_ah"].max())

        if max_cap < MIN_VALID_CAPACITY:
            continue

        cycle_values.append((
            pg_test_id,
            int(cycle_index),
            max_cap,
            max_cap,
            100.0,
        ))

    execute_values(
        pg_cur,
        """
        INSERT INTO cycles (
            test_id,
            cycle_index,
            charge_capacity_ah,
            discharge_capacity_ah,
            efficiency_percent
        )
        VALUES %s
        """,
        cycle_values,
        page_size=5000,
    )

    pg_conn.commit()
    pg_cur.close()

    print("Imported cycles:", len(cycle_values))


def scan_once():
    mysql_conn = get_mysql_conn()
    mysql_cur = mysql_conn.cursor()
    pg_conn = get_pg_conn()

    tests = get_active_neware_tests(mysql_cur)

    print("Active Neware tests:", len(tests))

    for t in tests:
        try:
            import_one_test(t, pg_conn)
        except Exception as e:
            pg_conn.rollback()
            print("ERROR importing:", t)
            print(e)

    mysql_cur.close()
    mysql_conn.close()
    pg_conn.close()


def main():
    print("Neware live importer started")
    print("No Excel. Source = Neware MySQL + NDC")

    while True:
        scan_once()
        print()
        print("Sleeping", SCAN_EVERY_SECONDS, "seconds")
        time.sleep(SCAN_EVERY_SECONDS)


if __name__ == "__main__":
    main()