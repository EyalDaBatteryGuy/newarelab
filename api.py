from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import psycopg2
import os

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/tests")
def tests():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT t.id, t.test_name, e.name
        FROM tests t
        LEFT JOIN experiments e ON t.experiment_id = e.id
        ORDER BY t.id
    """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "id": r[0],
            "test_name": r[1],
            "experiment": r[2]
        }
        for r in rows
    ]


@app.get("/test/{test_id}/cycles")
def test_cycles(test_id: int):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT cycle_index,
               charge_capacity_ah,
               discharge_capacity_ah
        FROM cycles
        WHERE test_id = %s
        ORDER BY cycle_index
    """, (test_id,))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "cycle": r[0],
            "charge_capacity": r[1],
            "discharge_capacity": r[2]
        }
        for r in rows
    ]


@app.get("/test/{test_id}/plot")
def test_plot(test_id: int, limit: int = 5000):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT time,
               voltage_v,
               current_a,
               capacity_ah,
               energy_wh
        FROM records_plain
        WHERE test_id = %s
        ORDER BY time
        LIMIT %s
    """, (test_id, limit))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return {
        "time": [str(r[0]) for r in rows],
        "voltage": [r[1] for r in rows],
        "current": [r[2] for r in rows],
        "capacity": [r[3] for r in rows],
        "energy": [r[4] for r in rows],
    }


def build_segments(test_id: int):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT time,
               voltage_v,
               current_a,
               capacity_ah
        FROM records_plain
        WHERE test_id = %s
        ORDER BY time
    """, (test_id,))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    segments = []
    current_segment = []

    last_capacity = None
    last_sign = None

    for time, voltage, current, capacity in rows:

        if current is None or capacity is None:
            continue

        sign = 1 if current > 0 else -1 if current < 0 else 0

        new_segment = False

        if last_capacity is not None and capacity < last_capacity:
            new_segment = True

        if (
            last_sign is not None
            and sign != 0
            and last_sign != 0
            and sign != last_sign
        ):
            new_segment = True

        if new_segment and current_segment:
            segments.append(current_segment)
            current_segment = []

        current_segment.append({
            "time": str(time),
            "voltage": voltage,
            "current": current,
            "capacity": capacity
        })

        last_capacity = capacity

        if sign != 0:
            last_sign = sign

    if current_segment:
        segments.append(current_segment)

    return segments


@app.get("/test/{test_id}/virtual_cycle/{cycle_number}")
def virtual_cycle(test_id: int, cycle_number: int):

    segments = build_segments(test_id)

    cycle_index = cycle_number - 1

    charge_i = cycle_index * 2
    discharge_i = charge_i + 1

    return {
        "cycle": cycle_number,
        "charge": segments[charge_i] if charge_i < len(segments) else [],
        "discharge": segments[discharge_i] if discharge_i < len(segments) else []
    }


@app.get("/test/{test_id}/voltage_fade")
def voltage_fade(test_id: int):

    segments = build_segments(test_id)

    cycles = []
    avg_voltage = []

    cycle_number = 1

    for i in range(1, len(segments), 2):

        discharge = segments[i]

        voltages = [
            p["voltage"]
            for p in discharge
            if p["voltage"] is not None
        ]

        if voltages:
            cycles.append(cycle_number)
            avg_voltage.append(sum(voltages) / len(voltages))

        cycle_number += 1

    return {
        "cycle": cycles,
        "avg_voltage": avg_voltage
    }


@app.get("/dashboard")
def dashboard():
    return FileResponse("/app/dashboard.html")

@app.get("/tests/search")
def search_tests(
    cell_name: str | None = None,
    cycler: int | None = None,
    module: int | None = None,
    channel: int | None = None,
):
    conn = get_conn()
    cur = conn.cursor()

    if cell_name:
        cur.execute("""
            SELECT id, test_name, cell_name, cycler_number, module_number, channel_number
            FROM tests
            WHERE LOWER(cell_name) LIKE LOWER(%s)
            LIMIT 20
        """, (f"%{cell_name}%",))

    else:
        cur.execute("""
            SELECT id, test_name, cell_name, cycler_number, module_number, channel_number
            FROM tests
            WHERE cycler_number = %s
              AND module_number = %s
              AND channel_number = %s
            LIMIT 20
        """, (cycler, module, channel))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "id": r[0],
            "test_name": r[1],
            "cell_name": r[2],
            "cycler_number": r[3],
            "module_number": r[4],
            "channel_number": r[5],
        }
        for r in rows
    ]

@app.get("/tests")
def get_tests():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, test_name
        FROM tests
        ORDER BY id DESC
    """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "id": r[0],
            "test_name": r[1]
        }
        for r in rows
    ]