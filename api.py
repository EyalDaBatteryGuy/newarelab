from pathlib import Path
import struct

import pandas as pd
import pymysql
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

NEWARE_ROOT = Path(r"C:\Program Files (x86)\NEWARE\BTSServer80")
NDC_ROOT = NEWARE_ROOT / "NdcFile"


def get_conn():
    return pymysql.connect(
        host="127.0.0.1",
        port=3306,
        user="neware_reader",
        password="NewareRead123!",
        database="bts63",
        cursorclass=pymysql.cursors.DictCursor,
    )


def read_ndc(path: Path, limit: int = 5000):
    data = path.read_bytes()
    pattern = bytes.fromhex("ea07")  # 2026 little endian

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

        timestamp = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

        seq = struct.unpack("<I", rec[20:24])[0]
        voltage_v = struct.unpack("<i", rec[48:52])[0] / 1000.0

        if voltage_v < -0.1 or voltage_v > 5.0:
            continue

        current_a = struct.unpack("<i", rec[47:51])[0] / 1_000_000.0
        capacity_raw = struct.unpack("<I", rec[56:60])[0]

        rows.append({
            "time": timestamp,
            "seq": seq,
            "voltage": voltage_v,
            "current": current_a,
            "capacity": capacity_raw / 1000.0,
            "energy": None,
        })

    return rows


def get_test_meta(test_id: int):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT test_id, dev_uid, unit_id, chl_id, ndc_path, start_time, end_time
        FROM test
        WHERE test_id = %s
        ORDER BY rec_num DESC
        LIMIT 1
    """, (test_id,))

    row = cur.fetchone()
    cur.close()
    conn.close()

    return row


def get_ndc_file(test_id: int):
    meta = get_test_meta(test_id)

    if not meta:
        return None

    ndc_path = meta.get("ndc_path")
    if not ndc_path:
        return None

    rel = ndc_path.replace("/", "\\")

    candidates = [
        NEWARE_ROOT / rel,
        Path(str(NEWARE_ROOT / rel) + ".ndc"),
        Path(str(NEWARE_ROOT / rel) + ".NDC"),
        NDC_ROOT / rel,
        Path(str(NDC_ROOT / rel) + ".ndc"),
        Path(str(NDC_ROOT / rel) + ".NDC"),
    ]

    for p in candidates:
        if p.exists():
            print(f"FOUND NDC: {p}")
            return p

    print("NDC NOT FOUND. Tried:")
    for p in candidates:
        print(p)

    return None


@app.get("/")
def root():
    return {"status": "ok", "source": "neware_mysql_ndc"}


@app.get("/dashboard")
def dashboard():
    return FileResponse("dashboard.html")


@app.get("/tests")
def tests():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            test_id,
            dev_uid,
            unit_id,
            chl_id,
            rec_num,
            start_time,
            end_time,
            creator
        FROM test
        WHERE ndc_path IS NOT NULL
        ORDER BY start_time DESC
        LIMIT 200
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "id": r["test_id"],
            "test_name": f"Dev {r['dev_uid']} | Unit {r['unit_id']} | Ch {r['chl_id']}",
            "cell_name": f"Dev {r['dev_uid']} | Unit {r['unit_id']} | Ch {r['chl_id']}",
            "cycler_number": r["dev_uid"],
            "module_number": r["unit_id"],
            "channel_number": r["chl_id"],
            "records": r["rec_num"],
            "start_time": str(r["start_time"]),
            "end_time": str(r["end_time"]) if r["end_time"] else None,
        }
        for r in rows
    ]


@app.get("/tests/search")
def search_tests(
    cell_name: str | None = None,
    cycler: str | None = None,
    cycler_number: str | None = None,
    dev_uid: str | None = None,
    module: int | None = None,
    channel: int | None = None,
):
    raw_cycler = cycler or cycler_number or dev_uid

    cycler_full = None
    if raw_cycler:
        cycler_num = int(raw_cycler)
        cycler_full = cycler_num + 240000 if cycler_num < 100000 else cycler_num

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT test_id, dev_uid, unit_id, chl_id, rec_num, start_time, end_time
        FROM test
        WHERE ndc_path IS NOT NULL
          AND (%s IS NULL OR dev_uid = %s)
          AND (%s IS NULL OR unit_id = %s)
          AND (%s IS NULL OR chl_id = %s)
        ORDER BY start_time DESC
        LIMIT 100
    """, (cycler_full, cycler_full, module, module, channel, channel))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "id": r["test_id"],
            "test_name": f"Dev {r['dev_uid']} | Unit {r['unit_id']} | Ch {r['chl_id']}",
            "cell_name": f"Dev {r['dev_uid']} | Unit {r['unit_id']} | Ch {r['chl_id']}",
            "cycler_number": r["dev_uid"],
            "module_number": r["unit_id"],
            "channel_number": r["chl_id"],
            "records": r["rec_num"],
            "start_time": str(r["start_time"]),
            "end_time": str(r["end_time"]) if r["end_time"] else None,
        }
        for r in rows
    ]


