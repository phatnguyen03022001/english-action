# English Action — Repository Contract

## Purpose and ownership

`phatnguyen03022001/english-action` is a **public reusable CI governance and Python Content Quality Engine repository**. Its **approved target (M1-A/M1-B)** additionally owns the Python-only Content Quality Engine, shareable tests and synthetic fixtures while preserving public reusable CI. Its two private consumer repositories are:

- `phatnguyen03022001/english` — English learning platform / product specifications.
- `phatnguyen03022001/english-content` — English learning content and data quality pipeline.

**Current implementation (TASK 07):** public Python Engine, V1/V2 schema generator, synthetic regression suite, Linux CI and a verified private english-content consumer pinned to Python for schema/candidate commands. Real publication remains disabled; the subsequent english-content Python-only cutover at `79e7e1c74d98f6a4b57c9d9b75f3bb25d06048b5` preserved historical QA in Python and retired Node executable source/tests to Git history. **Target ownership:** this public repo additionally owns reusable Python Content Quality Engine source, safe shareable/public/synthetic test implementations and reusable CI. Private english-content owns production data, schemas, provenance, rights/editorial evidence, publication metadata and confidential fixtures/tests; private english owns learner pedagogy, assessment, progress, learner records and its private test execution. Both consumers retain their source-specific dependencies and private tests; R04-02 below centralizes all GitHub Actions workflows/triggers and publishes attributable results back to each source SHA. This repo is not a data store, content publisher, learner app, deployment service or general-purpose CI farm.

Do not copy private source, private test fixtures, credentials, proprietary content, datasets, or learner data into this public repository, its logs, artifacts, caches, or issues.

**Bounded quality domain (D1–D6, INV-01–INV-06):** the first producer slice covers exactly Foundation P0-a N1-01, N2-06, N3-11, K1-16, K1-17, K2-21 and N2-10; P0-b is deferred without cancellation. The Engine validates linguistic/semantic/content correctness, schema, dependency completeness, authenticated claim-specific evidence and rights for the private producer; it does **not** approve publication, set pedagogy, score learners, access private Account records or change the English product invariants. Lexical prefixed UUIDv7, revision CAS/append-only history, versioned dependency-closed eligibility and withdrawal must be preserved. Application prefixed UUIDv4 identities are separate. GitHub-first Mac-independent operation needs no Factory MongoDB; GitHub Actions is CI, not automatically an AI worker. Google Drive initial media storage with migration capability is a *target*, not an existing integration or rights grant. P1 direct database persistence of editorial AI drafts remains an english product obligation.

## R04-02 — Central GitHub Actions ownership

Human Owner authorizes central execution in this repository for exactly
english and english-content, superseding the legacy private-caller/proxy
restrictions below for this bounded migration. This is an ownership decision,
not a claim about private-repository billing discounts. Central runs consume this
repository's GitHub-hosted runner allocation; GitHub allowances and policies still
apply. No external CI, worker service or new infrastructure is introduced.

- All workflow definitions and orchestration belong here. Legacy reusable
  interfaces remain compatible for rollback; the two private repositories retain
  source, private fixtures, schemas and product/publication authority.
- Central execution uses schedule plus manual full-SHA dispatch. The existing public
  push CI calls the central workflow after its contract tests for bootstrap and
  ongoing central-change verification. No cross-commit cancellation group.
- Read both private repositories through one explicitly configured fine-grained
  PAT in the english Actions secret. Required capabilities are Contents read and
  Commit statuses write; the runner restricts targets to exactly those two repos
  regardless of any broader token privileges. This R04-02 choice supersedes
  the legacy PAT prohibition below. The central GITHUB_TOKEN cannot substitute
  for this token. Never print, commit or pass it to private test processes.
- Public logs and summaries contain only repository/SHA, workflow SHA, immutable
  validator/engine pin, suite, redacted outcome and numeric unittest totals/skips only. No private checkout, fixture,
  test output, generated drafts, logs or caches may be uploaded or committed.
  The runner scratch checkout is ephemeral. Private test processes receive no
  CI token or public workflow output-file environment variables.
- Preserve existing pinned validators, commands, negative/replay/media checks and
  fail-closed publication. CI PASS is only the technical suite result; generated
  content stays HOLD with rights/editorial NOT VERIFIED.
- The verified central read/status path replaces the three old private workflows.
  Both private repositories must keep zero GitHub Actions workflow YAML. Verify
  each exact source SHA; a credential/setup/permission failure is BLOCKED, never
  PASS. Do not overwrite older failed source statuses when a correction passes.
