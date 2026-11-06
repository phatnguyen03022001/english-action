# english-action

**Current (TASK 07):** public Python Content Quality Engine, deterministic V1/V2 schema generation, regression tests and reusable GitHub-hosted CI. **Approved target (M1-A/M1-B):** production-parity Python engine, shareable test implementations, synthetic fixtures and reusable CI. Private english-content owns content/data, contracts/schemas, private quality evidence, editorial/rights decisions, confidential fixtures, revisioned publication and withdrawal. Private english owns pedagogy, learner assessment/progress and P1 direct database authoring-time AI drafts. Each private consumer retains its source, private tests, workflows/triggers, permissions, secrets, CI result and runner billing; a public workflow must not be used as a private-source CI proxy.

## Repository guidance

- [AGENTS.md](AGENTS.md) is the canonical repository contract and security boundary.
- [Codex skills](.agents/skills/) provide task-specific guidance for test design, workflow security and verification, and measured CI performance.
- [ECC provenance](third_party/ecc/manifest.json) records the pinned upstream sources and adapted files.

## Feature modules and compatibility

- `english_action/english/` owns independent Spec Kit specification validation for `english`. It does not import the content feature.
- `english_action/content/` owns the Python Quality Engine, schema generation, candidate ledger/review and draft-only Spawn Engine for `english-content`. It does not import the English specification feature.
- Legacy `english_action.*` imports, `python -m english_action`, `python -m english_action.spec_validate`, `python -m english_action.spawn`, `english-quality` and `english-spawn` remain available; the legacy Python modules delegate to the feature implementations. New direct module paths are `english_action.english.spec_validate`, `english_action.content.cli` and `english_action.content.spawn`.
- Both existing reusable workflows stay in `.github/workflows/` and keep their inputs. Consumers may remain pinned to previous verified SHAs; no private consumer change is implied by this refactor.

## Current status

