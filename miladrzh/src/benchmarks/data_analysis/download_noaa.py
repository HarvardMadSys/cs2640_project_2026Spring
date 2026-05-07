"""
Download NOAA GHCN-Daily by-year CSV files (2015-2023).
~9 gzipped CSV files, ~150MB each compressed, ~1.3GB compressed total.
Columns: station_id, date, element (TMAX/TMIN/PRCP/SNOW/...), value, flags.

Run: python benchmarks/data_analysis/download_noaa.py
Override data dir: DATA_DIR=/path/to/data python ...
"""

import os
import sys
import requests

BASE_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_year"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(_ROOT, "data", "noaa"))
YEARS = range(2015, 2024)


def download_file(url, dest):
    if os.path.exists(dest):
        print(f"  skip {os.path.basename(dest)} (exists)")
        return
    print(f"  downloading {os.path.basename(dest)} ...", end="", flush=True)
    r = requests.get(url, stream=True, timeout=120)
    r.raise_for_status()
    total = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
            total += len(chunk)
            print(f"\r  downloading {os.path.basename(dest)} ... {total >> 20} MB", end="", flush=True)
    print(f"\r  {os.path.basename(dest)}: {total >> 20} MB")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"Data dir: {DATA_DIR}\n")
    for year in YEARS:
        filename = f"{year}.csv.gz"
        url = f"{BASE_URL}/{filename}"
        dest = os.path.join(DATA_DIR, filename)
        try:
            download_file(url, dest)
        except Exception as e:
            print(f"  FAILED {filename}: {e}", file=sys.stderr)
    print("\nDone.")


if __name__ == "__main__":
    main()
