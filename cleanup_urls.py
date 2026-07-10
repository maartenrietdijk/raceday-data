#!/usr/bin/env python3
"""Remove all resultsUrl fields from calendar JSON files."""
import json
from pathlib import Path

for json_file in Path(".").glob("*_2026.json"):
    if "_standings_" in json_file.name:
        print(f"Skipping standings file {json_file}")
        continue

    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print(f"Skipping non-calendar JSON {json_file}")
        continue

    changed = False

    for round_data in data:
        if not isinstance(round_data, dict):
            continue

        sessions = round_data.get("sessions", [])
        if not isinstance(sessions, list):
            continue

        for session in sessions:
            if not isinstance(session, dict):
                continue

            if "resultsUrl" in session:
                del session["resultsUrl"]
                changed = True

    if changed:
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"✅ Cleaned {json_file}")

print("Done")
