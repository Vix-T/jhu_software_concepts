## Name

Vix Talbot (JHED: vtalbot1)

## Overview

Module 3 loads cleaned Grad Cafe applicant data (scraped and LLM-standardized
in Module 2) into a PostgreSQL database, analyzes it via both raw SQL
(`query_data.py`) and the SQLAlchemy ORM (`orm_queries.py`), and displays the
results on a Flask webpage (`app.py`) with "Pull Data" (scrape new entries)
and "Update Analysis" (re-run the analysis queries against the current
database) functionality.

## Setup / Run

1. Create a `.env` file in `module_4/` defining `DATABASE_URL`:
   ```
   DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME
   ```
   The password is optional for local trust authentication
   (`postgresql://USER@localhost:5432/DBNAME`). A `DATABASE_URL` already set
   in the environment takes precedence over `.env`.
2. Install dependencies: `pip install -r requirements.txt`
3. Populate the database once: `python src/load_data.py`
4. Start the Flask app: `python src/app.py`
5. Run the tests from the repository root:
   ```
   pytest -c module_4/pytest.ini module_4/tests
   ```

### Pull Data precondition

The "Pull Data" button on the Flask page scrapes new entries from Grad Cafe.
It requires a Chrome browser already running with remote debugging enabled
(`--remote-debugging-port=9222`), with Grad Cafe's Cloudflare challenge
already manually cleared in that session. Pull Data attaches to that
existing, already-verified session — it does not launch a browser or solve
the challenge itself.

---

# Module 3 — Raw SQL vs. SQLAlchemy Comparison (Part 7)

This section compares the raw-SQL and SQLAlchemy (ORM) implementations of **Question 9**: "Repeat Question 8, but identify the university and program using `llm_generated_program`/`llm_generated_university` instead of the original downloaded fields." Q9 was chosen because its regex-based, word-boundary university/program matching required dropping into PostgreSQL's `~*` operator on both sides, making it a concrete case where the two approaches diverge.

## Raw SQL (`query_data.py`)

```python
def q9(cur):
    """Same as Q8, but university/program matched via the LLM-generated fields."""
    university_clause = " OR ".join(
        "llm_generated_university ~* %s" for _ in Q8_Q9_UNIVERSITIES
    )
    params = [pattern for _, pattern in Q8_Q9_UNIVERSITIES]
    cur.execute(
        f"""
        SELECT COUNT(*) FROM applicants
        WHERE term = 'Fall 2026'
          AND status = 'Accepted'
          AND degree = 'PhD'
          AND llm_generated_program ~* %s
          AND ({university_clause})
        """,
        [CS_PATTERN] + params,
    )
    return cur.fetchone()[0]
```

## SQLAlchemy (`orm_queries.py`)

```python
def orm_q9(session):
    """Same as Q8, but university/program matched via the LLM-generated fields."""
    university_clause = or_(
        *[
            Applicant.llm_generated_university.op("~*")(pattern)
            for _, pattern in Q8_Q9_UNIVERSITIES
        ]
    )
    stmt = select(func.count()).where(
        and_(
            Applicant.term == "Fall 2026",
            Applicant.status == "Accepted",
            Applicant.degree == "PhD",
            Applicant.llm_generated_program.op("~*")(CS_PATTERN),
            university_clause,
        )
    )
    return session.scalar(stmt)
```

## Comparison

The SQLAlchemy version's real advantage shows up in the non-regex filters. `Applicant.term == "Fall 2026"`, `Applicant.status == "Accepted"`, and `Applicant.degree == "PhD"` are typed attributes on the `Applicant` model, so a typo'd column name or a type mismatch would be caught before the query ever reaches the database, and the same model would carry over largely unchanged if this project ever moved off PostgreSQL to another SQL backend. The raw-SQL version, by contrast, builds its `WHERE` clause as an f-string with manual `%s` placeholders and a hand-assembled parameter list as the number of dynamic clauses grows. That said, the regex-based university/program matching required falling back to `.op("~*")` on the ORM side anyway. Viewed through that lens the ORM version reads as SQL-with-extra-steps, and arguably the raw-SQL version is more direct and easier to audit at a glance, since the whole filter is visible as one literal query string rather than assembled through `and_()`/`or_()`/`.op()` calls. Overall, for this specific question, the ORM's main benefit was reusing `Q8_Q9_UNIVERSITIES` and `CS_PATTERN` from `query_data.py` unchanged and getting typed-column safety on the simple equality filters, while the regex matching itself was a wash.
