# Snyk findings

## Snyk Open Source

`snyk test --file=requirements.txt --package-manager=pip --command=venv/bin/python`
(from `module_5/`) tested 64 dependencies and found no known issues; nothing
is ignored. Full output: `snyk_test_output.txt`.

## Snyk Code

`snyk code test src` and `snyk code test tests` (from `module_5/`) report 11
findings. Full output: `snyk_code_output.txt`. All 11 were reviewed and are
false positives; they are left open (no ignores, no code changes).

| # | Rule | Location | Severity | Verdict |
|---|---|---|---|---|
| 1 | SQL Injection (`python/Sqli`) | `src/flask_app.py:348` (into `src/applicant_search.py:99`) | High | False positive |
| 2-7 | Path Traversal (`python/PT`) | `src/flask_app.py:226, 281, 287, 304, 308, 314` | Low | False positive |
| 8 | Path Traversal (`python/PT`) | `src/pull_data.py:179` | Low | False positive |
| 9-11 | Use of Hardcoded Passwords (`python/NoHardcodedPasswords/test`) | `tests/test_config.py:18, 60, 134` | Low | False positive |

### SQL Injection

Snyk traces the `GET /api/applicants` query parameters into `cur.execute()`,
but no user input ever becomes SQL text. `build_applicants_query()` accepts
`sort` only if it is a key of `SORT_COLUMNS`, and then uses the fixed column
name that key maps to as an `sql.Identifier`; `order` must be `asc` or
`desc`, which map to fixed `sql.SQL` fragments; `limit` is clamped to an
integer and `university` is LIKE-escaped, and both are passed as bound `%s`
parameters. Any other value raises `ValidationError` (HTTP 400) before a
query is built. Snyk does not treat the allowlist lookup as a sanitizer, so
it reports the flow anyway.

### Path Traversal

All seven traces start at `os.environ.get("PULL_RESULT_FILE")`
(`src/pull_data.py:157`) and end at `os.replace()` in `write_pull_result()`,
which writes the Pull Data result file. That variable is set only by whoever
starts the server, or by the app itself when it launches the Pull Data child
process (`src/flask_app.py:213`); no HTTP request can reach it. Anyone who
can set the server's environment can already run code as the app, so the
path is not attacker-controlled.

### Use of Hardcoded Passwords

The three values (`"s3cret"` in `FULL_SETTINGS` and the expected DSN, and
`SPECIAL_PASSWORD`) are made-up fixtures in unit tests of the configuration
parser. They never authenticate to any system; they check that a password
is read from `DB_PASSWORD` and escaped correctly. Snyk's own rule ID marks
these as test findings.
