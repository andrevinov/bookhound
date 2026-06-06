# Repository Instructions

## Language

All repository content must be written in English.

This includes:

- documentation;
- code comments;
- inline help text;
- CLI command descriptions;
- test names and test data, unless the test explicitly validates multilingual
  search behavior;
- commit messages and pull request descriptions;
- issue templates and future project-management files.

User-facing examples may include non-English search keywords when they are
relevant to Bookhound's PDF discovery behavior, but the surrounding explanation
must remain in English.

## Project direction

Bookhound is a Python CLI for discovering PDFs by keyword, storing URLs and
metadata in SQLite, and downloading files only when the active mode and license
policy allow it.

Keep the implementation incremental and testable. Each task should be small
enough to have focused unit tests and should avoid real network calls in the
unit test suite.

## Personal implementation workflow

For each task in `docs/implementation-tasks.md`, use this workflow:

1. Start by writing the tests for the task.
2. Stop after the tests are written so the user can review them.
3. The user will mark reviewed tests with `pytest.mark.revised`.
4. After the reviewed tests are marked, implement the production code that
   makes those tests pass.
5. Once the implementation passes the reviewed tests, consider the task
   complete and move to the next task only when requested.

Do not implement production code for a task before its tests have been reviewed
and marked with `pytest.mark.revised`, unless the user explicitly changes this
workflow.

Never modify the code of a test marked with `pytest.mark.revised` under any
circumstance, unless the user gives a clear affirmative instruction explicitly
asking for that revised test to be changed.

## Safety rules

- `collect` must never download PDFs.
- `download` must always pass through the license gate.
- `daemon` must be non-interactive and conservative.
- External source integrations must use fixtures or mocks in unit tests.
- Do not bypass paywalls, logins, captchas, robots policies, or access blocks.
