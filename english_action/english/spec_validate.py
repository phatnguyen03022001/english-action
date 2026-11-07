"""Read-only structural validator for the English Spec Kit project override.

Exit 0: structural PASS; 1: confirmed structural failures; 2: bad invocation.
PASS never implies semantic correctness, approval or release authorization.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import html
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
LINK = re.compile(r"\[[^\]\n]+\]\((?P<dest><[^>]+>|[^)\s]+)(?:\s+['\"][^'\"]+['\"])?\)")
LINK_DEF = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s*(?P<dest><[^>]+>|\S+)")
HTML_ANCHOR = re.compile(r'<a\b[^>]*\b(?:id|name)=["\']([^"\']+)["\']', re.I)
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
DOC_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.md\Z")
BUSINESS_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
ADR_NAME = re.compile(r"\d{4}-[a-z0-9]+(?:-[a-z0-9]+)*\.md\Z")
TEMPLATE = Path(".specify/templates/overrides/spec-template.md")
REF = re.compile(r"(?<![\w-])(?P<kind>US|NFR|FR|SC)-?(?P<num>\d{1,3})(?P<scenario>\.\d+)?(?P<tail>(?:\s*[-–/]\s*\d+(?:\.\d+)?)*)")
CROSS = re.compile(r"(?<![\w-])F(?P<feature>\d{3}):(?P<kind>US|FR|NFR|SC)-?(?P<num>\d{1,3})(?![\w.])")
FIELDS = ("Feature ID", "Repository Branch", "Created", "Decision Status",
          "Product Increment", "Feature Scope", "Authoritative Input",
          "Human Owner Decisions", "Related Capability IDs",
          "Canonical Specialist Contracts")
MAX_FILES = 256  # Feature specs, retained for existing compatibility.
MAX_DOC_FILES = 512
MAX_SIZE = 2_000_000
MAX_TOTAL_BYTES = 16_000_000
MAX_OUTPUT = 60
# (Preferred LOC, review LOC); budgets are advisory, never acceptance gates.
SPEC_BUDGET = (250, 350)
DISCOVERY_BUDGET = (300, 450)
DOMAIN_BUDGET = (400, 600)
ADR_BUDGET = (200, 300)
TEMPLATE_BUDGET = (150, 200)


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
        self.warnings: list[Issue] = []
        self.inventory: set[str] = set()
        self.specs: dict[str, Spec] = {}
        self.anchor_cache: dict[Path, set[str]] = {}
        self.lines_cache: dict[Path, list[str]] = {}
        self.bytes_read = 0

    def emit(self, path: Path, line: int, code: str, message: str) -> None:
        try:
            name = path.relative_to(self.root).as_posix()
        except ValueError:
            name = "<outside-repository>"
        self.errors.append(Issue(name, line, code, message))

    def read(self, path: Path, line: int = 1) -> list[str] | None:
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root):
                self.emit(path, line, "PATH_ESCAPE", "Path escapes caller repository")
                return None
            if resolved in self.lines_cache:
                return self.lines_cache[resolved]
            size = path.stat().st_size
            if size > MAX_SIZE or self.bytes_read + size > MAX_TOTAL_BYTES:
                self.emit(path, line, "TOO_LARGE", "Documentation exceeds validator read bound")
                return None
            lines = path.read_text(encoding="utf-8").splitlines()
            self.bytes_read += size
            self.lines_cache[resolved] = lines
            return lines
        except (OSError, UnicodeError, ValueError) as exc:
            self.emit(path, line, "UNREADABLE", f"Cannot read document ({type(exc).__name__})")
            return None

    def get_inventory(self) -> None:
        modern = self.root / "knowledge/product/capability-map.md"
        legacy = self.root / "docs/product/capabilities.md"
        if modern.is_file() and legacy.is_file():
            self.emit(legacy, 1, "DUPLICATE_INVENTORY",
                      "Legacy and Knowledge Base capability inventories both present")
        # A migrated product directory must have its own canonical inventory.
        # Retain legacy support for existing callers without that directory.
        path = modern if (self.root / "knowledge/product").is_dir() else legacy
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
        """GitHub-style ATX anchors, repeated-heading suffixes and explicit HTML IDs."""
        counts: dict[str, int] = {}
        result: set[str] = set()
        fenced = False
        marker = ""
        for line in lines:
            fence = FENCE.match(line)
            if fence:
                run = fence.group(1)
                if not fenced:
                    fenced, marker = True, run
                elif run[0] == marker[0] and len(run) >= len(marker):
                    fenced = False
                continue
            if fenced:
                continue
            result.update(html.unescape(value) for value in HTML_ANCHOR.findall(line))
            match = HEADING.match(line)
            if match is None:
                continue
            title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", match.group(2))
            title = re.sub(r"<[^>]*>", "", title)
            title = html.unescape(title).replace(chr(96), "").replace("*", "")
            slug = re.sub(r"[^\w\-\s]", "", title.lower())
            slug = re.sub(r"\s", "-", slug)
            prior = counts.get(slug, 0)
            counts[slug] = prior + 1
            result.add(f"{slug}-{prior}" if prior else slug)
        return result

    def links(self, path: Path, lines: list[str], *, template: bool = False) -> None:
        # The canonical override is copied to specs/NNN-slug/spec.md. Only that
        # template gets the generated-spec link base; normal docs use their own.
        base = self.root / "specs/000-template-context" if template else path.parent
        fenced = False
        marker = ""
        comment = False
        for line_no, line in enumerate(lines, 1):
            fence = FENCE.match(line)
            if fence and not comment:
                run = fence.group(1)
                if not fenced:
                    fenced, marker = True, run
                elif run[0] == marker[0] and len(run) >= len(marker):
                    fenced = False
                continue
            if fenced:
                continue
            # Ignore Markdown in comments and inline code, not ordinary prose.
            source = ""
            remaining = line
            while remaining:
                if comment:
                    end = remaining.find("-->")
                    if end < 0:
                        break
                    remaining, comment = remaining[end + 3:], False
                else:
                    start = remaining.find("<!--")
                    if start < 0:
                        source += remaining
                        break
                    source += remaining[:start]
                    remaining, comment = remaining[start + 4:], True
            source = re.sub(r"(`+).*?\1", "", source)
            destinations = [match.group("dest") for match in LINK.finditer(source)]
            defined = LINK_DEF.match(source)
            if defined:
                destinations.append(defined.group("dest"))
            for dest in destinations:
                dest = html.unescape(dest.strip("<>"))
                try:
                    url = urlsplit(dest)
                    if url.scheme or url.netloc or dest.startswith("//"):
                        continue  # External references are never fetched.
                    rel, fragment = unquote(url.path), unquote(url.fragment)
                    if not rel and not fragment:
                        continue
                    target = (path if not rel else
                              self.root / rel.lstrip("/") if rel.startswith("/") else
                              base / rel)
                    resolved = target.resolve()
                    if not resolved.is_relative_to(self.root):
                        self.emit(path, line_no, "LINK_ESCAPE", "Local link escapes repository")
                    elif not resolved.exists():
                        self.emit(path, line_no, "BROKEN_LINK", "Local link target missing")
                    elif fragment and (not rel or resolved.suffix.lower() == ".md"):
                        if not resolved.is_file():
                            self.emit(path, line_no, "BROKEN_LINK", "Heading target is not a file")
                            continue
                        if resolved not in self.anchor_cache:
                            anchor_lines = self.read(resolved, line_no)
                            if anchor_lines is None:
                                continue
                            self.anchor_cache[resolved] = self.anchors(anchor_lines)
                        if fragment not in self.anchor_cache[resolved]:
                            self.emit(path, line_no, "BROKEN_ANCHOR", "Markdown heading anchor missing")
                except (OSError, ValueError):
                    self.emit(path, line_no, "INVALID_LINK", "Invalid local link target")

    def documentation(self) -> list[Path]:
        """Enumerate only owned docs; never traverse vendored Spec Kit templates."""
        paths = [self.root / name for name in ("PRODUCT.md", "README.md", "AGENTS.md")
                 if (self.root / name).is_file()]
        for folder in ("docs", "knowledge", "specs"):
            directory = self.root / folder
            if not directory.is_dir():
                continue
            if not directory.resolve().is_relative_to(self.root):
                self.emit(directory, 1, "PATH_ESCAPE", "Documentation directory escapes repository")
                continue
            for path in directory.rglob("*.md"):
                paths.append(path)
                if len(paths) > MAX_DOC_FILES:
                    self.emit(directory, 1, "DOC_LIMIT", "Too many owned Markdown documents")
                    return []
        override = self.root / TEMPLATE
        if override.is_file():
            paths.append(override)
        # Project-owned Spec Kit compatibility navigation, unlike vendored assets.
        compatibility = self.root / ".specify/memory/constitution.md"
        if compatibility.is_file():
            paths.append(compatibility)
        return sorted(set(paths))

    def document_conventions(self, path: Path, lines: list[str]) -> None:
        rel = path.relative_to(self.root)
        parts = rel.parts
        if parts[0] == "specs":
            if parts[-1] != "spec.md":
                return  # Other Spec Kit feature artifacts (plan/tasks) are not Gate 2 specs.
            if len(parts) != 3 or not FEATURE_DIR.fullmatch(parts[1]):
                self.emit(path, 1, "BAD_DOC_PATH", "Expected specs/NNN-feature-name/spec.md")
            budget = SPEC_BUDGET
        elif rel == TEMPLATE or rel == Path("docs/product/feature-spec-template.md"):
            budget = TEMPLATE_BUDGET
        elif parts[0] == "docs":
            if parts[1] == "adr":
                if len(parts) != 3 or not ADR_NAME.fullmatch(parts[-1]):
                    self.emit(path, 1, "BAD_DOC_PATH", "Expected docs/adr/NNNN-kebab-name.md")
                budget = ADR_BUDGET
            else:
                if not DOC_NAME.fullmatch(parts[-1]):
                    self.emit(path, 1, "BAD_DOC_PATH", "Expected lowercase kebab-case Markdown filename")
                budget = (DISCOVERY_BUDGET if parts[1] == "product"
                          or rel == Path("docs/what-why-audit.md") else DOMAIN_BUDGET)
        elif parts[0] == "knowledge":
            if rel == Path("knowledge/README.md"):
                return  # Navigation entrypoint, not a feature specification.
            if len(parts) < 3:
                self.emit(path, 1, "BAD_DOC_PATH", "Unexpected Knowledge Base document")
                return
            group = parts[1]
            if group == "features":
                if (len(parts) == 4 and parts[-1] == "README.md"
                        and BUSINESS_NAME.fullmatch(parts[2])):
                    return  # Optional domain navigation.
                if (len(parts) not in (5, 6) or parts[-1] != "spec.md"
                        or not all(BUSINESS_NAME.fullmatch(name) for name in parts[2:-1])):
                    self.emit(path, 1, "BAD_DOC_PATH",
                              "Expected knowledge/features/domain/feature[/subfeature]/spec.md")
                budget = DOMAIN_BUDGET
            elif group == "product" and len(parts) == 3 and DOC_NAME.fullmatch(parts[-1]):
                budget = DISCOVERY_BUDGET
            elif group == "architecture":
                if len(parts) == 4 and parts[2] == "decisions":
                    if not ADR_NAME.fullmatch(parts[-1]):
                        self.emit(path, 1, "BAD_DOC_PATH",
                                  "Expected knowledge/architecture/decisions/NNNN-name.md")
                    budget = ADR_BUDGET
                elif len(parts) == 3 and DOC_NAME.fullmatch(parts[-1]):
                    budget = DOMAIN_BUDGET
                else:
                    self.emit(path, 1, "BAD_DOC_PATH", "Unexpected architecture document")
                    budget = DOMAIN_BUDGET
            elif group == "operations" and len(parts) == 3 and DOC_NAME.fullmatch(parts[-1]):
                budget = DOMAIN_BUDGET
            else:
                self.emit(path, 1, "BAD_DOC_PATH", "Unexpected Knowledge Base document")
                budget = DOMAIN_BUDGET
        elif rel == Path("PRODUCT.md"):
            budget = DISCOVERY_BUDGET
        else:
            return  # README.md and AGENTS.md are recognized root navigation/governance.
        preferred, review = budget
        count = len(lines)
        if count > review:
            code, message = "LOC_REVIEW", f"{count} LOC exceeds review threshold {review}; human split/retention review recommended"
        elif count > preferred:
            code, message = "LOC_PREFERRED", f"{count} LOC exceeds preferred budget {preferred}"
        else:
            return
        self.warnings.append(Issue(rel.as_posix(), 1, code, message))

    def product_contract_locks(self) -> None:
        """Guard narrow canonical decisions, not arbitrary prose or historical quotes.

        This asserts declared owner markers and distinct exam scoring ownership;
        structural PASS cannot establish semantic consistency or release readiness.
        """
        path = self.root / "knowledge/product/decisions.md"
        if not path.is_file():
            return  # Synthetic/legacy Spec Kit callers have no product decision file.
        lines = self.read(path)
        if lines is None:
            return
        required = {
            "INV-02": ("only by `edge-tts`", "local ai agents",
                       "never synthesize speech", "static png/svg"),
            "INV-03": ("deterministic evaluation is the default",
                       "p0 runtime ai assessment is off",
                       "p1 ai assessment remains conditional"),
            "ASSESSMENT-LOCK-01": ("**forbidden**", "pre-generated question bank",
                                   "explicit submission", "ielts speaking",
                                   "after submission", "transcript is **not** evidence"),
        }
        for heading, markers in required.items():
            area = self.section(lines, heading, 3)
            if area is None:
                self.emit(path, 1, "CANONICAL_LOCK_MISSING",
                          f"Canonical {heading} decision section missing")
                continue
            start, stop = area
            body = "\n".join(lines[start:stop]).casefold()
            if any(marker not in body for marker in markers):
                self.emit(path, start + 1, "CANONICAL_LOCK_DRIFT",
                          f"Canonical {heading} markers changed; owner decision requires review")

        scoring = self.root / "knowledge/features/assessment/scoring/spec.md"
        if scoring.is_file():
            scoring_lines = self.read(scoring)
            if scoring_lines is not None:
                for heading in ("## 3. IELTS scoring", "## 4. TOEIC scoring",
                                "## 5. PTE Academic", "## 6. PTE Core"):
                    if not any(line.startswith(heading) for line in scoring_lines):
                        self.emit(scoring, 1, "SCORING_CONTRACT_MISSING",
                                  f"Distinct exam/version scoring section missing: {heading[3:]}")

    def run(self) -> list[Issue]:
        self.get_inventory()
        directory = self.root / "specs"
        if not directory.is_dir():
            self.emit(directory, 1, "NO_SPECS", "specs directory is required")
            return sorted(set(self.errors))
        if not directory.resolve().is_relative_to(self.root):
            self.emit(directory, 1, "PATH_ESCAPE", "Specification directory escapes repository")
            return sorted(set(self.errors))
        files: list[Path] = []
        for path in directory.rglob("spec.md"):
            files.append(path)
            if len(files) > MAX_FILES:
                self.emit(directory, 1, "SPEC_LIMIT", "Too many feature documents")
                return sorted(set(self.errors))
        files.sort()
        if not files:
            self.emit(directory, 1, "NO_SPECS", "No feature specifications")
        for file in files:
            self.load_spec(file)
        for spec in self.specs.values():
            self.traces(spec)
        for path in self.documentation():
            lines = self.read(path)
            if lines is None:
                continue
            self.document_conventions(path, lines)
            self.links(path, lines, template=path == self.root / TEMPLATE)
        self.product_contract_locks()
        self.warnings = sorted(set(self.warnings))
        return sorted(set(self.errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Caller repository (read-only)")
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print("spec-validate: --root must be an existing directory", file=sys.stderr)
        return 2
    validator = Validator(args.root.resolve())
    problems = validator.run()
    warnings = validator.warnings
    for item in problems[:MAX_OUTPUT]:
        print(f"{item.file}:{item.line}: ERROR {item.code}: {item.message}")
    for item in warnings[:max(0, MAX_OUTPUT - min(len(problems), MAX_OUTPUT))]:
        print(f"{item.file}:{item.line}: WARNING {item.code}: {item.message}")
    if len(problems) + len(warnings) > MAX_OUTPUT:
        print(f"... {len(problems) + len(warnings) - MAX_OUTPUT} further diagnostics suppressed")
    print(f"spec-validate: {'FAIL' if problems else 'PASS'}; {len(problems)} structural error(s); "
          f"{len(warnings)} advisory warning(s); semantic judgment and approval authenticity NOT VERIFIED.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
