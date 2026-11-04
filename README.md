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
