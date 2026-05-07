"""
Download NYC TLC yellow taxi trip records (2022-2023).
~24 parquet files, ~100MB each, ~2.5GB total, ~70M rows total.

Run: python benchmarks/data_analysis/download_nyc_taxi.py
Override data dir: DATA_DIR=/path/to/data python ...
"""

import os
import sys
import requests

BASE_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(_ROOT, "data", "nyc_taxi"))
YEARS = [2022, 2023]


def download_file(url, dest):
    if os.path.exists(dest):
        print(f"  skip {os.path.basename(dest)} (exists)")
        return
    print(f"  downloading {os.path.basename(dest)} ...", end="", flush=True)
    r = requests.get(url, stream=True, timeout=60)
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
        for month in range(1, 13):
            filename = f"yellow_tripdata_{year}-{month:02d}.parquet"
            url = f"{BASE_URL}/{filename}"
            dest = os.path.join(DATA_DIR, filename)
            try:
                download_file(url, dest)
            except Exception as e:
                print(f"  FAILED {filename}: {e}", file=sys.stderr)
    print("\nDone.")


if __name__ == "__main__":
    main()
