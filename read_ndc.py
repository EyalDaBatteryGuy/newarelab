from pathlib import Path
import struct
import pandas as pd


def read_ndc(path):
    path = Path(path)
    data = path.read_bytes()

    pattern = bytes.fromhex("ea0705")  # timestamp pattern: 2026-05
    hits = []
    pos = 0

    while True:
        idx = data.find(pattern, pos)
        if idx == -1:
            break

        if idx + 87 <= len(data):
            hits.append(idx)

        pos = idx + 1

    rows = []

    for h in hits:
        rec = data[h:h + 87]

        year = struct.unpack("<H", rec[0:2])[0]
        month = rec[2]
        day = rec[3]
        hour = rec[4]
        minute = rec[5]
        second = rec[6]

        if not (2020 <= year <= 2035 and 1 <= month <= 12 and 1 <= day <= 31):
            continue

        timestamp = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

        seq = struct.unpack("<I", rec[20:24])[0]
        voltage_v = struct.unpack("<i", rec[48:52])[0] / 1000.0

        if voltage_v < -0.1 or voltage_v > 5.0:
            continue

        capacity_raw = struct.unpack("<I", rec[56:60])[0]

        rows.append({
            "time": timestamp,
            "seq": seq,
            "voltage_v": voltage_v,
            "capacity_raw": capacity_raw,
            "capacity_ah": capacity_raw / 1000.0,
        })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    p = r"C:\Program Files (x86)\NEWARE\BTSServer80\NdcFile\20260506\20260506_150403_240122_1_2_2818575792.ndc"
    df = read_ndc(p)

    print(df.head())
    print(df.tail())
    print("rows:", len(df))

    if not df.empty:
        print("voltage min/max:", df["voltage_v"].min(), df["voltage_v"].max())
        print("capacity_raw min/max:", df["capacity_raw"].min(), df["capacity_raw"].max())
        print("capacity_ah min/max:", df["capacity_ah"].min(), df["capacity_ah"].max())