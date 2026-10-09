"""CLI entry points of query_data.py and orm_queries.py, run against seeded test-DB rows."""

import os
import runpy
import subprocess
import sys

import pytest
from conftest import make_record

import orm_queries
from config import db_env

pytestmark = pytest.mark.analysis

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
# The scripts import load_data / sql_utils from src/db, which is found on the path, not
# next to the script: the subprocess gets both import roots, as a deployment would.
SCRIPT_PYTHONPATH = os.pathsep.join([SRC_DIR, os.path.join(SRC_DIR, "db")])


def _row(i, term, nationality, status, degree, university, gpa, gre="165", llm_university=None):
    return make_record(
        i,
        **{
            "Semester and Year": term,
            "International/American": nationality,
            "Applicant Status": status,
            "Masters or PhD": degree,
            "University": university,
            "Program Name": "Computer Science",
            "GPA": gpa,
            "GRE Score": gre,
            "llm-generated-program": "Computer Science",
            "llm-generated-university": llm_university or university,
        },
    )


# Hand-checked expectations for these 7 rows:
#   Q1  Fall 2026 rows: 0, 1, 2, 6                                   -> 4
#   Q2  International: rows 1, 4 of 7                                -> 2/7 = 28.57%
#   Q4  American + Fall 2026 GPAs: 3.50, 3.70, 3.80                  -> 3.67 (n=3)
#   Q5  Fall 2025: rows 3, 4, 5; accepted: row 3                     -> 1/3 = 33.33%
#   Q7  Johns Hopkins + Masters + CS: row 2                          -> 1
#   Q8  Fall 2026 + Accepted + PhD + CS at the 4 schools: 0, 1, 6    -> 3
#   Q9  same via LLM fields; row 6's "Carnegie Melon" misses         -> 2
#   Custom 1  GRE outside 130-170: row 2 (900) of 7                  -> 14.29%
QUERY_SEED = [
    _row(0, "Fall 2026", "American", "Accepted", "PhD", "Stanford University", "3.50"),
    _row(1, "Fall 2026", "International", "Accepted", "PhD", "Massachusetts Institute of Technology", "3.90"),
    _row(2, "Fall 2026", "American", "Rejected", "Masters", "Johns Hopkins University", "3.70", gre="900"),
    _row(3, "Fall 2025", "American", "Accepted", "Masters", "Test University 3", "3.20"),
    _row(4, "Fall 2025", "International", "Rejected", "PhD", "Test University 4", None),
    _row(5, "Fall 2025", "American", "Wait listed", "PhD", "Test University 5", "3.60"),
    _row(6, "Fall 2026", "American", "Accepted", "PhD", "Carnegie Mellon University", "3.80",
         llm_university="Carnegie Melon University"),
]

ORM_MAIN_OUTPUT = """\
ORM vs. raw-SQL (query_data.py) comparison, both computed live in this run:

Q1         Fall 2026 applicant count                     ORM=4          raw-SQL=4          [MATCH]
Q4         Avg GPA, American, Fall 2026                  ORM=3.67       raw-SQL=3.67       [MATCH]
Q5         Fall 2025 acceptance %                        ORM=33.33      raw-SQL=33.33      [MATCH]
Q8         Fall 2026/Accepted/PhD/CS, original fields    ORM=3          raw-SQL=3          [MATCH]
Q9         Same as Q8, llm-generated fields              ORM=2          raw-SQL=2          [MATCH]
Custom Q1  GRE Quant contamination %                     ORM=14.29      raw-SQL=14.29      [MATCH]

Q4 n=3 (raw-SQL n=3)
Q5 1/3 (raw-SQL 1/3)
Custom Q1 1/7 (raw-SQL 1/7)

All ORM results match the raw-SQL results from query_data.py.
"""

