# Module 4 testing rules — non-negotiable
These apply to every task in this folder. If a rule blocks you, STOP and ask; do not work around it.

## Never
- Add `# pragma: no cover` or any coverage exclusion comment
- Create or edit .coveragerc, setup.cfg, pyproject.toml, or tox.ini coverage settings (omit, exclude_lines, source)
- Modify pytest.ini addopts, --cov-fail-under, or --cov paths without explicit approval
- Use pytest.skip, @pytest.mark.skip, skipif, xfail, or -p no:cov
- Write a test with no meaningful assert, or with assert True or an always-true condition
- Mock or patch the function or route under test (mock dependencies only: network, Selenium, subprocess, time)
- Delete, stub out, or simplify application code in src/ to raise coverage
- Wrap test bodies in try/except that swallows failures
- Use time.sleep() in tests
- Point any test or script at the jhu_module3 database; tests use jhu_module4_test only
- Read, print, or write module_4/.env

## Always
- Each test asserts observable behavior: status codes, JSON bodies, rendered HTML, DB rows
- Every test carries at least one marker: web, buttons, analysis, db, integration
- If a line seems untestable, report it and propose a design change (dependency injection) instead of excluding it
- Changes to src/ for testability must preserve Module 3 behavior; list each one
- Run pytest from the repo root: pytest -c module_4/pytest.ini module_4/tests
