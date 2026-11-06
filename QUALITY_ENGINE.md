# Python Content Quality Engine — TASK 07

Public implementation: `english_action/content/` (Python 3.11+, `jsonschema>=4.23,<5`); legacy `english_action.*` APIs/CLIs remain supported. The private caller supplies V1/V2 JSON Schemas, registry, data and all authentic editorial evidence. GitHub-hosted CI does not require a Mac, MongoDB or external network schema resolver.

## Engine and commands

```sh
python3 -m pip install .
python3 -m unittest discover -s tests -v
python3 -m english_action schemas-check --schemas /path/to/private/schemas
python3 -m english_action validate --schemas /path/to/private/schemas --input /path/to/batch.json --state /path/to/private/.draft/state.json
```

Operations: `schemas-check`, `schemas-generate --version v1|v2`, `validate`, `prepare`, `bind-evidence`, `revise`, `promote` and `check`. Schema generation uses caller-owned V1/V2 registries and writes only on explicit `schemas-generate`; normal schema checks never write. Persistent state mutations use exclusive locks, private permissions, atomic replacement, replay/CAS/history and fail-closed input handling.

Spawn reports keep `quality` as a compatibility alias for `lexicalSourceQuality`. `generatedContentQuality` records checks and issues for each candidate ID, revision and content hash. A draft may pass recipe, producer-contract and local-media checks while external references or editorial correctness remain `NOT_VERIFIED`. Replay of historical reports retains their original lexical result and marks generated-content quality as unverified when no corresponding evidence was recorded.

Generator media-rights labels appear only as untrusted claims in `rights.generatorClaims`. New draft content uses `rightsStatus: unknown`. Spawn records no authenticated evidence, keeps clearance `UNVERIFIED` and never makes drafts eligible for publication. Older immutable snapshots may contain raw claims and confer no clearance.

The Python implementation includes V1/V2 registry schemas, lexical and lifecycle graph validation, claim-specific evidence and rights checks, editorial eligibility, versioned draft operations, V1 representation migration and eight-axis Golden QA. Public tests use synthetic fixtures only. English-content executes its own private parity and historical QA tests in its private runner.

## Two independent safety gates

**Technical engine use:** TASK 07 switched English-content's nine then-npm schema/candidate entrypoints to an immutable public Engine SHA after positive/negative CI, stress, private integration and rollback checks. The [subsequent Python-only consumer cutover](https://github.com/phatnguyen03022001/english-content/commit/79e7e1c74d98f6a4b57c9d9b75f3bb25d06048b5) preserved historical QA in Python and retired Node implementation/tests; original Node/parity evidence and retired source remain in Git history. There is no active consumer Node fallback, and no public capability/API is removed by that consumer migration. Never use the public workflow to process private source or pay for a private consumer run.

**Real publication eligibility:** This CLI has no independently authenticated owner-appointment, evidence and rights provider. `promote` and positive publication eligibility are therefore deliberately blocked, even when isolated synthetic test callbacks establish a valid domain claim. The `QUALITY_PARITY_NOT_VERIFIED` guard is retained as a conservative production-publication lock, **not** as a claim that private technical test execution did not occur. Neither a passed schema check nor a technically migrated Engine authorizes actual content publication. Do not remove this guard without independent authority integration, a separately verified security boundary and Human Owner approval.

Source code and private data have distinct owners: `english-action` publishes only reusable public-safe Python code and tests; `english-content` owns schemas, datasets, evidence, editorial policy, publication metadata and confidential fixtures.
