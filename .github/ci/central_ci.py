"""Bounded exact-SHA CI; GitHub commit statuses are the recovery ledger.

Only repository/SHA/pins and redacted outcomes may leave runner scratch.
No source writes, publication, cache, artifact upload or latest-only cursor.
"""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "targets.json").read_text())
SHA = re.compile(r"[0-9a-f]{40}")
CONTEXT = "central-ci/"


class Blocked(Exception):
    """Setup, permission or incomplete history; never a passing check."""


def valid_sha(value):
    if not SHA.fullmatch(value):
        raise Blocked("Full immutable lowercase SHA required")
    return value


class GitHub:
    def __init__(self, token):
        self.token = token

    def request(self, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            "https://api.github.com" + path, data=body,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Method, allowlisted repository endpoint and HTTP code are metadata.
            # Response bodies can contain private data and are never surfaced.
            method = "GET" if payload is None else "POST"
            endpoint = path.split("?", 1)[0]
            raise Blocked(f"GitHub API {method} {endpoint}: HTTP {error.code}; response redacted") from None
        except (urllib.error.URLError, ValueError):
            # Never print API response bodies, private commit messages or tokens.
            raise Blocked("GitHub API unavailable or permission denied") from None

    def pages(self, path):
        separator = "&" if "?" in path else "?"
        for page in range(1, 1001):
            result = self.request(f"{path}{separator}per_page=100&page={page}")
            yield result
            items = result["commits"] if isinstance(result, dict) else result
            if len(items) < 100:
                return
        raise Blocked("History pagination safety limit reached; no results discarded")

    def history(self, repo, target):
        prefix = f"/repos/{CONFIG['owner']}/{repo}"
        baseline = valid_sha(target["baseline"])
        head = valid_sha(self.request(prefix + "/commits/main")["sha"])
        # Compare against one immutable snapshot, never a moving main.
        commits = [baseline]
        for result in self.pages(prefix + f"/compare/{baseline}...{head}"):
            if result["status"] not in {"ahead", "identical"}:
                raise Blocked("Migration baseline is no longer an ancestor of main")
            commits.extend(valid_sha(c["sha"]) for c in result["commits"])
        if commits[-1] != head:
            raise Blocked("Incomplete main history; refusing to skip commits")
        return list(dict.fromkeys(commits))

    def statuses(self, repo, sha):
        path = f"/repos/{CONFIG['owner']}/{repo}/commits/{sha}/statuses"
        return [item for page in self.pages(path) for item in page]

    def status(self, repo, sha, suite, outcome, pin):
        states = {"PASS": "success", "FAIL": "failure",
                  "BLOCKED": "error", "RUNNING": "pending"}
        workflow = valid_sha(os.environ["GITHUB_SHA"])
        run = os.environ["GITHUB_RUN_ID"]
        url = f"https://github.com/{CONFIG['owner']}/english-action/actions/runs/{run}"
        return self.request(f"/repos/{CONFIG['owner']}/{repo}/statuses/{sha}", {
            "state": states[outcome], "context": CONTEXT + suite,
            "description": f"{outcome} wf={workflow} pin={pin}",
            "target_url": url,
        })


def latest_by_context(statuses):
    latest = {}
    # GitHub returns newest first; never fall back behind a pending/untrusted run.
    for item in statuses:
        context = item.get("context")
        if context not in latest:
            latest[context] = item
    return latest


def trusted_outcome(item, pin, creator, workflow_sha=None):
    if not isinstance(item, dict) or (item.get("creator") or {}).get("login") != creator:
        return None
    match = re.fullmatch(
        r"(PASS|FAIL|BLOCKED|RUNNING) wf=([0-9a-f]{40}) pin=([0-9a-f]{40})",
        item.get("description") or "",
    )
    if not match:
        return None
    outcome, workflow, actual_pin = match.groups()
    if actual_pin != pin or (workflow_sha is not None and workflow != workflow_sha):
        return None
    if item.get("state") != {"PASS": "success", "FAIL": "failure",
                              "BLOCKED": "error", "RUNNING": "pending"}[outcome]:
        return None
    url = item.get("target_url") or ""
    if not re.fullmatch(
        rf"https://github\.com/{re.escape(CONFIG['owner'])}/english-action/actions/runs/[1-9][0-9]*",
        url,
    ):
        return None
    return outcome


def completed_suites(statuses, target, creator, workflow_sha=None):
    latest = latest_by_context(statuses)
    return {suite for suite in target["suites"]
            if trusted_outcome(latest.get(CONTEXT + suite), target["pin"],
                               creator, workflow_sha) in {"PASS", "FAIL"}}


def current_head_gate(api, repo, head, target, creator, workflow_sha):
    latest = latest_by_context(api.statuses(repo, head))
    outcomes = {suite: trusted_outcome(latest.get(CONTEXT + suite),
                                      target["pin"], creator, workflow_sha)
                for suite in target["suites"]}
    if any(value not in {"PASS", "FAIL"} for value in outcomes.values()):
        result = "BLOCKED"
    elif any(value == "FAIL" for value in outcomes.values()):
        result = "FAIL"
    else:
        result = "PASS"
    print(json.dumps({"kind": "CURRENT_HEAD_GATE",
                      "repository": f"{CONFIG['owner']}/{repo}",
                      "source_sha": head, "workflow_sha": workflow_sha,
                      "pin": target["pin"], "outcome": result,
                      "suites": {suite: outcome or "UNTRUSTED_OR_MISSING"
                                 for suite, outcome in outcomes.items()}},
                     sort_keys=True), flush=True)
    return result


def historical_audit(api, repo, target, history, creator, workflow_sha):
    failures = []
    blocked = incomplete = 0
    for sha in history[:-1]:
        latest = latest_by_context(api.statuses(repo, sha))
        for suite in target["suites"]:
            item = latest.get(CONTEXT + suite)
            outcome = trusted_outcome(item, target["pin"], creator)
            if outcome == "FAIL":
                failures.append((sha, suite, item))
            elif outcome == "BLOCKED":
                blocked += 1
            elif outcome != "PASS":
                incomplete += 1
    for sha, suite, item in failures:
        print(json.dumps({"kind": "HISTORICAL_TEST_FAILURE",
                          "repository": f"{CONFIG['owner']}/{repo}",
                          "source_sha": sha, "suite": suite,
                          "pin": target["pin"],
                          "workflow_sha": item["description"].split()[1][3:],
                          "diagnostic_run": item["target_url"], "outcome": "FAIL"},
                         sort_keys=True), flush=True)
    result = ("BLOCKED" if blocked else "INCOMPLETE" if incomplete else
              "FAIL" if failures else "PASS")
    print(json.dumps({"kind": "HISTORICAL_COMPATIBILITY_AUDIT",
                      "repository": f"{CONFIG['owner']}/{repo}",
                      "workflow_sha": workflow_sha, "pin": target["pin"],
                      "outcome": result, "failed_suites": len(failures),
                      "blocked_suites": blocked, "incomplete_suites": incomplete},
                     sort_keys=True), flush=True)
    return result


def select_work(api, repo, target, manual_sha, creator, *, history=None, workflow_sha=None):
    history = api.history(repo, target) if history is None else history
    if manual_sha:
        valid_sha(manual_sha)
        if manual_sha not in history:
            raise Blocked("Manual SHA must belong to main at/after migration baseline")
        return [(manual_sha, target["suites"])]
    # Verify the latest source revision promptly; still catch up every older
    # immutable revision (oldest first) in the remaining bounded slots.
    latest = history[-1]
    pending = [suite for suite in target["suites"]
               if suite not in completed_suites(api.statuses(repo, latest), target, creator,
                                               workflow_sha)]
    work = [(latest, pending)] if pending else []
    for sha in history[:-1]:
        done = completed_suites(api.statuses(repo, sha), target, creator)
        missing = [suite for suite in target["suites"] if suite not in done]
        if missing:
            work.append((sha, missing))
            if len(work) == CONFIG["batch_size"]:
                break
    return work


def lock_revisions(api, repo, target, *, history=None):
    """Only enforce newer Owner decisions on descendants of their adoption SHA."""
    baseline = target.get("lock_from")
    if baseline is None:
        return frozenset()
    valid_sha(baseline)
    history = api.history(repo, target) if history is None else history
    if baseline not in history:
        raise Blocked("Contract lock adoption SHA absent from verified main history")
    return frozenset(history[history.index(baseline):])


def clean_env():
    # Private commands have no API credentials, and cannot persist git auth.
    return {key: value for key, value in os.environ.items()
            if not any(word in key.upper() for word in
                       ("TOKEN", "PRIVATE_KEY", "SECRET", "PASSWORD"))
            and not key.startswith("GIT_CONFIG_")
            and key not in {"GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_PATH",
                            "GITHUB_STEP_SUMMARY", "GITHUB_STATE"}}


def command(args, cwd, env=None, timeout=300, *, test_counts=None):
    try:
        # Only unittest's numeric terminal summary may leave private scratch.
        # All stdout and the raw diagnostic bytes remain redacted.
        with tempfile.TemporaryFile() as diagnostics:
            result = subprocess.run(args, cwd=cwd, env=clean_env() if env is None else env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=(diagnostics if test_counts is not None
                                            else subprocess.DEVNULL),
                                    timeout=timeout, check=False)
            if test_counts is not None:
                diagnostics.seek(0, os.SEEK_END)
                diagnostics.seek(max(0, diagnostics.tell() - 4096))
                tail = diagnostics.read()
                totals = re.findall(rb"(?m)^Ran ([0-9]{1,12}) tests? in [0-9.]+s\r?$", tail)
                if not totals:
                    raise Blocked("Test discovery summary unavailable; details redacted")
                skipped = re.findall(rb"skipped=([0-9]{1,12})", tail)
                failures = re.findall(rb"failures=([0-9]{1,12})", tail)
                errors = re.findall(rb"errors=([0-9]{1,12})", tail)
                test_counts.append({"tests": int(totals[-1]),
                                    "skipped": int(skipped[-1]) if skipped else 0,
                                    "failures": int(failures[-1]) if failures else 0,
                                    "errors": int(errors[-1]) if errors else 0})
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("Command unavailable or timed out; details redacted") from None


def checkout(repo, sha, path, token=None):
    path.mkdir()
    env = clean_env()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        encoded = base64.b64encode(("x-access-token:" + token).encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
                   GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + encoded)
    for args in (["git", "init", "--quiet"],
                 ["git", "remote", "add", "origin", f"https://github.com/{CONFIG['owner']}/{repo}.git"],
                 ["git", "fetch", "--quiet", "--depth=1", "origin", sha],
                 ["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"]):
        if not command(args, path, env=env):
            raise Blocked("Exact-SHA checkout failed; details redacted")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path,
                                     env=clean_env(), text=True).strip()
    if actual != sha:
        raise Blocked("Checkout identity mismatch")


