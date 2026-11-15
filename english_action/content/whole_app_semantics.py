"""Deterministic DATA-05 semantic checks; never infer official scoring or media QA."""
from __future__ import annotations


def _duplicate(sequence: list) -> bool:
    return len(sequence) != len(set(sequence))


def _response_check(record, state) -> None:
    answer = record["answer"]
    response = record["response"]
    kind = response["interactionKind"]
    if answer["kind"] != kind or answer.get("mode") != response["variant"]:
        state.add("ANSWER_RESPONSE_MISMATCH", record, "answer/mode")
    if kind == "choice":
        options = answer["options"]
        keys = answer.get("correctKeys", ())
        if any(k not in options for k in keys):
            state.add("ANSWER_OPTION_MISSING", record, "answer/correctKeys")
        if _duplicate(list(options.values())):
            state.add("ANSWER_OPTION_DUPLICATE", record, "answer/options")
        if len(keys) < answer.get("minSelections", 1) or len(keys) > answer.get("maxSelections", 1):
            state.add("ANSWER_CARDINALITY", record, "answer/correctKeys")
        if answer.get("mode") == "position_set":
            positions = answer.get("positions", ())
            if len(positions) != len(keys) or {p["correctOptionKey"] for p in positions} != set(keys):
                state.add("ANSWER_POSITION_BINDING", record, "answer/positions")
    elif kind == "fill_blank":
        options = answer.get("options", {})
        blanks = answer["blanks"]
        if not blanks:
            state.add("ANSWER_BLANK_MISSING", record, "answer/blanks")
        for key, blank in blanks.items():
            for option in blank.get("acceptedOptionIds", ()):
                if option not in options:
                    state.add("ANSWER_OPTION_MISSING", record, f"answer/blanks/{key}")
        if answer["mode"] in ("dropdown", "drag_drop"):
            if answer.get("optionReuse") == "forbidden":
                accepted = [key for blank in blanks.values() for key in blank.get("acceptedOptionIds", ())]
                if _duplicate(accepted):
                    state.add("ANSWER_REUSE_INVALID", record, "answer/blanks")
        elif answer["mode"] == "typed" and options:
            state.add("ANSWER_MODE_MISMATCH", record, "answer/options")
        for key, blank in blanks.items():
            values = blank.get("acceptedValues", ())
            if _duplicate(list(values)):
                state.add("ANSWER_ALTERNATIVE_DUPLICATE", record, f"answer/blanks/{key}")
        item = state.index.get(("foundation_item", record["itemRef"]["id"], record["itemRef"]["revision"])) or (
            state.index.get(("exam_item", record["itemRef"]["id"], record["itemRef"]["revision"])))
        if item:
            count = item.get("response", {}).get("constraints", {}).get("blankCount")
            if count is not None and count != len(blanks):
                state.add("ANSWER_BLANK_COUNT", record, "answer/blanks")
    elif kind == "matching_ordering":
        tokens = answer["tokens"]
        for token in answer.get("sequence", ()):
            if token not in tokens:
                state.add("ANSWER_TOKEN_MISSING", record, "answer/sequence")
        if answer.get("relationMode") == "complete_order":
            sequence = answer.get("sequence", ())
            if set(sequence) != set(tokens) or _duplicate(list(sequence)):
                state.add("ANSWER_ORDER_INCOMPLETE", record, "answer/sequence")
        for relation in answer.get("relations", ()):
            if relation["from"] not in tokens or relation["to"] not in tokens:
                state.add("ANSWER_TOKEN_MISSING", record, "answer/relations")
    elif kind == "speech_response":
        if answer.get("evidenceRequired") != "audio_recording":
            state.add("SPEECH_EVIDENCE_INVALID", record, "answer/evidenceRequired")
    elif kind in ("extended_text", "short_text"):
        # Structure is constrained by schema; open semantic equivalence is unverified.
        if kind == "extended_text" and not answer.get("checklist"):
            state.add("REVIEW_CHECKLIST_MISSING", record, "answer/checklist")


def _assessment_check(record, state) -> None:
    kind = record["response"]["interactionKind"]
    dimensions = record.get("dimensions", ())
    if kind == "speech_response":
        acoustic_terms = ("pronunciation", "fluency", "prosody", "intonation", "timing")
        for dimension in dimensions:
            if any(term in dimension["name"].lower() for term in acoustic_terms):
                if dimension["status"] == "supported" and not (
                        {"audio_recording", "acoustic_measurement"} &
                        set(dimension.get("acceptedEvidence", ()))):
                    state.add("ACOUSTIC_EVIDENCE_MISSING", record, "dimensions")
                elif dimension["status"] == "supported" and state.eligibility:
                    state.add("ACOUSTIC_VALIDATION_UNAVAILABLE", record, "dimensions", blocked=True)
    if record["evaluationMode"] == "deterministic":
        if any(d["status"] != "supported" for d in dimensions):
            state.add("ASSESSMENT_UNSUPPORTED_DIMENSION", record, "dimensions")
    if record["evaluationMode"] == "conditional_future" and state.eligibility:
        state.add("ASSESSMENT_UNAVAILABLE", record, "evaluationMode", blocked=True)


def _positions(record, state, rows, path, *, field="position", label=None):
    numbers = [r[field] for r in rows]
    if len(numbers) != len(set(numbers)) or sorted(numbers) != list(range(1, len(numbers) + 1)):
        state.add("ORDER_INVALID", record, path)
    if label and _duplicate([r[label] for r in rows]):
        state.add("DUPLICATE_SECTION", record, path)