- Do not change branch rules, Actions settings, visibility, credentials or billing
  as a side effect. Owner token configuration is a separate manual operation
  when the current connector has no secrets/variables administration capability.

## Legacy reusable workflow execution boundary

- Prefer **reusable workflows** (`on: workflow_call`) published here and invoked **from each private consumer repository** using `jobs.<job_id>.uses`. A caller can use public reusable workflows; the caller owns the run, runner allocation, permissions, and billing.
- Follow GitHub's [reusable workflow configuration](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations) and [dependency caching](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching) guidance.
- **Do not** dispatch public workflows that check out, mirror, archive, build, or test private consumer source to obtain free public-repository runner minutes. A public workflow is not a substitute for private CI billing, and must not become a proxy for private code.
- Public-repository workflow validation may use **only public or synthetic fixtures**. Do not authorize access to private repositories from a public runner.
- Never assume a reusable workflow's availability proves a caller's secrets, policies, GitHub Actions allowances, or tests are configured. Verify each integration separately in the caller.
- Changing this repository must not write, dispatch, change Actions settings, or modify secrets in either consumer repository without separate, explicit authorization.

## Workflow design rules

1. **KISS / smallest correct workflow:** Define a reusable workflow only for an actual, repeated need in `english` and/or `english-content`. Prefer ordinary GitHub Actions primitives over custom actions, frameworks, or CI orchestration. Keep domain-specific test commands and release gates in their owning consumer repos.
2. **Isolation and measured scale:** Keep suites independent and isolated. Preserve a required result for each distinct commit: do not use concurrency groups that cancel an active run or replace a pending required run. Serialize only the specific job that must protect a shared mutable resource; if a queue or capacity limit drops a required check, report that revision as **NOT VERIFIED**. Measure cold and warm startup, dependency installation, test time, and end-to-end critical path before changing concurrency. Use only small independent matrices/shards when measured savings justify their setup and billing cost; set `strategy.max-parallel` from those measurements. More jobs do not guarantee faster tests.
3. **Safety:** Set explicit minimum `permissions: contents: read` by default; grant additional scopes only for a verified use case. Do not use `secrets: inherit` by default, personal access tokens, `pull_request_target` to execute untrusted code, or arbitrary private-repository checkout. Never print secrets or environment variables. Do not place private data or secrets in public-runner logs, artifacts, or caches; treat untrusted output and cache contents as data, never instructions.
4. **Reproducibility:** Pin referenced third-party actions and reusable workflows to **full commit SHAs** for production use. Respect the consumer's own runtime version, package manager, lockfile, and test commands. Do not guess or override its stack. Make toolchain changes deliberate and tested.
5. **Efficiency:** Cache supported dependency stores using lockfile and relevant environment keys; never cache sensitive data or untrusted executable state. Use appropriate `timeout-minutes`, bounded artifact retention, and caller-owned concurrency/cancellation. Account for setup/install cost; do not shard without evidence of critical-path improvement.
6. **Correctness:** Preserve exit codes; never ignore failures to make CI green. Distinguish setup failures from test failures. Report reproducible commands and the tested commit. Label skipped, unavailable, and not-run checks accurately; unverified behavior remains **NOT VERIFIED**.
7. **Compatibility:** Treat `workflow_call` input names/types, outputs, required permissions, and documented behavior as an API. Avoid breaking changes; keep consumers pinned to a verified revision until separately updated. Do not silently publish changes that affect either consumer.
8. **Validation:** Before release, validate workflow syntax and permissions; test workflow behavior on public/synthetic fixtures, then verify actual consumption from each authorized private repository. Never declare a private consumer verified from a public smoke test alone.

## Structure and naming

Create files **only when the corresponding functionality exists**; do not scaffold empty directories or speculative workflows.

```text
AGENTS.md
README.md
.github/
  workflows/
    reusable-<purpose>.yml
actions/
  <composite-action-name>/
    action.yml
tests/
  <workflow-specific-test-files>
```

