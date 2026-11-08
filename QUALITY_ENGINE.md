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

## Pre-generated media verification

Spawn's existing local-media check also rejects malformed/truncated PCM WAV,
digital silence and unsafe static SVG even when the supplied hash matches.
Only WAV and a narrow static SVG profile have byte-level decoders here; other
encodings still need suitable decoding/playback review. Non-silent bytes do not
prove speech, pronunciation, intelligibility, factual image correctness or rights.

`media_binding(assets, source, configuration)` fingerprints the existing asset
identities/revisions/checksums, accessibility, exact source/content revisions,
objective/construct, text/key/order snapshot, resolved dependency IDs and actual
voice/language/render configuration. Unknown configuration remains null.
Storage/locator/rights labels are excluded from the alignment fingerprint.
Callers retain their existing private evidence records; no asset registry exists.

`verify_media(..., reviewed_binding=..., findings=..., rights_status=...,
withdrawn=...)` is a read-only HOLD/revalidation report. It reuses local integrity
checks, detects stale binding and scoped source/asset withdrawal, and records
reported defects. Callers must supply the complete resolved dependency closure;
the helper does not crawl sources, authenticate a review, discover revocation or
grant rights. Even a matching binding and a claimed "cleared" label leave actual
content, rights and editorial authority unverified. No positive publication or new
use is granted. Compatible bytes can be retained pending those gates, without a
generator call; changed dependencies require scoped revalidation, not batch
regeneration. The function never mutates assets, source, ledger or old evidence.

## Multi-speaker local audio authoring (AUDIO-MS-01)

`python -m english_action.content.multi_speaker --spec dialogue.json --output /local/media/dialogue-r2.wav --cache-dir /local/media/utterances` is an **authoring-only** entry point. It does not run in GitHub Actions, generate learner-runtime speech, write producer state, or authorize publication. An eligible local Agent must install `edge-tts` and FFmpeg independently; neither is a public Engine/CI dependency. Upstream: [rany2/edge-tts](https://github.com/rany2/edge-tts) and [FFmpeg](https://ffmpeg.org/documentation.html).

Input is one reviewed `source` (`sourceRef`, positive `sourceRevision` and `contentRevision`, objective, construct, dependency IDs and **authoritative full `transcript`**), a 2–5-entry `speakers` map (`voice`, `locale`), 2–40 ordered `utterances` (`id`, contiguous `order` from 1, `speaker`, exact `text`, `pauseMs` 0–3000), and `settings` (`edgeTtsVersion`, `rate`, `pitch`, `volume`). The full reviewed transcript must equal the ordered utterance texts exactly; missing or unreviewed changed turns fail closed. A speaker's voice cannot change mid-script. Voice existence, provider availability and correct locale/pronunciation must still be checked at the actual authoring session; recording a version string is not automatic provider attestation. Utterances are capped at 500 characters each; full script at 8,000 characters and output at 300 s/32 MiB. Split longer material at reviewed natural turn boundaries; do not silently slice text.

The module synthesizes each missing utterance independently with **local `edge-tts` only**, decodes its MP3 with FFmpeg to 24-kHz mono 16-bit PCM WAV, checks the actual WAV bytes and signal, then concatenates nonoverlapping turns with exact sample-counted pauses. This deliberately uses sequential PCM append rather than mixing or MP3 packet concat: differing MP3 encoder delay/format cannot silently alter turn order. A >12-dB RMS gap between utterances is rejected for manual correction rather than automatically altering a speaker's voice; this is not an independent loudness/perceptual review.

A per-utterance cache key binds exact text, speaker/voice/locale, synthesis settings and normalized format. Its WAV digest and original MP3 digest are recorded; corrupt, incomplete or stale intermediate files fail closed. Changed text or voice regenerates only affected segments; source revision changes invalidate the assembled **`media_binding`** even if reusable utterance bytes are unchanged. The output WAV and adjacent JSON evidence sidecar are immutable: conflicting replacements are rejected and writes use temporary files. A failed segment/assembly cannot overwrite a previous valid output; operators use a new output filename for each materially changed revision.

The JSON sidecar carries the existing asset fields (identity, revision, checksum, origin, local locator), exact source/transcript and turn map, per-turn timing in samples, segment hashes and media binding. Handoff uses existing `verify_media`/private Content Factory review, never a new registry/schema, never automatic promotion. These tests use **synthetic tones and optional synthetic FFmpeg transcoding**: ordered assembly, resource limits, hashes and draft denial can pass while real speech pronunciation, intelligibility, natural pauses, timing of spoken words and authenticity remain **NOT_VERIFIED** until a reviewed local pilot.

## Two independent safety gates

**Technical engine use:** TASK 07 switched English-content's nine then-npm schema/candidate entrypoints to an immutable public Engine SHA after positive/negative CI, stress, private integration and rollback checks. The [subsequent Python-only consumer cutover](https://github.com/phatnguyen03022001/english-content/commit/79e7e1c74d98f6a4b57c9d9b75f3bb25d06048b5) preserved historical QA in Python and retired Node implementation/tests; original Node/parity evidence and retired source remain in Git history. There is no active consumer Node fallback, and no public capability/API is removed by that consumer migration. Never use the public workflow to process private source or pay for a private consumer run.

**Real publication eligibility:** This CLI has no independently authenticated owner-appointment, evidence and rights provider. `promote` and positive publication eligibility are therefore deliberately blocked, even when isolated synthetic test callbacks establish a valid domain claim. The `QUALITY_PARITY_NOT_VERIFIED` guard is retained as a conservative production-publication lock, **not** as a claim that private technical test execution did not occur. Neither a passed schema check nor a technically migrated Engine authorizes actual content publication. Do not remove this guard without independent authority integration, a separately verified security boundary and Human Owner approval.

Source code and private data have distinct owners: `english-action` publishes only reusable public-safe Python code and tests; `english-content` owns schemas, datasets, evidence, editorial policy, publication metadata and confidential fixtures.
