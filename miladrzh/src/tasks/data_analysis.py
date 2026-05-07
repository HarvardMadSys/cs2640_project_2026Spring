"""
Data analysis task definitions. Each task is a dict consumed by agent/loop.py.
DATA_DIR is injected at runtime from the DATA_DIR env var or default path.
"""

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(_ROOT, "data"))


def _t(task_id, prompt):
    return {
        "id": task_id,
        "agent_type": "data_analysis",
        "benchmark": "custom_data",
        "data_dir": DATA_DIR,
        "prompt": prompt.format(DATA_DIR=DATA_DIR),
    }


TASKS = [
    # NYC Taxi
    _t("taxi_01", """You have NYC yellow taxi trip records for 2022 and 2023 stored as
parquet files in {DATA_DIR}/nyc_taxi/. Files are named yellow_tripdata_YYYY-MM.parquet
(e.g. yellow_tripdata_2022-01.parquet through yellow_tripdata_2023-12.parquet).
DATA_DIR and pandas (as pd) are already available in your namespace.

Load all files with pd.read_parquet and predict hourly trip count by borough. Engineer
relevant time and location features, train a model of your choice, and report the top 3
most important features and the RMSE on a held-out month."""),

    _t("taxi_02", """You have NYC yellow taxi trip records for 2022 and 2023 in {DATA_DIR}/nyc_taxi/.
Files are named yellow_tripdata_YYYY-MM.parquet. DATA_DIR and pandas (as pd) are already
available in your namespace.

Investigate surge pricing patterns. Model the fare-per-mile ratio as a function of hour
of day, day of week, and pickup borough. Identify the top 3 time-location combinations
with the highest systematic surges and quantify the effect size."""),

    _t("taxi_03", """You have NYC yellow taxi trip records for 2022 and 2023 in {DATA_DIR}/nyc_taxi/.
Files are named yellow_tripdata_YYYY-MM.parquet. DATA_DIR and pandas (as pd) are already
available in your namespace.

Detect anomalous trip durations. For each pickup zone, flag trips whose duration exceeds
3 standard deviations from the zone mean. Report the top 10 zones by anomaly rate."""),

    _t("taxi_04", """You have NYC yellow taxi trip records for 2022 and 2023 in {DATA_DIR}/nyc_taxi/.
Files are named yellow_tripdata_YYYY-MM.parquet. DATA_DIR and pandas (as pd) are already
available in your namespace.

Analyze tipping behavior. Compute tip percentage (tip_amount / fare_amount) by payment
type, hour of day, and pickup borough. Which combination predicts the highest tips?"""),

    # --- easy taxi sanity checks (1-2 turn answers expected) ---
    _t("taxi_easy_01", """The file {DATA_DIR}/nyc_taxi/yellow_tripdata_2022-01.parquet contains
NYC yellow taxi trip records. DATA_DIR and pandas (as pd) are already available.

Question: How many rows are in this file?"""),

    _t("taxi_easy_02", """The file {DATA_DIR}/nyc_taxi/yellow_tripdata_2022-01.parquet contains
NYC yellow taxi trip records. DATA_DIR and pandas (as pd) are already available.

Question: List the column names and dtypes of this file."""),

    _t("taxi_easy_03", """The file {DATA_DIR}/nyc_taxi/yellow_tripdata_2022-01.parquet contains
NYC yellow taxi trip records with a numeric column named `fare_amount`. DATA_DIR and
pandas (as pd) are already available.

Question: What is the average value of `fare_amount` in this file?"""),

    _t("taxi_easy_04", """The file {DATA_DIR}/nyc_taxi/yellow_tripdata_2022-01.parquet contains
NYC yellow taxi trip records with an integer column `payment_type`. DATA_DIR and
pandas (as pd) are already available.

Question: What is the distribution of `payment_type` (count of rows for each distinct value)?"""),

    _t("taxi_easy_05", """The file {DATA_DIR}/nyc_taxi/yellow_tripdata_2022-01.parquet contains
NYC yellow taxi trip records with a datetime column `tpep_pickup_datetime`. DATA_DIR
and pandas (as pd) are already available.

Question: What is the minimum and maximum value of `tpep_pickup_datetime` in this file?"""),

    # NOAA
    _t("noaa_01", """You have NOAA GHCN-Daily weather observations for 2015-2023 in
{DATA_DIR}/noaa/ as gzipped CSV files named YYYY.csv.gz. Columns: ID, DATE, ELEMENT,
DATA_VALUE, M_FLAG, Q_FLAG, S_FLAG, OBS_TIME.

For US stations only (ID starts with 'US'), compute the annual mean TMAX per station
and fit a linear trend over 2015-2023. Report the top 20 stations with the steepest
warming trend."""),

    _t("noaa_02", """Using the NOAA data in {DATA_DIR}/noaa/, define an extreme heat day as
one where TMAX exceeds a station's historical 95th percentile. For US stations, compute
the frequency of extreme heat days per year (2015-2023). Identify stations where this
frequency has significantly increased and group them by US climate region."""),

    _t("noaa_03", """Using the NOAA data in {DATA_DIR}/noaa/ for 2020-2023, investigate the
relationship between total annual precipitation (PRCP) and mean summer temperature
(TMAX, June-August) across US stations. Cluster stations into 5 groups by long-run
climate averages and report whether wetter clusters are warming faster."""),

    # SEC EDGAR
    _t("sec_01", """You have SEC EDGAR quarterly company filing index files in
{DATA_DIR}/sec_edgar/ for 2019-2023 (files named YYYY_QTRn_company.gz).
The files are pipe-delimited with columns: CIK, Company Name, Form Type, Date Filed, Filename.

Compute filing volume by form type (10-K, 10-Q, 8-K, S-1) and year. Identify the
COVID-19 impact: which form types dropped most in Q1-Q2 2020 and which recovered
fastest by 2022?"""),

    _t("sec_02", """Using the SEC EDGAR index files in {DATA_DIR}/sec_edgar/, identify
companies with anomalous filing frequency. Compute filings per company per year and flag
companies whose annual count exceeds 3 standard deviations above their own historical
mean. What form types drive these spikes?"""),

    _t("sec_03", """Using the SEC EDGAR index files in {DATA_DIR}/sec_edgar/, track
first-time filers by year from 2019-2023 as a proxy for new market entrants. Did the
rate drop in 2020? Compare S-1 first-timers vs other form types."""),

    # Census ACS
    _t("census_01", """You have US Census ACS 1-Year PUMS person-level data for 2022 in
{DATA_DIR}/census/ as zip files: csv_pca.zip (California), csv_ptx.zip (Texas),
csv_pny.zip (New York), csv_pfl.zip (Florida), csv_ppa.zip (Pennsylvania).
Key columns: WAGP (wage income), SCHL (education attainment code), OCCP (occupation),
JWMNP (commute minutes), GRPIP (gross rent as pct of income), AGEP (age), RAC1P (race).

Load all five states and compute the Gini coefficient for wage income broken down by
state and education level (less than HS, HS diploma, bachelor's, graduate degree).
Which state-education combination has the highest income inequality?"""),

    _t("census_02", """Using the Census PUMS data in {DATA_DIR}/census/ (all 5 states),
train a regression model to predict individual commute time (JWMNP) from occupation
code, wage income, age, education level, and state. Report the top feature importances
and R-squared. Does predictive power differ across states?"""),

    _t("census_03", """Using the Census PUMS data in {DATA_DIR}/census/, identify
housing-cost-burdened households (GRPIP > 30, meaning rent exceeds 30% of income).
Compute the burden rate by age group (under 30, 30-50, 50-65, 65+), race, and state.
Which demographic group faces the highest burden in each state?"""),

    _t("census_04", """Using the Census PUMS data in {DATA_DIR}/census/, build an
occupation-education mismatch matrix: for each education level, what fraction of workers
hold jobs whose median wage is below the median for their education group? Report the
top 10 most common mismatch patterns across all 5 states."""),
]

TASKS_BY_ID = {t["id"]: t for t in TASKS}
