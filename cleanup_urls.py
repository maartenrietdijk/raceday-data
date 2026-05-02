#!/usr/bin/env python3
"""Remove all resultsUrl fields from JSON files."""
import json
from pathlib import Path

for json_file in Path(".").glob("*_2026.json"):
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    changed = False
    for round_data in data:
        for session in round_data.get("sessions", []):
            if "resultsUrl" in session:
                del session["resultsUrl"]
                changed = True
    
    if changed:
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ Cleaned {json_file}")

print("Done")
