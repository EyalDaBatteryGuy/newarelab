import pandas as pd
from pathlib import Path
import psycopg2

ROOT = Path("/data")

conn = psycopg2.connect(
    host="neware_postgres",
    dbname="neware_lab",
    user="neware",
    password="neware123",
)

cur = conn.cursor()


def get_or_create_project(project_name):
    cur.execute("""
        INSERT INTO projects (name)
        VALUES (%s)
        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id;
    """, (project_name,))
    return cur.fetchone()[0]


def get_or_create_experiment(project_id, experiment_name, folder_path):
    cur.execute("""
        INSERT INTO experiments (project_id, name, folder_path)
        VALUES (%s, %s, %s)
        ON CONFLICT (folder_path) DO UPDATE
        SET name = EXCLUDED.name,
            project_id = EXCLUDED.project_id
        RETURNING id;
    """, (project_id, experiment_name, folder_path))
    return cur.fetchone()[0]


def create_test(experiment_id, test_name):
    cur.execute("""
        INSERT INTO tests (experiment_id, test_name)
        VALUES (%s, %s)
        RETURNING id;
    """, (experiment_id, test_name))
    return cur.fetchone()[0]


def safe_float(value):
    if pd.isna(value):
        return None
    try:
        return float(value)
    except Exception:
        return None


def find_col(df, keywords):
    for col in df.columns:
        c = str(col).lower()
        if all(k.lower() in c for k in keywords):
            return col
    return None


def import_cycles(xlsx_file, test_id):
    cycle_df = pd.read_excel(xlsx_file, sheet_name="cycle")

    count = 0

    for _, row in cycle_df.iterrows():
        try:
            cycle_index = int(row.iloc[0])
        except Exception:
            continue

        charge_cap = safe_float(row.iloc[1])
        discharge_cap = safe_float(row.iloc[2])
        eff = safe_float(row.iloc[3])

        cur.execute("""
            INSERT INTO cycles
            (
                test_id,
                cycle_index,
                charge_capacity_ah,
                discharge_capacity_ah,
                efficiency_percent
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (
            test_id,
            cycle_index,
            charge_cap,
            discharge_cap,
            eff
        ))

        count += 1

    return count


def import_records(xlsx_file, test_id):
    record_df = pd.read_excel(xlsx_file, sheet_name="record")

    time_col = find_col(record_df, ["date"])
    if time_col is None:
        time_col = find_col(record_df, ["time"])

    voltage_col = find_col(record_df, ["voltage"])
    current_col = find_col(record_df, ["current"])
    capacity_col = find_col(record_df, ["capacity"])
    energy_col = find_col(record_df, ["energy"])

    cycle_col = find_col(record_df, ["cycle"])
    step_col = find_col(record_df, ["step"])

    if time_col is None or voltage_col is None or current_col is None:
        print("Missing required record columns in:", xlsx_file)
        print("Columns:", list(record_df.columns))
        return 0

    rows = []

    for _, row in record_df.iterrows():
        timestamp = row[time_col]

        if pd.isna(timestamp):
            continue

        cycle_index = None
        if cycle_col is not None and pd.notna(row[cycle_col]):
            try:
                cycle_index = int(row[cycle_col])
            except Exception:
                cycle_index = None

        step_index = None
        if step_col is not None and pd.notna(row[step_col]):
            try:
                step_index = int(row[step_col])
            except Exception:
                step_index = None

        rows.append((
            timestamp,
            test_id,
            cycle_index,
            step_index,
            safe_float(row[voltage_col]),
            safe_float(row[current_col]),
            safe_float(row[capacity_col]) if capacity_col is not None else None,
            safe_float(row[energy_col]) if energy_col is not None else None,
        ))

    if not rows:
        return 0

    cur.executemany("""
        INSERT INTO records
        (
            time,
            test_id,
            cycle_index,
            step_index,
            voltage_v,
            current_a,
            capacity_ah,
            energy_wh
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, rows)

    return len(rows)


def main():
    imported_files = 0
    imported_cycles = 0
    imported_records = 0

    for xlsx_file in ROOT.rglob("*.xlsx"):
        try:
            relative = xlsx_file.relative_to(ROOT)
            parts = relative.parts

            if len(parts) < 3:
                continue

            project_name = parts[0]
            experiment_name = parts[1]
            experiment_folder = str(ROOT / project_name / experiment_name)

            project_id = get_or_create_project(project_name)
            experiment_id = get_or_create_experiment(
                project_id,
                experiment_name,
                experiment_folder
            )

            test_id = create_test(experiment_id, xlsx_file.stem)

            print("Processing:", xlsx_file)

            c_count = import_cycles(xlsx_file, test_id)
            r_count = import_records(xlsx_file, test_id)

            conn.commit()

            imported_files += 1
            imported_cycles += c_count
            imported_records += r_count

            print("Imported cycles:", c_count)
            print("Imported records:", r_count)

        except Exception as e:
            conn.rollback()
            print("ERROR:", xlsx_file, e)

    print("Done.")
    print("Imported files:", imported_files)
    print("Imported cycles:", imported_cycles)
    print("Imported records:", imported_records)


if __name__ == "__main__":
    main()
    cur.close()
    conn.close()