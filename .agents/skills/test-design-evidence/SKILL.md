---
name: test-design-evidence
description: "Use when selecting or reviewing tests for changed behavior, a requirement, defect, regression, or reusable workflow contract; design focused behavioral evidence, including meaningful negative cases, without adding unnecessary suites or coverage targets."
---

# Test design and evidence

Use this skill when a change needs test design, contract coverage, regression protection, or evidence review. Follow repository [AGENTS.md](../../../AGENTS.md) and the owning project's existing runner, naming, and test layout.

1. Identify the observable behavior, source requirement/defect/risk, and relevant boundary. Keep the owning repository's specification as the source of expected behavior.
2. Select the smallest useful test level. Cover the normal outcome and meaningful invalid, denied, failure, or boundary cases. For shared workflow interfaces, check caller inputs, outputs, permissions, and compatibility.
3. Arrange deterministic, isolated inputs and fixtures. Use Arrange-Act-Assert where it clarifies setup, action, and observable result. Avoid coupling tests through shared mutable state or external services.
4. Name tests in the runner's idiom and state the behavior and condition. Assert outcomes users or callers can observe, not incidental implementation details.
5. For a bug, preserve a regression case that fails for the defect and passes after the fix when the test environment permits. Do not weaken or skip a failing check without recording the reason and unresolved risk.
6. Report the command, revision, result, failures, and unrun checks. A test that was not executed is not passing evidence. Do not claim broad coverage from a narrow test.

Do not require a fixed coverage percentage, E2E tests for every change, a registry that mirrors every test, or a particular TDD ceremony. Keep test source and private fixtures in their owning private repository; this public repository uses only public or synthetic fixtures.

## ECC source

Adapted at a high level from the pinned ECC sources listed in [`third_party/ecc/manifest.json`](../../../third_party/ecc/manifest.json), especially `rules/common/testing.md`, `skills/test-collaboration/SKILL.md`, `skills/ai-regression-testing/SKILL.md`, `skills/tdd-workflow/SKILL.md`, and `skills/python-testing/SKILL.md`. ECC provides test behavior focus, isolation, AAA, and regression evidence; its blanket coverage and mandatory checkpoint rules are not adopted.
