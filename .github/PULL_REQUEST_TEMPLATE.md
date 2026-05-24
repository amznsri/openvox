<!--
Thanks for contributing to OpenVox. The template below mirrors the
PR descriptions we've been writing all of Session 17 — fill in what
applies, delete what doesn't. Short PRs don't need every section.
-->

## Summary

<!-- One paragraph: what changes, why. Reference the issue / planning
doc / CLAUDE.md bug number that motivated it. -->

## What changed

<!-- Bulleted list of the *user-visible* deltas. Code touched per file
is in the diff; this section is the why-it-matters version. -->

-

## Test plan

<!-- Mark each box you've actually run. Don't pretend; reviewers
trust the list. -->

- [ ] `pytest -m "not e2e"` from `packages/core/` passes
- [ ] `pytest -m e2e` passes (if change touches the daemon, API
      routes, or provider bootstrap)
- [ ] If the change touches the dashboard: started it locally and
      walked the affected page in a browser
- [ ] If the change touches a provider: ran `openvox info` and
      confirmed the provider's `is_available()` still returns the
      expected value
- [ ] If the change touches the install path: ran
      `bash scripts/smoke_linux.sh branch` or the macOS-doc smoke

## Risk / rollback

<!-- For non-trivial changes: what could break in prod, how would
you notice, how would you roll back. Skip for docs/test-only PRs. -->