def verify(repo, sha, target, suite, token):
    with tempfile.TemporaryDirectory(prefix="central-ci-", dir=os.environ["RUNNER_TEMP"]) as tmp:
        root = Path(tmp)
        source, engine = root / "source", root / "engine"
        checkout(repo, sha, source, token)
        checkout("english-action", target["pin"], engine)
        # A venv for every suite avoids pin/dependency bleed between consumers.
        python = root / "venv/bin/python"
        for args in ([sys.executable, "-m", "venv", str(root / "venv")],
                     [str(python), "-m", "pip", "install", "--disable-pip-version-check",
                      "--no-input", str(engine)]):
            if not command(args, root):
                raise Blocked("Pinned tool installation failed; details redacted")
        env = clean_env()
        env["RUNNER_TEMP"] = str(root)
        if suite == "spec-validation":
            spec_command = [str(python), "-m", "english_action.english.spec_validate",
                            "--root", str(source)]
            if target.get("product_lock_active"):
                spec_command.append("--product-locks")
            commands = [spec_command]
        elif suite == "content-quality":
            commands = [
                [str(python), "-m", "english_action.content.cli", "schemas-check", "--schemas", "schemas"],
                [str(python), "-m", "unittest", "discover", "-s", str(engine / "tests"), "-p", "test_*.py", "-v"],
                [str(python), "-m", "unittest", "discover", "-s", "tests/integration", "-p", "test_*.py", "-v"],
            ]
        elif suite == "spawn-draft":
            commands = [
                [str(python), "-m", "unittest", "discover", "-s", str(engine / "tests"), "-p", "test_spawn.py", "-v"],
                ["bash", str(ROOT / "spawn-gate.sh")],
            ]
            env["PATH"] = str(python.parent) + os.pathsep + env["PATH"]
        else:
            raise Blocked("Unknown suite")
        # Execute every check, preserving failure even if a subsequent check passes.
        counts = []
        outcomes = [command(args, source, env=env,
                            test_counts=counts if args[1:3] == ["-m", "unittest"] else None)
                    for args in commands]
        if counts:
            print(json.dumps({"repository": f"{CONFIG['owner']}/{repo}",
                              "source_sha": sha, "workflow_sha": os.environ["GITHUB_SHA"],
                              "pin": target["pin"], "suite": suite,
                              "check_outcomes": ["PASS" if ok else "FAIL" for ok in outcomes],
                              "test_counts": counts}, sort_keys=True), flush=True)
        return "PASS" if all(outcomes) else "FAIL"


