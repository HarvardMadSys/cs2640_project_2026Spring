"""
SQL agent task definitions for the BIRD benchmark.

Each task is a natural-language question over a BIRD database. The agent
calls sql_exec to inspect schema, then refines to a heavy aggregation query.
The final query (the slow turn) is expected to take >5s in DuckDB due to
large table scans and multi-table JOINs.

BIRD databases are stored as DuckDB files under data/bird/. Run
benchmarks/sql/download_bird.py to build them from the BIRD SQLite sources.
"""

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIRD_DIR = os.path.join(_ROOT, "data", "bird")


def _t(task_id, db_name, prompt):
    return {
        "id": task_id,
        "agent_type": "sql",
        "benchmark": "bird",
        "db_path": os.path.join(_BIRD_DIR, f"{db_name}.duckdb"),
        "prompt": prompt,
    }


_FINANCIAL_SCHEMA = """Schema (exact column names):
  account(account_id, district_id, frequency, date)
  client(client_id, gender, birth_date, district_id)
  disp(disp_id, client_id, account_id, type)
  loan(loan_id, account_id, date, amount, duration, payments, status)
  trans(trans_id, account_id, date, type, operation, amount, balance, k_symbol, bank, account)
  district(district_id, A2, A3, A4, A5, A6, A7, A8, A9, A10, A11, A12, A13, A14, A15, A16)
Note: the table is named "trans" not "transaction". Join path: loan->account->disp->client."""

TASKS = [
    # --- financial (loan, account, transaction, client tables, ~1M rows) ---
    _t("fin_01", "financial", f"""Database: financial
{_FINANCIAL_SCHEMA}

Question: What is the average loan amount for clients whose average monthly
transaction amount in the 12 months before the loan was issued exceeds 5000?
Break down by district (use district.A2 for name) and sort by average loan amount descending."""),

    _t("fin_02", "financial", f"""Database: financial
{_FINANCIAL_SCHEMA}

Question: Identify the top 10 accounts by total outgoing transaction volume
(trans.type='VYDAJ') in 1997. For each, report the account owner's district,
the number of transactions, and the total amount sent."""),

    _t("fin_03", "financial", f"""Database: financial
{_FINANCIAL_SCHEMA}

Question: Among loans issued in 1996-1997 (loan.date like '199%'), compute the
default rate (loan.status='D' or 'B') grouped by client age bracket (under 30,
30-45, 45-60, over 60, derived from client.birth_date) and district. Which
segment has the highest default rate?"""),

    _t("fin_04", "financial", f"""Database: financial
{_FINANCIAL_SCHEMA}

Question: For each month from 1993-01 to 1998-12, compute the ratio of loan
issuances to new account openings. Identify the 3 months with the highest and
lowest ratios. Does the ratio correlate with district urbanization (district.A10)?"""),

    # --- easy financial sanity checks (1-3 turn answers expected) ---
    _t("fin_easy_01", "financial", f"""Database: financial
{_FINANCIAL_SCHEMA}

Question: How many loans are in the loan table?"""),

    _t("fin_easy_02", "financial", f"""Database: financial
{_FINANCIAL_SCHEMA}

Question: What is the average loan amount across all loans?"""),

    _t("fin_easy_03", "financial", f"""Database: financial
{_FINANCIAL_SCHEMA}

Question: How many clients are female and how many are male? Report both counts."""),

    _t("fin_easy_04", "financial", f"""Database: financial
{_FINANCIAL_SCHEMA}

Question: List the distinct loan statuses and the number of loans with each status."""),

    _t("fin_easy_05", "financial", f"""Database: financial
{_FINANCIAL_SCHEMA}

Question: What is the maximum transaction amount in the trans table, and on what date did it occur?"""),

    # --- california_schools ---
    _t("cal_01", "california_schools", """Database: california_schools
Tables: schools, satscores, frpm (free/reduced price meal eligibility).

Question: For each county, compute the average SAT math score for schools
where more than 50% of students qualify for free/reduced price meals.
Compare this against the county average for schools with <20% eligibility.
Report the gap and rank counties by the size of the achievement gap."""),

    _t("cal_02", "california_schools", """Database: california_schools
Tables: schools, satscores, frpm.

Question: Identify charter schools (schools.Charter = 1) that outperform
non-charter schools in the same county on average SAT reading scores.
For these counties, report the number of charter schools, the average
reading score difference, and the frpm eligibility rate for both groups."""),

    # --- european_football_2 (match events: millions of rows) ---
    _t("euro_01", "european_football_2", """Database: european_football_2
Tables: Match (~25000 rows), Player, Player_Attributes (~180000 rows),
Team, Team_Attributes (~1500 rows), League, Country.

Question: Which teams showed the greatest improvement in overall rating
(from Team_Attributes) between the 2010-2011 and 2015-2016 seasons?
For the top 5 improving teams, report their home win rate, average goals
scored per match, and the league they compete in."""),

    _t("euro_02", "european_football_2", """Database: european_football_2
Tables: Match, Player, Player_Attributes (~180000 rows), Team, League.

Question: Identify players who played in at least 3 different leagues and
had an overall rating above 80 in Player_Attributes in 2014. For each such
player, report their name, the leagues they played in, and whether their
rating increased or decreased across leagues."""),
]

TASKS_BY_ID = {t["id"]: t for t in TASKS}