def _exam_check(record, state) -> None:
    kind = record["kind"]
    if kind == "exam_version":
        _positions(record, state, record.get("sections", ()), "sections", label="key")
        return
    if kind == "task_profile":
        if record.get("policyStatus") == "unresolved" and state.eligibility:
            state.add("POLICY_UNVERIFIED", record, "policyStatus", blocked=True)
        grouping = record.get("grouping", {})
        if grouping.get("maximumItems", 0) and (
                grouping["maximumItems"] < grouping.get("minimumItems", 1)):
            state.add("GROUP_COUNT_INVALID", record, "grouping")
        return
    if kind == "blueprint":
        _positions(record, state, record.get("sections", ()), "sections", label="section")
        if record["completeness"] == "evidence_backed" and state.eligibility:
            # Declared references alone do not authenticate a provider composition.
            if not record.get("ruleRefs"):
                state.add("BLUEPRINT_POLICY_UNVERIFIED", record, "ruleRefs", blocked=True)
        return
    if kind == "exam_form":
        _positions(record, state, record.get("sections", ()), "sections", label="section")
        for section in record["sections"]:
            for group in section["groups"]:
                refs = [r["itemRef"]["id"] for r in group["items"]]
                if _duplicate(refs):
                    state.add("FORM_DUPLICATE_ITEM", record, "sections/groups/items")
        if record.get("claim") != "illustrative_only" and state.eligibility:
            state.add("FORM_SCORING_UNVERIFIED", record, "claim", blocked=True)


def _audio_check(record, state) -> None:
    turns = record["turns"]
    speakers = {s["key"] for s in record["speakers"]}
    if len(speakers) != len(record["speakers"]):
        state.add("MEDIA_SPEAKER_DUPLICATE", record, "speakers")
    _positions(record, state, turns, "turns")
    for turn in turns:
        if turn["speakerKey"] not in speakers:
            state.add("MEDIA_SPEAKER_MISSING", record, "turns")
    normalized = " ".join(record.get("reviewedText", "").split())
    expected = " ".join(" ".join(t["text"].split()) for t in sorted(turns, key=lambda x: x["position"]))
    if expected != normalized:
        state.add("MEDIA_REVIEWED_TEXT_MISMATCH", record, "reviewedText")
    transcript = record["transcript"]
    offset = 0
    for turn in sorted(turns, key=lambda x: x["position"]):
        found = transcript.find(turn["text"], offset)
        if found < 0:
            state.add("MEDIA_TRANSCRIPT_MISMATCH", record, "transcript")
            break
        offset = found + len(turn["text"])
    positions = {t["position"] for t in turns}
    for pause in record.get("pauses", ()):
        if pause["afterTurn"] not in positions:
            state.add("MEDIA_PAUSE_INVALID", record, "pauses")


def _image_check(record, state) -> None:
    facts = record["sourceFacts"]
    objects = {obj["key"] for obj in record["requiredObjects"]}
    if len(objects) != len(record["requiredObjects"]):
        state.add("MEDIA_OBJECT_DUPLICATE", record, "requiredObjects")
    for relation in record.get("spatialRelations", ()):
        if relation["fromKey"] not in objects or relation["toKey"] not in objects:
            state.add("MEDIA_SPATIAL_REFERENCE", record, "spatialRelations")
    known_labels = {r["key"] for r in record.get("labels", ())}
    for region in record.get("answerRegions", ()):
        if region["key"] not in objects and region["key"] not in known_labels:
            state.add("MEDIA_ANSWER_REGION_MISSING", record, "answerRegions")
    if facts["kind"] == "chart_facts":
        values = [v for series in facts["series"] for v in series["values"]]
        if any(len(series["values"]) != len(facts["categories"]) for series in facts["series"]):
            state.add("MEDIA_CHART_SHAPE", record, "sourceFacts/series")
        for scale in record.get("scaleConstraints", ()):
            if scale["minimum"] >= scale["maximum"]:
                state.add("MEDIA_CHART_SCALE", record, "scaleConstraints")
            if scale["axis"] == "y" and any(
                    v < scale["minimum"] or v > scale["maximum"] for v in values):
                state.add("MEDIA_CHART_SCALE", record, "scaleConstraints")


def _lifecycle_check(record, state) -> None:
    lifecycle = record.get("lifecycle")
    if not lifecycle or lifecycle["state"] != "superseded":
        return
    reference = lifecycle["supersededBy"]
    if reference["targetKind"] != record["kind"]:
        state.add("SUPERSESSION_TYPE", record, "lifecycle/supersededBy")
    if reference["id"] == record["id"] and reference["revision"] <= record["revision"]:
        state.add("SUPERSESSION_REVISION", record, "lifecycle/supersededBy")
    state.link(record, reference, "lifecycle/supersededBy", record_edge=False)


def validate_semantics(record, state) -> None:
    """Cross-field checks only; uncertainty is not converted into a score."""
    kind = record["kind"]
    if kind == "answer_spec":
        _response_check(record, state)
    elif kind == "assessment_metadata":
        _assessment_check(record, state)
    elif kind in ("exam_version", "task_profile", "blueprint", "exam_form"):
        _exam_check(record, state)
    elif kind == "audio_source":
        _audio_check(record, state)
    elif kind == "image_source":
        _image_check(record, state)
    if kind in ("stimulus", "foundation_item", "exam_item"):
        _lifecycle_check(record, state)