The private [english-content consumer at Python-only cutover](https://github.com/phatnguyen03022001/english-content/commit/79e7e1c74d98f6a4b57c9d9b75f3bb25d06048b5) invokes the pinned Python Engine for schema/candidate commands and Python historical/private regressions. Node/Ajv is no longer its runtime or CI dependency; retired Node source/tests remain recoverable in Git history, not as an active fallback. **Real publication stays disabled** without independently authenticated appointments, evidence, rights and editorial authority. The public repository neither executes private datasets nor proxies private CI billing.


## Approved target versus current implementation

- **Producer boundary:** exactly seven approved Foundation P0-a recipes (N1-01, N2-06, N3-11, K1-16, K1-17, K2-21, N2-10); the three P0-b recipes are reserved. The private producer owns publication, semantic correctness, rights, editorial authority and dependency/withdrawal evidence. Public Python implementation checks transferable rules using consumer-supplied schema directories, without copying confidential producer artifacts.
- **Status:** TASK 07 moved schema/candidate ownership to pinned Python; the subsequent private cutover preserved historical QA in Python and retired Node executable code/tests. Existing historical Node/parity evidence is retained. This consumer cutover does not remove a public engine API/CLI or prove a live producer, learner-app integration or publication approval.
- **Safety:** public or synthetic fixture data only; never expose private evidence, proprietary content, customer/learner data, credentials or confidential tests in public code, logs, caches, artifacts or issues. Content-quality PASS does not approve editorial claims, distribution rights, learner scoring or release.
- **Identity and policy:** preserve lexical prefixed UUIDv7 with revision CAS/history, consumer prefixed UUIDv4 as a different namespace, versioned dependency-closed and fail-closed use eligibility, correction and withdrawal. Follow INV-01–INV-06, including P0 runtime AI Assessment OFF.
- **Execution/storage:** GitHub-first Mac-independent work, hosted GitHub Actions CI in the private caller and no mandatory Factory MongoDB. Initial Google Drive media storage with migration capability is an unimplemented target, **not a commercial media/voice license**. No autonomous AI worker is implied by a CI runner.


## TASK 08 — Foundation P0-a Spawn Engine (draft-only)

`english_action.spawn` implements a bounded asynchronous Seed → Worker → Generate → Merge → Private Contract → Python Quality Engine → DRAFT/HOLD pipeline. It covers exactly N1-01, N2-06, N3-11, K1-16, K1-17, K2-21 and N2-10. Worker fanout is 1–16, per-attempt timeout is 0–120 seconds, retry count 0–3, and each batch contains at most 100 seeds. Persistent private JSON drafts use the existing atomic state-file locking mechanism, stable candidate identity, CAS revision checks, replay safety and before/after history. Conflicting or invalid outputs are rejected and never eligible for publication.

The public module exposes the async `Worker` protocol plus an explicit synthetic fixture worker and opt-in OpenAI Chat Completions adapter. The adapter is **not verified against a live provider** by synthetic CI. Real invocation requires a configured compatible model, `OPENAI_API_KEY`, and explicit `--provider openai --allow-real-ai`; requests are bounded by timeout and completion-token limit. GitHub Actions neither supplies nor authorizes AI credentials automatically. No real provider is used by default.

The private consumer supplies the versioned producer JSON Schema, lexical schemas, lexical candidate fixture, source references and media. Example:

```sh
python -m english_action.spawn \
  --input data/spawn/seeds/foundation-p0a-synthetic.json \
  --contract schemas/spawn/foundation-p0a.schema.json \
  --schemas schemas \
  --quality-input data/candidates/pilot-fanout-001.json \
  --media-root /private/test-fixtures \
  --state /private/drafts/state.json \
  --provider synthetic --concurrency 3 --timeout 15 --retries 1
```

Private CI runs this against a full-SHA-pinned public engine, verifies synthetic WAV fixture hashes, tests all seven positive and negative producer shapes, checks replay, and uploads a versioned **private, 7-day-retained synthetic draft artifact**. This is test storage, not an approved durable production draft database or Google Drive upload. Real audio pronunciation, media storage integration, independent linguistic evidence, rights and owner-appointed editorial review are **NOT VERIFIED**; every candidate remains HOLD and public release stays disabled. The producer never scores learners or calls the english app.

## Gate 2 specification and documentation validator

The public, standard-library validator checks deterministic Spec Kit 1.2.3 project-local structure for caller-owned feature specifications. It reads `specs/NNN-feature-name/spec.md` and the caller's `docs/product/capabilities.md`, checks required metadata/sections, stories and Given/When/Then cases, numbered FR/NFR/SC IDs, trace references, capabilities and explicit approval-provenance formatting. It also scans `PRODUCT.md`, `README.md`, `AGENTS.md`, `docs/**/*.md`, feature Markdown and the English-owned `.specify/templates/overrides/spec-template.md` for local link targets/heading anchors and established naming conventions. Override links resolve as if copied to `specs/NNN-feature-name/spec.md`; vendored Spec Kit templates are excluded. Advisory LOC budgets (preferred/review): feature spec 250/350, product/discovery 300/450, domain contract 400/600, ADR 200/300, owned template 150/200. Overages print bounded `WARNING` diagnostics and **never** fail validation by themselves; confirmed structural/link/path errors print `ERROR` and exit `1`, invalid invocation exits `2`. No external URL fetch or repository write occurs. A structural PASS is **not** semantic quality, approval authenticity, release readiness, privacy compliance or learning-evidence validity.

Run locally with the existing package installation:

```sh
python -m english_action.spec_validate --root /path/to/english
```

A private `english` caller must pin the reusable workflow and its required `validator_sha` input to the **same verified full commit SHA**. The TASK 39 public validator update does not modify or automatically update that private caller; TASK 38 owns its separate caller workflow/pin integration. Example job (replace both placeholders with the identical, immutable full SHA):

```yaml
jobs:
  gate-2-spec:
    permissions:
      contents: read
    uses: phatnguyen03022001/english-action/.github/workflows/reusable-spec-validation.yml@<VERIFIED_VALIDATOR_SHA>
    with:
      validator_sha: <VERIFIED_VALIDATOR_SHA>
```

The reusable workflow checks out the calling repository's triggering `github.sha`, checks out the public validator at `validator_sha`, verifies both SHAs and runs validation in the **private caller's** GitHub Actions run. No secrets inheritance, PAT, artifact upload or private-source proxy in the public repository. Public CI runs only synthetic fixtures; validation against a private checkout is read-only and separate from private CI integration. Roll back by restoring both caller pins to the previously accepted SHA. The public repository cannot silently alter a pinned consumer.