QUERY_DATA_MAIN_OUTPUT = """\
Fall 2026 applicant count: 4

Percent international: 28.57% (2/7 entries with a usable classification)

Average scores (each computed over entries that provide that metric):
  Average GPA: 3.62 (n=6)
  Average GRE: 165.00 (n=6)
  Average GRE V: 160.00 (n=7)
  Average GRE AW: 4.50 (n=7)

Average GPA, American applicants, Fall 2026: 3.67 (n=3)

Fall 2025 acceptance percentage: 33.33% (1/3 entries)

Average GPA, accepted applicants, Fall 2026: 3.73 (n=3)

Q7 (JHU, Masters, Computer Science, all-time): 1

Q8 (Fall 2026, Accepted, PhD, Computer Science, original fields): 3
Q9 (same filters, llm_generated_program/university):             2
Difference (Q8 - Q9):                                            1

Per-university breakdown under Q8/Q9 filters (original vs. LLM-generated fields):
  Georgetown       original=0    llm=0    diff=0
  MIT              original=1    llm=1    diff=0
  Stanford         original=1    llm=1    diff=0
  Carnegie Mellon  original=1    llm=0    diff=1

Custom Q1 (GRE Quant contamination rate): 14.29% (1/7 entries outside the plausible 130-170 range)

Custom Q2 (acceptance rate by degree type):
  PhD        60.00% (3/5)
  Masters    50.00% (1/2)
"""


@pytest.fixture
def seeded(seed):
    assert seed(QUERY_SEED) == (len(QUERY_SEED), 0, [])


def test_query_data_main_output(seeded, capsys):
    runpy.run_path(os.path.join(SRC_DIR, "query_data.py"), run_name="__main__")
    assert capsys.readouterr().out == QUERY_DATA_MAIN_OUTPUT


def test_orm_queries_main_output(seeded, capsys):
    runpy.run_path(os.path.join(SRC_DIR, "orm_queries.py"), run_name="__main__")
    assert capsys.readouterr().out == ORM_MAIN_OUTPUT


def test_format_comparison_all_match():
    table, verdict = orm_queries.format_comparison([
        ("Q1", "Fall 2026 applicant count", 4, 4, "{}"),
        ("Q4", "Avg GPA, American, Fall 2026", 3.67, 3.67, "{:.2f}"),
    ])
    assert [line.endswith("[MATCH]") for line in table] == [True, True]
    assert "ORM=3.67" in table[1] and "raw-SQL=3.67" in table[1]
    assert verdict == "All ORM results match the raw-SQL results from query_data.py."


def test_format_comparison_reports_mismatch():
    table, verdict = orm_queries.format_comparison([
        ("Q1", "Fall 2026 applicant count", 4, 4, "{}"),
        ("Q8", "Fall 2026/Accepted/PhD/CS, original fields", 3, 2, "{}"),
    ])
    assert table[0].endswith("[MATCH]")
    assert table[1].endswith("[MISMATCH]")
    assert "ORM=3" in table[1] and "raw-SQL=2" in table[1]
    assert verdict.startswith("MISMATCH DETECTED")


def test_query_data_main_on_empty_table(capsys):
    runpy.run_path(os.path.join(SRC_DIR, "query_data.py"), run_name="__main__")

    out = capsys.readouterr().out
    assert "Fall 2026 applicant count: 0\n" in out
    assert "Percent international: N/A (no data) (0/0 entries with a usable classification)\n" in out
    assert "  Average GPA: N/A (no data) (n=0)\n" in out
    assert "Average GPA, American applicants, Fall 2026: N/A (no data) (n=0)\n" in out
    assert "Fall 2025 acceptance percentage: N/A (no data) (0/0 entries)\n" in out
    assert "Custom Q1 (GRE Quant contamination rate): N/A (no data) (0/0 entries" in out
    assert out.endswith("Custom Q2 (acceptance rate by degree type):\n  N/A (no data)\n")


def test_orm_queries_main_on_empty_table(capsys):
    runpy.run_path(os.path.join(SRC_DIR, "orm_queries.py"), run_name="__main__")

    out = capsys.readouterr().out
    assert "ORM=0          raw-SQL=0          [MATCH]" in out
    assert "Avg GPA, American, Fall 2026                  ORM=N/A (no data) raw-SQL=N/A (no data) [MATCH]" in out
    assert "Q5 0/0 (raw-SQL 0/0)\n" in out
    assert out.endswith("All ORM results match the raw-SQL results from query_data.py.\n")


@pytest.mark.parametrize("script", ["query_data.py", "orm_queries.py"])
def test_cli_exits_cleanly_on_empty_table(script, test_database_url):
    result = subprocess.run(
        [sys.executable, os.path.join(SRC_DIR, script)],
        env={**os.environ, **db_env(test_database_url), "PYTHONPATH": SCRIPT_PYTHONPATH},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert "Traceback" not in result.stdout
    assert "N/A (no data)" in result.stdout
