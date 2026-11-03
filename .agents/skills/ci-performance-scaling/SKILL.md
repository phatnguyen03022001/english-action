---
name: ci-performance-scaling
description: "Use when measuring or changing CI duration, matrix jobs, sharding, parallelism, dependency caching, cancellation, timeouts, or artifact retention for reusable workflows."
---

# Measured CI performance and scale

Use this skill only when a workflow has a real repeated caller need and performance or scale is part of the change. Keep the implementation simple and preserve test correctness.

1. Establish a baseline for cold and warm startup, dependency installation, test duration, and end-to-end critical path. Compare comparable runs and record variance and runner context when available.
2. Identify genuinely independent suites or environment variants. Add only a small matrix or shard when measured critical-path savings exceed added startup, install, coordination, and caller billing costs.
3. Set `strategy.max-parallel` to a measured bound that balances duration, runner availability, and cost. Do not assume more jobs are faster; avoid sharding work that is small, stateful, or setup dominated.
4. Cache only supported dependency stores, keyed by lockfile and relevant environment. Exclude secrets, private data, generated outputs, and untrusted executable state.
5. Apply reasonable timeouts, caller-appropriate cancellation, and bounded artifact retention. Preserve failure signals and required diagnostics; do not hide checks or shorten retention below the debugging need.
6. Re-measure the end-to-end critical path after the change. Report commands or run references, before/after measurements, trade-offs, and any unverified caller behavior. If reliable measurements are unavailable, describe the proposal as unverified rather than a speedup.

The private caller owns runners and billing. Public validation must not execute private source to obtain public runner capacity.

## ECC source

Adapted at a high level from the pinned ECC sources listed in [`third_party/ecc/manifest.json`](../../../third_party/ecc/manifest.json), especially `rules/common/coding-style.md`, `rules/common/testing.md`, and `skills/verification-loop/SKILL.md`. KISS and evidence-based verification inform this guidance; no universal performance target or assumed benefit is imposed.
