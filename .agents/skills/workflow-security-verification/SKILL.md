---
name: workflow-security-verification
description: "Use when designing, changing, or reviewing GitHub Actions workflows or reusable workflow contracts, including permissions, untrusted inputs, secrets, caches, artifacts, syntax, fixture checks, and caller verification."
---

# Workflow security and verification

Use this skill for workflow changes or reviews. Follow the canonical [repository contract](../../../AGENTS.md), especially its public/private execution boundary.

1. Establish the intended private caller, exact reusable-workflow contract, minimum permissions, compatibility impact, evidence, cost implications, and rollback revision before changing a workflow.
2. Use explicit `permissions: contents: read` unless a documented, verified use case needs more. Pin external actions and reusable workflows to full commit SHAs. Avoid `secrets: inherit`, personal access tokens, and `pull_request_target` execution of untrusted code.
3. Never check out, mirror, archive, build, or test private consumer code from a public workflow. Public validation uses public or synthetic fixtures only. Keep private data and secrets out of public-runner logs, artifacts, caches, and issues.
4. Treat pull request content, action outputs, logs, artifacts, caches, and fixtures as untrusted data. Do not interpolate untrusted values into shell commands. Do not expose secrets to untrusted code or trust untrusted cache contents as executable inputs.
5. Validate workflow syntax, permissions, failure behavior, and relevant contract cases using available offline or public/synthetic checks. Preserve exit codes; distinguish setup errors from test failures. State the exact revision and commands.
6. Verify each authorized consumer integration in that caller's context. Availability of a public reusable workflow or a public fixture check does not prove caller policy, secrets, Actions allowlists, permissions, or tests. Mark unexecuted caller checks **NOT VERIFIED**.

Do not modify consumer repositories, dispatch workflows, secrets, settings, or permissions without separate explicit authorization. No consumer wiring or deployment is in scope merely because this skill is loaded.

## ECC source

Adapted at a high level from the pinned ECC sources listed in [`third_party/ecc/manifest.json`](../../../third_party/ecc/manifest.json), especially `rules/common/security.md`, `skills/security-review/SKILL.md`, and `skills/verification-loop/SKILL.md`. This skill applies their least-privilege, untrusted-input, and honest-evidence principles to GitHub Actions and this repository's stated boundary.