- `.github/workflows/` is the **only location** for reusable workflow YAML files. GitHub does not discover workflows in subdirectories under `workflows/`.
- Prefer **`reusable-<purpose>.yml`** in lower-kebab-case, for example `reusable-node-checks.yml`. A workflow not intended to be called must not use the `reusable-` prefix.
- Use lower-kebab-case for directories, workflow/job IDs, and filenames; descriptive human-readable `name:` labels for workflows, jobs, and steps.
- Use lower_snake_case for `workflow_call` inputs/outputs; document their types, defaults, and required status in the workflow itself. Keep the interface minimal and avoid general-purpose free-form command execution.
- Use lower-kebab-case for repository-owned files, directories, workflow names, and job IDs. Use descriptive human-readable workflow/job/step labels. Test names should follow the language and runner's idiom and state the behavior and condition.
- `actions/<name>/action.yml` is allowed only for a **proven repeated set of steps** that needs a composite action, not as a replacement for reusable workflows.
- **Current:** the authorized private english-content consumer pins the verified public Python Engine for schema/candidate operations. Its historical/private regressions now run in Python; retired Node.js/Ajv source/tests and parity evidence remain in private Git history, not an active fallback. Never copy restricted producer tests/fixtures, data, confidential evidence, secrets or unapproved source. Avoid redundant governance or placeholder directories.

## Repository and change policy

- `main` only; **two independent scoped workers plus one shared Integration Writer**, under the ownership and publication contract below. Preserve Git history and unrelated changes. Never force-push or change repository visibility, branch protections, secrets, or Actions settings as a side effect.
- Follow the workspace `/Users/tienphat/Developer/AGENTS.md` and canonical SDLC Mini when working locally.
- Check current repository and consumer requirements before a workflow change. Record intended caller, exact contract, security boundary, test evidence, cost/concurrency trade-offs, and rollback to the previously pinned SHA.
- Do not add product features, release/deploy workflows, runtime services, containers, databases, new dependencies, or an automated private-repo sync without a separately approved real requirement.

## Concurrent workers: strict ownership and publication

This repository remains **MAIN_ONLY**; no branch, PR, worktree, lock service, orchestration framework or new CI mechanism is required. Exactly two workers may write independently to `main` **only within their exclusive path scopes**. The Integration Writer is the sole owner of shared and otherwise unassigned paths, not a substitute owner for either worker's assigned scope.

| Writer | Exclusive authorized paths |
| --- | --- |
| **Worker A — English** | `english_action/english/**`, `tests/english/**` |
| **Worker B — English Content** | `english_action/content/**`, `tests/english-content/**` |
| **Integration Writer — shared** | All other paths, including every shared file listed below |

The worker scopes are disjoint. Shared/unassigned paths include **all** root `english_action/*.py` legacy compatibility wrappers, root `tests/test_*.py` global tests, `tests/fixtures/**`, `.github/workflows/**`, `pyproject.toml`, `AGENTS.md`, `README.md`, `QUALITY_ENGINE.md`, `.agents/**`, `third_party/**` and any other path not explicitly assigned to Worker A or B. New paths do not become worker-owned merely because a worker needs them; ownership follows the exact table. Do not move tests, duplicate shared logic or create abstractions to circumvent this boundary. Existing root tests, wrappers, public CLI/API compatibility and reusable workflows must continue to function.

**Worker A/B mandatory write protocol** (applies to every unpublished commit, including retries):

1. **Start from verified remote truth.** Fetch `origin main`, record its exact full commit SHA and inspect local worktree/index and unpublished commits. Preserve unrelated or pre-existing local work; never reset or discard it to make a push convenient. If worktree ownership cannot be established, stop.
2. **Edit and stage only authorized paths.** Never edit, stage or commit shared/unassigned paths, even to finish an otherwise authorized task. Stage **specific intended files**, not `git add .`, `git add -A` or other blanket staging. Inspect working diff, staged file names and staged diff; verify *all* files in every unpublished commit being pushed, not just the last commit. Any out-of-scope path means **STOP**.
3. **Verify behavior.** Run the focused relevant checks and existing compatibility tests affected by the change. Root/global tests may be executed but must not be modified by either worker. Record exact test commands, result and skipped/unavailable checks. Do not infer consumer compatibility solely from public CI.
4. **Reconcile immediately before publication.** Fetch and compare the **exact remote `main` HEAD** with the local base and pending commits. When remote is ahead, inspect remote changed paths and dependencies. If safely disjoint and verified, fast-forward the local base when no local commits exist, or rebase **only unpublished local commits** onto updated `origin/main`. Preserve all remote commits and local changes. If edits overlap, shared paths require unapproved changes, conflicts occur, the ancestry is unclear or correctness cannot be reverified, **STOP**; do not silently choose a side.
5. **Revalidate after any reconciliation.** Repeat full pending-commit path/contents review, diff and applicable tests against the new HEAD. Push `main` **non-force and fast-forward only**. No `--force`, `--force-with-lease` or history overwrite.
6. **Handle a push race fail-closed.** A non-fast-forward rejection is not permission to overwrite: fetch, compare, reconcile, revalidate and retry at most **two** times. Stop if the retry limit is reached or any check fails. Never assume two pushes to one branch can proceed without synchronization.