@app.get("/test/{test_id}/plot")
def test_plot(test_id: int, limit: int = 5000):
    ndc_file = get_ndc_file(test_id)

    if not ndc_file or not ndc_file.exists():
        return {
            "time": [],
            "voltage": [],
            "current": [],
            "capacity": [],
            "energy": [],
            "error": f"NDC file not found: {ndc_file}",
        }

    rows = read_ndc(ndc_file, limit=limit)

    return {
        "time": [r["time"] for r in rows],
        "voltage": [r["voltage"] for r in rows],
        "current": [r["current"] for r in rows],
        "capacity": [r["capacity"] for r in rows],
        "energy": [r["energy"] for r in rows],
    }


@app.get("/test/{test_id}/cycles")
def test_cycles(test_id: int):
    ndc_file = get_ndc_file(test_id)

    if not ndc_file or not ndc_file.exists():
        return []

    rows = read_ndc(ndc_file, limit=300000)

    if not rows:
        return []

    df = pd.DataFrame(rows)

    # מזהה מחזור חדש רק כשיש איפוס משמעותי של קיבול
    df["cycle_raw"] = (df["capacity"].diff() < -5).cumsum() + 1

    out = []
    cycle_num = 1

    for _, g in df.groupby("cycle_raw"):
        max_cap = float(g["capacity"].max())

        # מסנן מחזורים חלקיים/רעש/נפילות קטנות
        if max_cap < 27:
            continue

        out.append({
            "cycle": cycle_num,
            "charge_capacity": max_cap,
            "discharge_capacity": max_cap,
        })

        cycle_num += 1

    return out

@app.get("/test/{test_id}/voltage_fade")
def voltage_fade(test_id: int):
    ndc_file = get_ndc_file(test_id)

    if not ndc_file or not ndc_file.exists():
        return {"cycle": [], "avg_voltage": []}

    rows = read_ndc(ndc_file, limit=300000)

    if not rows:
        return {"cycle": [], "avg_voltage": []}

    df = pd.DataFrame(rows)
    df["cycle"] = (df["capacity"].diff() < -0.01).cumsum() + 1

    out_cycle = []
    out_voltage = []

    for cycle, g in df.groupby("cycle"):
        out_cycle.append(int(cycle))
        out_voltage.append(float(g["voltage"].mean()))

    return {
        "cycle": out_cycle,
        "avg_voltage": out_voltage,
    }


@app.get("/test/{test_id}/virtual_cycle/{cycle_number}")
def virtual_cycle(test_id: int, cycle_number: int):
    ndc_file = get_ndc_file(test_id)

    if not ndc_file or not ndc_file.exists():
        return {"cycle": cycle_number, "charge": [], "discharge": []}

    rows = read_ndc(ndc_file, limit=300000)

    if not rows:
        return {"cycle": cycle_number, "charge": [], "discharge": []}

    df = pd.DataFrame(rows)
    df["cycle"] = (df["capacity"].diff() < -0.01).cumsum() + 1

    g = df[df["cycle"] == cycle_number]

    points = [
        {
            "time": r["time"],
            "voltage": r["voltage"],
            "current": r["current"],
            "capacity": r["capacity"],
        }
        for _, r in g.iterrows()
    ]

    return {
        "cycle": cycle_number,
        "charge": points,
        "discharge": [],
    }