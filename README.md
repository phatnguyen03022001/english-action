# english-action

**Current (TASK 07):** public Python Content Quality Engine, deterministic V1/V2 schema generation, regression tests and reusable GitHub-hosted CI. **Approved target (M1-A/M1-B):** production-parity Python engine, shareable test implementations, synthetic fixtures and reusable CI. Private english-content owns content/data, contracts/schemas, private quality evidence, editorial/rights decisions, confidential fixtures, revisioned publication and withdrawal. Private english owns pedagogy, learner assessment/progress and P1 direct database authoring-time AI drafts. Each private consumer retains its source, private tests, workflows/triggers, permissions, secrets, CI result and runner billing; a public workflow must not be used as a private-source CI proxy.

## Repository guidance

- [AGENTS.md](AGENTS.md) is the canonical repository contract and security boundary.
- [Codex skills](.agents/skills/) provide task-specific guidance for test design, workflow security and verification, and measured CI performance.
- [ECC provenance](third_party/ecc/manifest.json) records the pinned upstream sources and adapted files.

## Current status

The private english-content consumer now invokes a pinned Python Engine for schema and candidate commands; private regression, compatibility and rollback CI have been executed. Node historical-QA and fallback code remains in english-content. **Real publication stays disabled** without independently authenticated appointments, evidence, rights and editorial authority. The public repository neither executes private datasets nor proxies private CI billing.


## Approved target versus current implementation

- **Producer boundary:** exactly seven approved Foundation P0-a recipes (N1-01, N2-06, N3-11, K1-16, K1-17, K2-21, N2-10); the three P0-b recipes are reserved. The private producer owns publication, semantic correctness, rights, editorial authority and dependency/withdrawal evidence. Public Python implementation checks transferable rules using consumer-supplied schema directories, without copying confidential producer artifacts.
- **Status:** TASK 07 has moved schema/candidate command ownership to pinned Python. The private consumer independently runs legacy Node checks and private regression fixtures; not all historical JavaScript-dependent data-quality tests have Python replacements, so obsolete-code cleanup is separately gated. Technical validation is not publication approval.
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

## TASK 34 — Gate 2 feature specification validator

The public, standard-library validator checks deterministic Spec Kit 1.2.3 project-local structure for caller-owned feature specifications. It reads `specs/NNN-feature-name/spec.md` and the caller's `docs/product/capabilities.md`, checks required metadata/sections, stories and Given/When/Then cases, numbered FR/NFR/SC IDs, trace references, capabilities, relative Markdown paths/heading anchors, and explicit approval-provenance formatting. It never writes to the caller repository. A structural PASS is **not** semantic quality, approval authenticity, release readiness, privacy compliance or learning-evidence validity.

Run locally with the existing package installation:

```sh
python -m english_action.spec_validate --root /path/to/english
```

For **TASK 35**, the private `english` repository must separately add its own caller workflow and pin the reusable workflow to the full verified TASK 34 commit SHA. Pass that **same SHA** as the required `validator_sha` input. Example caller job (replace both placeholders with the identical, full immutable SHA):

```yaml
jobs:
  gate-2-spec:
    permissions:
      contents: read
    uses: phatnguyen03022001/english-action/.github/workflows/reusable-spec-validation.yml@<TASK_34_COMMIT_SHA>
    with:
      validator_sha: <TASK_34_COMMIT_SHA>
```

The reusable workflow checks out the calling repository's triggering `github.sha`, checks out the public validator at `validator_sha`, verifies both SHAs and runs validation in the **private caller's** GitHub Actions run. No secrets inheritance, PAT, artifact upload or private-source proxy in the public repository. Public CI runs only synthetic fixtures; private integration is **NOT VERIFIED** until TASK 35. Roll back by reverting the caller's workflow pin to its prior accepted revision or disabling that new caller job; the public repository cannot silently alter a pinned consumer.
