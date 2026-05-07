"""
Download US Census ACS 1-Year PUMS person-level data (2022) for 5 large states.
CA, TX, NY, FL, PA: ~8M person records total, ~400MB compressed.

Run: python benchmarks/data_analysis/download_census.py
Override data dir: DATA_DIR=/path/to/data python ...
"""

import os
import sys
import requests

BASE_URL = "https://www2.census.gov/programs-surveys/acs/data/pums/2022/1-Year"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(_ROOT, "data", "census"))

# 2-letter state codes used by Census PUMS file naming
STATES = {
    "ca": "California",
    "tx": "Texas",
    "ny": "New York",
    "fl": "Florida",
    "pa": "Pennsylvania",
}

HEADERS = {"User-Agent": "research-agent miladrzh@gmail.com"}


def download_file(url, dest):
    if os.path.exists(dest):
        print(f"  skip {os.path.basename(dest)} (exists)")
        return
    print(f"  downloading {os.path.basename(dest)} ...", end="", flush=True)
    r = requests.get(url, stream=True, timeout=180, headers=HEADERS)
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
    for code, name in STATES.items():
        filename = f"csv_p{code}.zip"
        url = f"{BASE_URL}/{filename}"
        dest = os.path.join(DATA_DIR, filename)
        print(f"{name} ({code})")
        try:
            download_file(url, dest)
        except Exception as e:
            print(f"  FAILED {filename}: {e}", file=sys.stderr)
    print("\nDone.")


if __name__ == "__main__":
    main()
