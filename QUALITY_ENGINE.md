# Python Content Quality Engine (TASK 06)

Source: `english_action/`. Python 3.11+ and `jsonschema>=4.23,<5`. No Mac, MongoDB, private data, or network schema resolver required. Schemas are **supplied by the private caller** through `--schemas <root>` with `v1/` and `v2/` directories; this public repository does not ship private schemas, proprietary fixtures, editorial records or production data.

Run the package and public synthetic tests:

```sh
python3 -m pip install .
python3 -m unittest discover -s tests -v
python3 -m english_action validate --schemas /path/to/private/schema-root --input /path/to/private/batch.json --state /path/to/private/.draft/candidates-state.json
```

Operations: `validate`, `prepare`, `bind-evidence`, `revise`, `promote`, `check`. `--dry-run` never writes. Mutating operations acquire an exclusive state lock, reject symlink state paths, and atomically replace a private state file. Inputs and output JSON statuses retain the Node command vocabulary; **exact Node result parity is not certified**. `--full-publication-diagnostics` applies to `check`.

**Safety boundary:** The CLI has no owner-attestation/rights verification callbacks. `promote` is therefore blocked even if supplied JSON claims approval. The engine also hard-blocks positive publication eligibility with `QUALITY_PARITY_NOT_VERIFIED` until real private consumer parity, graph/diagnostics completion and independent authority verification are finished. `schema valid` and `ledger prepared` do not authorize publication. Do not remove this guard as part of TASK 06.

**Known work for TASK 07 readiness:** Full semantic graph equivalence, V1/V2 diagnostic ordering/message parity, all JavaScript number edge-case digests, private consumer fixtures/behavior comparison, trusted caller integration, positive authorized publication flows, and consumer rollback have **not** been verified. JavaScript implementation in `english-content` is untouched. Keep it as the production authoritative implementation; do not use this Python build as a cutover or publication decision engine yet.