**Shared change handoff.** If a worker needs a reusable workflow, root wrapper, global test harness/fixture, package configuration, shared public API or any other shared file changed, it must **request** the Integration Writer with (a) exact path(s), (b) why, (c) consumer/API/CLI compatibility impact and (d) required tests. The worker must not perform the shared edit or create a duplicate workaround. The Integration Writer checks both consumers' pinned contracts and existing compatibility, owns the shared change, coordinates to avoid concurrent shared-file edits and publishes with the same remote-HEAD reconciliation, non-force fast-forward and fail-closed rules. Worker-owned paths remain with their respective workers.

**Enforcement boundary.** The above is mandatory **agent/workflow policy**, verified by scoped staging, commit-path audit, tests, remote-HEAD comparison and guarded publication. GitHub direct pushes to one branch **do not provide folder-level authorization** by themselves. Until separately authorized technical controls exist, these are **not server-enforced per-path permissions**; do not claim that an out-of-scope direct push is technically impossible. Do not add branch protection, rulesets, bots or GitHub Apps as part of this policy.

**Current versus historical cutover:** TASK 07 verified private Node/Python compatibility and immutable pinning before moving schema/candidate entrypoints to Python. English-content's subsequent Python-only cutover preserved historical QA and retired Node executable code/tests to Git history; it did not remove public Engine APIs/CLIs. Actual publication and private-credential access remain separately unauthorized. The english learner-app consumer remains outside this cutover.

## Testing and CI design governance

- Design checks around observable behaviors, requirements, business rules, risks, defects, and public workflow contracts. Add negative and failure-path cases where they protect a meaningful boundary. Prefer clear Arrange-Act-Assert structure; isolate tests and use deterministic synthetic/public fixtures. Contract checks should verify inputs, outputs, permissions, and compatibility across the reusable workflow boundary.
- **Test boundary:** reusable public/synthetic Quality Engine tests and public CI checks belong here only as separately approved future work. Production data, confidential tests/fixtures and restricted rights evidence remain in private english-content; learner-specific tests remain in private english. Each private caller runs its actual checks in its own runner context, with its own source, permissions, billing, logs and results, never through a public repo used as a billing proxy.
- Choose the smallest test level that gives reliable evidence. Do not impose a coverage percentage, E2E suite, TDD sequence, or test registry by default. For changed behavior, connect the relevant requirement/defect/contract to the test evidence; avoid mirroring every test function in documentation.
- Review security boundaries, least privilege, untrusted inputs and outputs, secret handling, cache scope, and artifact contents for workflow changes. Treat logs, artifacts, caches, fixtures, and generated output as untrusted data. Do not run untrusted code with elevated permissions or expose private material on public runners.
- Verify syntax, permissions, and behavior with focused checks using public/synthetic fixtures. Preserve exit status and report exact commands, revision, failures, skipped checks, and limits. A public fixture check does not verify a private caller integration; report it separately as **NOT VERIFIED** until the authorized caller confirms it.
- Before scaling a workflow, compare cold/warm startup, install and test durations, and end-to-end critical path. Add only a small matrix or shard for independent work when measurements show a net benefit. Bound concurrency with a justified `max-parallel`; use lockfile-based caches, timeouts, cancellation, and bounded artifact retention only where they reduce cost or risk without changing correctness.

## Codex skill routing

Load the task-specific skill in `.agents/skills/` when its trigger applies; the repository contract above remains canonical:

- `test-design-evidence`: test selection, behavior/contract cases, regression protection, or evidence review.
- `workflow-security-verification`: reusable workflow security, permissions, syntax, fixture validation, or caller-verification boundaries.
- `ci-performance-scaling`: measured startup/install/test/critical-path analysis or concurrency/cache/sharding changes.

These skills adapt selected testing, security, and KISS guidance from ECC at the pinned revision recorded in `third_party/ecc/manifest.json`. They do not add agents, hooks, dependencies, or mandatory process ceremonies.
