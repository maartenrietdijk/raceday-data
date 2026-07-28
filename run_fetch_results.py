#!/usr/bin/env python3
"""Compatibility entry point for the existing RaceDay results fetcher.

The original parser keeps all of its behaviour. This small entry point only
registers every calendar JSON present in the repository, so newly added series
(such as WSBK) do not fail solely because fetch_results.py has a hard-coded map.
"""

import importlib
from pathlib import Path


def register_calendar_files(series_json: dict[str, str], root: Path = Path(".")) -> None:
    for path in root.glob("*_20??.json"):
        if "_standings_" in path.name:
            continue
        series = path.name.rsplit("_", 1)[0]
        series_json.setdefault(series, path.name)


if __name__ == "__main__":
    fetch_results = importlib.import_module("fetch_results")
    register_calendar_files(fetch_results.SERIES_JSON)
    fetch_results.main()
