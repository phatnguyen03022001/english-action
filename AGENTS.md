# English Action — Repository Contract

## Purpose and ownership

`phatnguyen03022001/english-action` is a **public GitHub Actions-only repository** serving exactly two private consumer repositories:

- `phatnguyen03022001/english` — English learning platform / product specifications.
- `phatnguyen03022001/english-content` — English learning content and data quality pipeline.

It owns **shared, reusable CI workflow definitions and their validation**. The consumer repositories own their source code, tests, dependencies, secrets, execution triggers, repository permissions, and test results. This repository is **not** a learning app, data store, content factory, deployment service, or general-purpose CI farm.

Do not copy private source, private test fixtures, credentials, proprietary content, datasets, or learner data into this public repository, its logs, artifacts, caches, or issues.

## GitHub Actions execution boundary

- Prefer **reusable workflows** (`on: workflow_call`) published here and invoked **from each private consumer repository** using `jobs.<job_id>.uses`. A caller can use public reusable workflows; the caller owns the run, runner allocation, permissions, and billing.
- **Do not** dispatch public workflows that check out, mirror, archive, build, or test private consumer source to obtain free public-repository runner minutes. A public workflow is not a substitute for private CI billing, and must not become a proxy for private code.
- Public-repository workflow validation may use **only public or synthetic fixtures**. Do not authorize access to private repositories from a public runner.
- Never assume a reusable workflow's availability proves a caller's secrets, policies, GitHub Actions allowances, or tests are configured. Verify each integration separately in the caller.
- Changing this repository must not write, dispatch, change Actions settings, or modify secrets in either consumer repository without separate, explicit authorization.

## Workflow design rules

1. **KISS / smallest correct workflow:** Define a reusable workflow only for an actual, repeated need in `english` and/or `english-content`. Prefer ordinary GitHub Actions primitives over custom actions, frameworks, or CI orchestration. Keep domain-specific test commands and release gates in their owning consumer repos.
2. **Isolation:** Separate only genuinely independent test suites or environment variants into parallel jobs or a small matrix. Start with the smallest useful concurrency, measure end-to-end duration, and limit `strategy.max-parallel` when needed. More jobs do not guarantee faster tests.
3. **Safety:** Explicit minimum `permissions: contents: read` by default; grant additional scopes only for a verified use case. Do not use `secrets: inherit` by default, personal access tokens, `pull_request_target` execution of untrusted code, or arbitrary private-repository checkout. Never print environment variables or secret values.
4. **Reproducibility:** Pin referenced third-party actions and reusable workflows to **full commit SHAs** for production use. Respect the consumer's own runtime version, package manager, lockfile, and test commands. Do not guess or override its stack. Make toolchain changes deliberate and tested.
5. **Efficiency:** Cache supported dependency stores by lockfile and environment, not sensitive data. Use `timeout-minutes`, bounded artifact retention, and `concurrency`/cancellation only where appropriate to the caller's workflow. Do not multiply setup costs through pointless sharding.
6. **Correctness:** Preserve exit codes; never ignore failing checks to make CI green. Mark skipped, unavailable, or not-run verification accurately. Distinguish test failures from setup failures. Report reproducible commands and the tested commit.
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
- `actions/<name>/action.yml` is allowed only for a **proven repeated set of steps** that needs a composite action, not as a replacement for reusable workflows.
- Scripts, fixtures, and tooling belong here only when required to validate or support **these public workflow definitions**. No copied product tests, no duplicated governance documents, no placeholder directories.

## Repository and change policy

- `main` only; one integration writer. Preserve Git history and unrelated changes. Never force-push or change repository visibility, branch protections, secrets, or Actions settings as a side effect.
- Follow the workspace `/Users/tienphat/Developer/AGENTS.md` and canonical SDLC Mini when working locally.
- Check current repository and consumer requirements before a workflow change. Record intended caller, exact contract, security boundary, test evidence, cost/concurrency trade-offs, and rollback to the previously pinned SHA.
- Do not add product features, release/deploy workflows, runtime services, containers, databases, new dependencies, or an automated private-repo sync without a separately approved real requirement.

**Current scope:** Record the repository's contract and standards. **No CI implementation, consumer wiring, secrets, or dispatch is authorized by this document alone.**
