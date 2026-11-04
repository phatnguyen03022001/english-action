# Python Content Quality Engine — TASK 07

Public implementation: `english_action/` (Python 3.11+, `jsonschema>=4.23,<5`). The private caller supplies V1/V2 JSON Schemas, registry, data and all authentic editorial evidence. GitHub-hosted CI does not require a Mac, MongoDB or external network schema resolver.

## Engine and commands

```sh
python3 -m pip install .
python3 -m unittest discover -s tests -v
python3 -m english_action schemas-check --schemas /path/to/private/schemas
python3 -m english_action validate --schemas /path/to/private/schemas --input /path/to/batch.json --state /path/to/private/.draft/state.json
```

Operations: `schemas-check`, `schemas-generate --version v1|v2`, `validate`, `prepare`, `bind-evidence`, `revise`, `promote` and `check`. Schema generation uses caller-owned V1/V2 registries and writes only on explicit `schemas-generate`; normal schema checks never write. Persistent state mutations use exclusive locks, private permissions, atomic replacement, replay/CAS/history and fail-closed input handling.

The Python implementation includes V1/V2 registry schemas, lexical and lifecycle graph validation, claim-specific evidence and rights checks, editorial eligibility, versioned draft operations, V1 representation migration and eight-axis Golden QA. Public tests use synthetic fixtures only. English-content executes its own private parity and historical QA tests in its private runner.

## Two independent safety gates

**Technical engine use:** English-content's nine npm schema/candidate command entrypoints were switched to an immutable public Engine SHA under TASK 07 after positive and negative CI, synthetic stress, private integration and rollback tests. Node implementations remain in the private repository as test oracles and rollback references until their separate historical-QA responsibilities can be removed safely. Never use the public workflow to process private source or pay for a private consumer run.

**Real publication eligibility:** This CLI has no independently authenticated owner-appointment, evidence and rights provider. `promote` and positive publication eligibility are therefore deliberately blocked, even when isolated synthetic test callbacks establish a valid domain claim. The `QUALITY_PARITY_NOT_VERIFIED` guard is retained as a conservative production-publication lock, **not** as a claim that private technical test execution did not occur. Neither a passed schema check nor a technically migrated Engine authorizes actual content publication. Do not remove this guard without independent authority integration, a separately verified security boundary and Human Owner approval.

Source code and private data have distinct owners: `english-action` publishes only reusable public-safe Python code and tests; `english-content` owns schemas, datasets, evidence, editorial policy, publication metadata and confidential fixtures.
