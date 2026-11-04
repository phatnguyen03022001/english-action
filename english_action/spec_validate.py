"""Read-only structural validator for the English Spec Kit project override.

Exit 0: structural PASS; 1: confirmed structural failures; 2: bad invocation.
PASS never implies semantic correctness, approval or release authorization.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

FEATURE_DIR = re.compile(r"\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*\Z")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
META = re.compile(r"^\*\*([^*]+)\*\*:\s*(.*)$")
STORY = re.compile(r"^### User Story (\d+) - .+ \(Priority: (P[123])\)\s*$")
IDENT = re.compile(r"^-\s+\*\*((?:FR|NFR|SC)-(\d{3}))\*\*(?:\s*[:—-])")
CAP = re.compile(r"(?<![\w-])D\d{2}-\d{2}(?![\w-])")
LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\((?P<dest><[^>]+>|[^)\s]+)(?:\s+['\"][^'\"]+['\"])?\)")
REF = re.compile(r"(?<![\w-])(?P<kind>US|NFR|FR|SC)-?(?P<num>\d{1,3})(?P<scenario>\.\d+)?(?P<tail>(?:\s*[-–/]\s*\d+(?:\.\d+)?)*)")
CROSS = re.compile(r"(?<![\w-])F(?P<feature>\d{3}):(?P<kind>US|FR|NFR|SC)-?(?P<num>\d{1,3})(?![\w.])")
FIELDS = ("Feature ID", "Repository Branch", "Created", "Decision Status",
          "Product Increment", "Feature Scope", "Authoritative Input",
          "Human Owner Decisions", "Related Capability IDs",
          "Canonical Specialist Contracts")
MAX_FILES = 256
MAX_SIZE = 2_000_000
MAX_OUTPUT = 60


@dataclass(frozen=True, order=True)
class Issue:
    file: str
    line: int
    code: str
    message: str


@dataclass
class Spec:
    path: Path
    lines: list[str]
    stories: dict[int, int]
    scenario_counts: dict[int, int]
    identifiers: dict[str, set[str]]


class Validator:
    def __init__(self, root: Path):
        self.root = root
        self.errors: list[Issue] = []
        self.inventory: set[str] = set()
        self.specs: dict[str, Spec] = {}
        self.anchor_cache: dict[Path, set[str]] = {}

    def emit(self, path: Path, line: int, code: str, message: str) -> None:
        try:
            name = path.relative_to(self.root).as_posix()
        except ValueError:
            name = "<outside-repository>"
        self.errors.append(Issue(name, line, code, message))

    def read(self, path: Path, line: int = 1) -> list[str] | None:
        if not path.resolve().is_relative_to(self.root):
            self.emit(path, line, "PATH_ESCAPE", "Path escapes caller repository")
            return None
        try:
            if path.stat().st_size > MAX_SIZE:
                self.emit(path, line, "TOO_LARGE", "Document exceeds validator size bound")
                return None
            return path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            self.emit(path, line, "UNREADABLE", f"Cannot read document ({type(exc).__name__})")
            return None

    def get_inventory(self) -> None:
        path = self.root / "docs/product/capabilities.md"
        if not path.is_file():
            self.emit(path, 1, "NO_INVENTORY", "Canonical capability inventory missing")
            return
        for line in self.read(path) or []:
            match = re.match(r"^\|\s*(D\d{2}-\d{2})\s*\|", line)
            if match:
                self.inventory.add(match.group(1))
        if not self.inventory:
            self.emit(path, 1, "NO_INVENTORY_IDS", "Canonical capability inventory has no rows")

    @staticmethod
    def section(lines: list[str], prefix: str, level: int) -> tuple[int, int] | None:
        for start, line in enumerate(lines):
            match = HEADING.match(line)
            if match and len(match.group(1)) == level and match.group(2).startswith(prefix):
                stop = next((j for j in range(start + 1, len(lines))
                             if (m := HEADING.match(lines[j])) and len(m.group(1)) <= level),
                            len(lines))
                return start, stop
        return None

    def capabilities(self, path: Path, line: int, source: str) -> None:
        for ident in sorted(set(CAP.findall(source)) - self.inventory):
            self.emit(path, line, "UNKNOWN_CAPABILITY", f"Capability {ident} missing from canonical inventory")

    def load_spec(self, path: Path) -> None:
        lines = self.read(path)
        if lines is None:
            return
        directory = path.parent.name
        if (not FEATURE_DIR.fullmatch(directory) or path.name != "spec.md"
                or path.parent.parent != self.root / "specs"):
            self.emit(path, 1, "BAD_PATH", "Expected specs/NNN-feature-name/spec.md")
        feature = directory[:3]
        if not lines or not lines[0].startswith("# Feature Specification: "):
            self.emit(path, 1, "NO_TITLE", "Feature Specification H1 required")
        metadata: dict[str, tuple[str, int]] = {}
        for i, line in enumerate(lines, 1):
            match = META.match(line)
            if match and match.group(1) not in metadata:
                metadata[match.group(1)] = (match.group(2).strip(), i)
        for field in FIELDS:
            value, line = metadata.get(field, ("", 1))
            if not value or re.fullmatch(r"\[[^\]]+\]", value) or value in ("-", "TBD"):
                self.emit(path, line, "NO_METADATA", f"Required metadata: {field}")
        value, line = metadata.get("Feature ID", ("", 1))
        if not re.match(rf"^{re.escape(feature)}(?:\s|$)", value):
            self.emit(path, line, "FEATURE_ID", "Feature ID differs from directory")
        value, line = metadata.get("Decision Status", ("", 1))
        status = re.match(r"^\**(APPROVED|DEFERRED|PROPOSED)\b", value, re.I)
        if value and not status:
            self.emit(path, line, "BAD_STATUS", "Status must be APPROVED, DEFERRED or PROPOSED")
        if status and status.group(1).upper() == "APPROVED":
            evidence, where = metadata.get("Gate 2 Acceptance Provenance", ("", line))
            if not ("Human Owner" in evidence and
                    re.search(r"(?:\bTASK[- ]\d+\b|[0-9a-f]{40}|\]\([^)]+\))", evidence, re.I)):
                self.emit(path, where, "NO_APPROVAL_PROVENANCE",
                          "APPROVED needs Human Owner and identifiable acceptance record")
        value, line = metadata.get("Related Capability IDs", ("", 1))
        if not CAP.search(value):
            self.emit(path, line, "NO_CAPABILITY", "Related Capability IDs needs at least one ID")
        self.capabilities(path, line, value)
        for title in ("User Scenarios & Testing", "Requirements", "Success Criteria"):
            if self.section(lines, title, 2) is None:
                self.emit(path, 1, "NO_SECTION", f"Required section: {title}")
        for title in ("Functional Requirements", "Measurable Outcomes"):
            if self.section(lines, title, 3) is None:
                self.emit(path, 1, "NO_SECTION", f"Required subsection: {title}")

        stories: dict[int, int] = {}
        scenario_counts: dict[int, int] = {}
        area = self.section(lines, "User Scenarios & Testing", 2)
        if area:
            start, stop = area
            matches = [(i, STORY.match(lines[i])) for i in range(start + 1, stop)
                       if STORY.match(lines[i])]
            for i in range(start + 1, stop):
                if lines[i].startswith("### User Story ") and not STORY.match(lines[i]):
                    self.emit(path, i + 1, "BAD_STORY", "User Story needs number and P1/P2/P3 priority")
            for index, (i, match) in enumerate(matches):
                assert match is not None
                number = int(match.group(1))
                if number in stories:
                    self.emit(path, i + 1, "DUPLICATE_STORY", f"User Story {number} duplicated")
                stories[number] = i + 1
                end = matches[index + 1][0] if index + 1 < len(matches) else stop
                end = next((j for j in range(i + 1, end) if lines[j].startswith("### ")), end)
                block = lines[i + 1:end]
                if not any(re.match(r"^\*\*Independent Test\*\*:\s*\S", line) for line in block):
                    self.emit(path, i + 1, "NO_INDEPENDENT_TEST", f"User Story {number} needs Independent Test")
                if not any(line.startswith("**Acceptance Scenarios**:") for line in block):
                    self.emit(path, i + 1, "NO_ACCEPTANCE", f"User Story {number} needs Acceptance Scenarios")
                count = 0
                for j in range(i + 1, end):
                    if re.match(r"^\s*\d+\.\s+", lines[j]):
                        count += 1
                        if not re.search(r"\*\*Given\*\*.+\*\*When\*\*.+\*\*Then\*\*", lines[j]):
                            self.emit(path, j + 1, "BAD_SCENARIO", "Scenario needs Given/When/Then")
                scenario_counts[number] = count
                if not count:
                    self.emit(path, i + 1, "NO_SCENARIO", f"User Story {number} needs at least one scenario")
        if not stories:
            self.emit(path, 1, "NO_STORIES", "At least one prioritized User Story required")

        ids: dict[str, set[str]] = {"FR": set(), "NFR": set(), "SC": set()}
        for kind, title in (("FR", "Functional Requirements"),
                            ("NFR", "Non-Functional Requirements"),
                            ("SC", "Measurable Outcomes")):
            area = self.section(lines, title, 3)
            if area is None:
                continue
            start, stop = area
            last = 0
            for j in range(start + 1, stop):
                match = IDENT.match(lines[j])
                if match is None:
                    if re.match(r"^-\s+\*\*(?:FR|NFR|SC)-", lines[j]):
                        self.emit(path, j + 1, "BAD_IDENTIFIER", "Identifier must use PREFIX-NNN")
                    continue
                ident, digits = match.groups()
                prefix = ident.split("-")[0]
                if prefix != kind:
                    self.emit(path, j + 1, "WRONG_SECTION", f"{prefix} outside its section")
                    continue
                number = int(digits)
                if ident in ids[kind]:
                    self.emit(path, j + 1, "DUPLICATE_ID", f"Duplicate {ident}")
                elif number != last + 1:
                    self.emit(path, j + 1, "NONSEQUENTIAL_ID", f"{kind} numbering must start 001 and be contiguous")
                ids[kind].add(ident)
                last = number
                self.capabilities(path, j + 1, lines[j])
            if kind in ("FR", "SC") and not ids[kind]:
                self.emit(path, start + 1, "NO_IDENTIFIER", f"At least one {kind}-NNN required")
        if feature in self.specs:
            self.emit(path, 1, "DUPLICATE_FEATURE", f"Feature {feature} has multiple directories")
        self.specs[feature] = Spec(path, lines, stories, scenario_counts, ids)

    def traces(self, spec: Spec) -> None:
        for i, line in enumerate(spec.lines, 1):
            if not IDENT.match(line):
                continue
            clauses = [item for item in re.findall(r"\[([^\]]+)\]", line)
                       if re.search(r"(?:\bTrace\s*:|\bUS\d|\b(?:FR|NFR|SC)-)", item)]
            if not clauses:
                self.emit(spec.path, i, "NO_TRACE", "Requirement or outcome needs trace references")
                continue
            for clause in clauses:
                seen = False
                for cross in CROSS.finditer(clause):
                    seen = True
                    target = self.specs.get(cross.group("feature"))
                    if target is None:
                        self.emit(spec.path, i, "UNKNOWN_TRACE", "Cross-feature target not found")
                        continue
                    kind = cross.group("kind")
                    number = int(cross.group("num"))
                    exists = (number in target.stories if kind == "US"
                              else f"{kind}-{number:03d}" in target.identifiers[kind])
                    if not exists:
                        self.emit(spec.path, i, "UNKNOWN_TRACE", f"Cross-feature F{cross.group('feature')}:{kind}-{number:03d} absent")
                local = CROSS.sub("", clause)
                for ref in REF.finditer(local):
                    seen = True
                    kind = ref.group("kind")
                    base = int(ref.group("num"))
                    sub = int(ref.group("scenario")[1:]) if ref.group("scenario") else None
                    entries: list[tuple[int, int | None]] = [(base, sub)]
                    for separator, piece in re.findall(r"([-–/])\s*(\d+(?:\.\d+)?)", ref.group("tail")):
                        if "." in piece:
                            a, b = map(int, piece.split(".", 1))
                            entry = (a, b)
                        elif kind == "US" and sub is not None:
                            entry = (base, int(piece))
                        else:
                            entry = (int(piece), None)
                        prev = entries[-1]
                        if separator in "-–" and prev[0] == entry[0] and prev[1] is not None and entry[1] is not None and 0 <= entry[1] - prev[1] <= 300:
                            entries.extend((base, n) for n in range(prev[1] + 1, entry[1] + 1))
                        elif separator in "-–" and prev[1] is None and entry[1] is None and 0 <= entry[0] - prev[0] <= 300:
                            entries.extend((n, None) for n in range(prev[0] + 1, entry[0] + 1))
                        else:
                            entries.append(entry)
                    for n, scenario in entries:
                        if kind == "US":
                            if n not in spec.stories or (scenario is not None and (scenario < 1 or scenario > spec.scenario_counts[n])):
                                self.emit(spec.path, i, "UNKNOWN_TRACE", f"User Story {n} scenario reference absent")
                        elif f"{kind}-{n:03d}" not in spec.identifiers[kind]:
                            self.emit(spec.path, i, "UNKNOWN_TRACE", f"{kind}-{n:03d} not declared")
                if not seen:
                    self.emit(spec.path, i, "MALFORMED_TRACE", "No recognizable story/requirement in trace")

    @staticmethod
    def anchors(lines: list[str]) -> set[str]:
        counts: dict[str, int] = {}
        result: set[str] = set()
        for line in lines:
            match = HEADING.match(line)
            if match is None:
                continue
            title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", match.group(2))
            title = re.sub(r"<[^>]*>", "", title)
            slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
            prior = counts.get(slug, 0)
            counts[slug] = prior + 1
            result.add(f"{slug}-{prior}" if prior else slug)
        return result

    def links(self, spec: Spec) -> None:
        fence = False
        tick = chr(96)
        for line_no, line in enumerate(spec.lines, 1):
            if line.lstrip().startswith((tick * 3, "~~~")):
                fence = not fence
                continue
            if fence:
                continue
            source = re.sub(tick + "[^" + tick + "]+" + tick, "", line)
            for match in LINK.finditer(source):
                dest = match.group("dest").strip("<>")
                if not dest or dest.startswith(("http:", "https:", "mailto:", "tel:", "//")):
                    continue
                url = urlsplit(dest)
                if url.scheme or url.netloc:
                    continue
                link_path, fragment = unquote(url.path), unquote(url.fragment)
                target = (spec.path if not link_path else
                          self.root / link_path.lstrip("/") if link_path.startswith("/") else
                          spec.path.parent / link_path)
                if target.suffix.lower() != ".md":
                    continue
                if not target.resolve().is_relative_to(self.root):
                    self.emit(spec.path, line_no, "LINK_ESCAPE", "Relative link escapes repository")
                elif not target.is_file():
                    self.emit(spec.path, line_no, "BROKEN_LINK", "Relative Markdown file missing")
                elif fragment:
                    key = target.resolve()
                    if key not in self.anchor_cache:
                        self.anchor_cache[key] = self.anchors(self.read(target, line_no) or [])
                    if fragment not in self.anchor_cache[key]:
                        self.emit(spec.path, line_no, "BROKEN_ANCHOR", "Markdown heading anchor missing")

    def run(self) -> list[Issue]:
        self.get_inventory()
        directory = self.root / "specs"
        if not directory.is_dir():
            self.emit(directory, 1, "NO_SPECS", "specs directory is required")
            return sorted(set(self.errors))
        files = sorted(directory.rglob("spec.md"))
        if len(files) > MAX_FILES:
            self.emit(directory, 1, "SPEC_LIMIT", "Too many feature documents")
            return sorted(set(self.errors))
        if not files:
            self.emit(directory, 1, "NO_SPECS", "No feature specifications")
        for file in files:
            self.load_spec(file)
        for spec in self.specs.values():
            self.traces(spec)
            self.links(spec)
        return sorted(set(self.errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Caller repository (read-only)")
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print("spec-validate: --root must be an existing directory", file=sys.stderr)
        return 2
    problems = Validator(args.root.resolve()).run()
    for item in problems[:MAX_OUTPUT]:
        print(f"{item.file}:{item.line}: ERROR {item.code}: {item.message}")
    if len(problems) > MAX_OUTPUT:
        print(f"... {len(problems) - MAX_OUTPUT} further errors suppressed")
    print(f"spec-validate: {'FAIL' if problems else 'PASS'}; {len(problems)} structural error(s); "
          "semantic judgment and approval authenticity NOT VERIFIED.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
