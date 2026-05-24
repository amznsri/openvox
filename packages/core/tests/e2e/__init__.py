"""End-to-end tests — spawn the daemon as a real subprocess + drive
it via HTTP + Playwright browser.

Slow by design (~5-15 s per test). All tests in this package carry
the ``@pytest.mark.e2e`` marker so `pytest -m "not e2e"` skips them
by default. The default `pytest` invocation runs them; CI runs them
in a separate job (per PLANNING_SESSION17.md §Phase 2 sub-task 3).

The fixtures these tests rely on live in ``tests/conftest_e2e.py``
(shared with future e2e tests). Don't define new fixtures here —
keep them centralised.
"""
