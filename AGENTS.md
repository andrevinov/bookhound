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

## Local collaboration notes

Use `.collab-notes/` for private local reports, plans, and working notes shared
between the user and Codex during implementation. The directory must stay
untracked by git and excluded from Docker build contexts. Unlike tracked
repository documentation, all materials inside `.collab-notes/` must be written
in Portuguese to support the local collaboration workflow.

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

This restriction applies to the individual revised test, not to the whole test
file. It is acceptable, and preferred when it keeps the suite organized, to add
new tests to an existing file that already contains revised tests. Before
editing such a file, double-check that no revised test body, parameters, marks,
name, or expected assertions are changed.

## Safety rules

- `collect` must never download PDFs.
- `download` must always pass through the license gate. User-recorded explicit
  authorization can satisfy that gate only when the authorization evidence and
  scope are persisted.
- `daemon` must be non-interactive and conservative.
- External source integrations must use fixtures or mocks in unit tests.
- Do not bypass paywalls, logins, captchas, robots policies, or access blocks.

## Local configuration with secrets

- Public template configuration files must not contain personal information,
  credentials, API keys, private emails, or other secrets.
- When a local template needs secret values, keep a same-purpose
  `*.with-secrets.toml` file next to the public template and make sure it is
  ignored by git.
- For Docker local configuration, keep `config/bookhound.docker.example.toml`
  public and put local secrets in `config/bookhound.docker.with-secrets.toml`.
  The helper scripts must prefer the `with-secrets` file when it exists and
  copy it to `.local/bookhound.toml`.
- Whenever the public local Docker config template is expanded with new secret
  fields, mirror the same fields in the `with-secrets` file and fill in the
  local secret values there.
