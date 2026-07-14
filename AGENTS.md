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

## PRAIA remediation workflow

Files under `docs/code-audit/praia/` are audit findings, not automatically
approved implementation tasks.

Before modifying production code or tests for a PRAIA finding:

1. Read the finding, the level summary, and the level backlog.
2. Validate that the cited evidence still matches the current repository state.
3. Check whether the finding duplicates or shares a root cause with another
   finding.
4. Wait for the user to mark the finding as `Accepted`.
5. Work on only one accepted finding, or one explicitly approved root-cause
   group, at a time.

For each accepted finding:

1. Perform a repository-wide search for existing classes, functions, fixtures,
   protocols, helpers, and abstractions related to the change.
2. Produce a short change plan listing:
   - files to modify;
   - files to create;
   - existing components to reuse;
   - architectural boundaries affected;
   - tests required;
   - risks and behavior that must be preserved.
3. Do not create a new file, class, protocol, repository, service, fixture, or
   helper when an existing cohesive component can reasonably be extended.
4. Write focused regression or characterization tests before changing
   production code.
5. Stop after writing the tests so the user can review them, following the
   existing revised-test workflow.
6. After approval, implement the smallest coherent change that resolves the
   root cause, not merely the visible symptom.
7. Run the focused tests and the complete relevant test suite.
8. Recheck the original evidence and acceptance criteria.
9. Update the finding and backlog with the result, affected files, tests added,
   and final status.

A finding may use the following statuses:

- `Proposed`
- `Needs Evidence`
- `Accepted`
- `In Progress`
- `Resolved`
- `Rejected`
- `Deferred`

Do not:

- implement all PRAIA findings in a single change;
- assume that every audit conclusion is correct;
- modify production code for a `Proposed`, `Needs Evidence`, `Rejected`, or
  `Deferred` finding;
- create parallel implementations of existing concepts;
- solve an architectural finding only by mocking around the problem;
- mark a finding as resolved merely because its new focused test passes;
- begin the next PRAIA level until the user explicitly requests it.

After resolving all accepted P0 and P1 findings from a level, perform a focused
verification audit of that level before proceeding to the next one.
