# english-action

Public repository for reusable GitHub Actions workflow definitions and their public or synthetic validation. The private repositories `english` and `english-content` own their source, tests, execution triggers, permissions, secrets, and test results. Reusable workflows run in the caller's context, including its runner allocation and billing.

## Repository guidance

- [AGENTS.md](AGENTS.md) is the canonical repository contract and security boundary.
- [Codex skills](.agents/skills/) provide task-specific guidance for test design, workflow security and verification, and measured CI performance.
- [ECC provenance](third_party/ecc/manifest.json) records the pinned upstream sources and adapted files.

## Current status

This repository currently contains governance documentation and Codex skills only. It has no deployed CI workflows and makes no claim that either private consumer is integrated or verified. This public repository does not run private consumer tests or act as a public CI runner proxy.