def record(repo, sha, suite, outcome, pin):
    # Deliberately metadata only; no private stdout/stderr, paths or test names.
    evidence = {"repository": f"{CONFIG['owner']}/{repo}", "source_sha": sha,
                "workflow_sha": os.environ["GITHUB_SHA"], "pin": pin,
                "suite": suite, "outcome": outcome}
    line = json.dumps(evidence, sort_keys=True)
    print(line, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as stream:
            stream.write(line + "\n\n")


def run(api, repo, target, work, *, head_sha=None, history=None):
    bad = False
    history = api.history(repo, target) if history is None else history
    head_sha = history[-1] if head_sha is None else head_sha
    locked = lock_revisions(api, repo, target, history=history)
    for sha, suites in work:
        blocked = False
        for suite in suites:
            try:
                # This also proves statuses:write before executing private code.
                api.status(repo, sha, suite, "RUNNING", target["pin"])
                configured = {**target, "product_lock_active": sha in locked}
                outcome = verify(repo, sha, configured, suite, api.token)
            except Blocked as error:
                print(f"BLOCKED: {repo} {sha} {suite}; {error}", flush=True)
                outcome = "BLOCKED"
            try:
                api.status(repo, sha, suite, outcome, target["pin"])
            except Blocked:
                outcome = "BLOCKED"
            record(repo, sha, suite, outcome, target["pin"])
            bad |= outcome == "BLOCKED" or (sha == head_sha and outcome != "PASS")
            blocked |= outcome == "BLOCKED"
        if blocked:
            # No advancing past incomplete permission/setup/results in this batch.
            break
    return bad


def main():
    token = os.environ.get("CENTRAL_CI_TOKEN", "")
    if not token:
        raise Blocked("Configure the english Actions secret in english-action")
    repository = os.environ.get("SOURCE_REPOSITORY", "all") or "all"
    manual_sha = os.environ.get("SOURCE_SHA", "")
    if repository not in {"all", *CONFIG["targets"]}:
        raise Blocked("Unsupported repository")
    if manual_sha and repository == "all":
        raise Blocked("Manual SHA requires one repository")
    api = GitHub(token)
    creator = api.request("/user").get("login")
    if not isinstance(creator, str) or not creator:
        raise Blocked("Token identity could not be verified")
    workflow_sha = valid_sha(os.environ.get("GITHUB_SHA", ""))
    bad = False
    snapshots = {}
    for repo, target in CONFIG["targets"].items():
        if repository not in {"all", repo}:
            continue
        try:
            valid_sha(target["pin"])
            history = api.history(repo, target)
            head = history[-1]
            snapshots[repo] = head
            work = select_work(api, repo, target, manual_sha, creator,
                               history=history, workflow_sha=workflow_sha)
            bad |= run(api, repo, target, work, head_sha=head, history=history)
            # This gate is independent of work selection, even on a no-op
            # schedule or an explicit manual rerun of an older revision.
            bad |= current_head_gate(api, repo, head, target, creator, workflow_sha) != "PASS"
            # Historical test failures are diagnostics; incomplete/untrusted
            # coverage and infrastructure failures are blocking.
            audit = historical_audit(api, repo, target, history, creator, workflow_sha)
            bad |= audit in {"BLOCKED", "INCOMPLETE"}
        except Blocked as error:
            print(f"BLOCKED: {repo}; {error}", flush=True)
            bad = True
    # Recheck after both repositories: a newly advanced main cannot inherit
    # another revision's PASS, including when main moves during the second repo.
    for repo, head in snapshots.items():
        try:
            current = valid_sha(api.request(
                f"/repos/{CONFIG['owner']}/{repo}/commits/main")["sha"])
            if current != head:
                print(f"BLOCKED: {repo}; main advanced during verification", flush=True)
                bad = True
        except (Blocked, KeyError, TypeError):
            print(f"BLOCKED: {repo}; main identity unavailable", flush=True)
            bad = True
    return int(bad)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Blocked as error:
        print("BLOCKED: " + str(error), flush=True)
        sys.exit(2)
