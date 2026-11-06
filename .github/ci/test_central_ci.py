import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import central_ci as ci


def status(suite, state="success", pin="a" * 40, creator="ci[bot]"):
    return {"context": ci.CONTEXT + suite, "state": state,
            "description": "PASS wf=" + "b" * 40 + " pin=" + pin,
            "creator": {"login": creator}}


class FakeAPI:
    token = "never-log-this"
    def __init__(self):
        self.results = []
        self.hist = ["a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40]
        self.status_map = {}
    def history(self, repo, target):
        return self.hist
    def statuses(self, repo, sha):
        return self.status_map.get(sha, [])
    def status(self, repo, sha, suite, outcome, pin):
        self.results.append((repo, sha, suite, outcome, pin))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.target = {"baseline": "a" * 40, "pin": "a" * 40,
                       "suites": ["one", "two"]}
        self.api = FakeAPI()

    def test_oldest_missing_commits_survive_newer_success_and_batch_limit(self):
        self.api.status_map["e" * 40] = [status("one"), status("two")]
        work = ci.select_work(self.api, "english", self.target, "", "ci[bot]")
        self.assertEqual([sha for sha, suites in work], self.api.hist[:3])
        self.api.status_map.update({sha: [status("one"), status("two")]
                                   for sha, suites in work})
        work = ci.select_work(self.api, "english", self.target, "", "ci[bot]")
        self.assertEqual(work, [("d" * 40, ["one", "two"])])

    def test_pending_blocked_wrong_pin_and_wrong_creator_are_not_completed(self):
        for bad in [status("one", "pending"), status("one", "error"),
                    status("one", pin="c" * 40), status("one", creator="other")]:
            self.assertEqual(ci.completed_suites([bad], self.target, "ci[bot]"), set())

    def test_latest_pending_rerun_supersedes_old_success(self):
        items = [status("one", "pending"), status("one"), status("two", "failure")]
        self.assertEqual(ci.completed_suites(items, self.target, "ci[bot]"), {"two"})

    def test_failed_revision_is_terminal_and_not_relabelled_pass(self):
        items = [status("one", "failure"), status("two")]
        self.api.status_map[self.api.hist[0]] = items
        work = ci.select_work(self.api, "english", self.target, "", "ci[bot]")
        self.assertEqual(work[0][0], self.api.hist[1])
        self.assertEqual(items[0]["state"], "failure")

    def test_manual_reruns_full_sha_even_when_it_passed(self):
        sha = self.api.hist[0]
        self.api.status_map[sha] = [status("one"), status("two")]
        self.assertEqual(ci.select_work(self.api, "english", self.target, sha, "ci[bot]"),
                         [(sha, ["one", "two"])])

    def test_invalid_unreachable_or_short_sha_is_rejected(self):
        for sha in ["main", "a" * 7, "A" * 40, "f" * 40, "../main"]:
            with self.assertRaises(ci.Blocked):
                ci.select_work(self.api, "english", self.target, sha, "ci[bot]")

    def test_failure_is_attributed_to_exact_repo_sha_and_pin(self):
        work = [(self.api.hist[0], ["one"]), (self.api.hist[1], ["two"])]
        with patch.object(ci, "verify", side_effect=["FAIL", "PASS"]), patch.object(ci, "record"):
            self.assertTrue(ci.run(self.api, "english", self.target, work))
        self.assertEqual(self.api.results,
                         [("english", self.api.hist[0], "one", "RUNNING", "a" * 40),
                          ("english", self.api.hist[0], "one", "FAIL", "a" * 40),
                          ("english", self.api.hist[1], "two", "RUNNING", "a" * 40),
                          ("english", self.api.hist[1], "two", "PASS", "a" * 40)])

    def test_setup_failure_does_not_advance_past_missing_result(self):
        with patch.object(ci, "verify", side_effect=ci.Blocked("denied")) as verify, patch.object(ci, "record"):
            self.assertTrue(ci.run(self.api, "english", self.target,
                                   [(self.api.hist[0], ["one"]), (self.api.hist[1], ["one"])]))
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(self.api.results[-1][3], "BLOCKED")

    def test_pat_identity_is_used_without_app_bot_suffix(self):
        with patch.dict(os.environ, {"CENTRAL_CI_TOKEN": "private",
                                     "SOURCE_REPOSITORY": "english", "SOURCE_SHA": ""}), \
                patch.object(ci, "GitHub", return_value=self.api), \
                patch.object(self.api, "request", create=True,
                             return_value={"login": "token-owner"}) as request, \
                patch.object(ci, "select_work", return_value=[]) as select, \
                patch.object(ci, "run", return_value=False):
            self.assertEqual(ci.main(), 0)
        request.assert_called_once_with("/user")
        self.assertEqual(select.call_args.args[-1], "token-owner")

    def test_invalid_token_identity_is_blocked_before_source_access(self):
        with patch.dict(os.environ, {"CENTRAL_CI_TOKEN": "invalid",
                                     "SOURCE_REPOSITORY": "all", "SOURCE_SHA": ""}), \
                patch.object(ci, "GitHub", return_value=self.api), \
                patch.object(self.api, "request", create=True,
                             side_effect=ci.Blocked("denied")), \
                patch.object(ci, "select_work") as select:
            with self.assertRaises(ci.Blocked):
                ci.main()
        select.assert_not_called()

    def test_status_write_failure_never_runs_tests_or_reports_pass(self):
        with patch.object(self.api, "status", side_effect=ci.Blocked("denied")), \
                patch.object(ci, "verify") as verify, patch.object(ci, "record") as record:
            self.assertTrue(ci.run(self.api, "english", self.target,
                                   [(self.api.hist[0], ["one"])]))
        verify.assert_not_called()
        self.assertEqual(record.call_args.args[3], "BLOCKED")


class HistoryTests(unittest.TestCase):
    def test_compare_paginates_more_than_one_page_against_immutable_head(self):
        baseline, head = "0" * 40, f"{150:040x}"
        requests = []
        api = ci.GitHub("redacted")
        def request(path, payload=None):
            requests.append(path)
            if path.endswith("/commits/main"):
                return {"sha": head}
            page = 1 if "&page=1" in path else 2
            numbers = range(1, 101) if page == 1 else range(101, 151)
            return {"status": "ahead", "commits": [{"sha": f"{n:040x}"} for n in numbers]}
        with patch.object(api, "request", side_effect=request):
            history = api.history("english", {"baseline": baseline})
        self.assertEqual(len(history), 151)
        self.assertEqual(history[0], baseline)
        self.assertEqual(history[-1], head)
        self.assertTrue(all(baseline + "..." + head in p for p in requests[1:]))
        self.assertIn("page=2", requests[-1])

    def test_divergence_or_truncated_history_blocks_instead_of_advancing(self):
        for comparison in [{"status": "diverged", "commits": []},
                           {"status": "ahead", "commits": []}]:
            api = ci.GitHub("redacted")
            with patch.object(api, "request", side_effect=[{"sha": "b" * 40}, comparison]):
                with self.assertRaises(ci.Blocked):
                    api.history("english", {"baseline": "a" * 40})

    def test_status_ledger_is_paginated(self):
        api = ci.GitHub("redacted")
        with patch.object(api, "request", side_effect=[[status("one")] * 100, [status("two")]]) as request:
            self.assertEqual(len(api.statuses("english", "a" * 40)), 101)
        self.assertIn("page=2", request.call_args.args[0])

    def test_status_payload_preserves_outcome_and_workflow_attribution(self):
        api = ci.GitHub("redacted")
        with patch.dict(os.environ, {"GITHUB_SHA": "b" * 40, "GITHUB_RUN_ID": "123"}), \
                patch.object(api, "request") as request:
            api.status("english-content", "a" * 40, "spawn-draft", "BLOCKED", "c" * 40)
        path, payload = request.call_args.args
        self.assertEqual(path, "/repos/phatnguyen03022001/english-content/statuses/" + "a" * 40)
        self.assertEqual(payload["state"], "error")
        self.assertIn("wf=" + "b" * 40, payload["description"])
        self.assertIn("pin=" + "c" * 40, payload["description"])
        self.assertTrue(payload["target_url"].endswith("/123"))

    def test_http_denial_reports_endpoint_and_code_without_response_or_token(self):
        api = ci.GitHub("credential-not-for-logs")
        denied = urllib.error.HTTPError("https://api.github.com/private",
                                       403, "private response text", {}, None)
        with patch.object(ci.urllib.request, "urlopen", side_effect=denied):
            with self.assertRaises(ci.Blocked) as raised:
                api.request("/repos/owner/repository/commits/main")
        message = str(raised.exception)
        self.assertIn("GET /repos/owner/repository/commits/main: HTTP 403", message)
        self.assertNotIn("private response text", message)
        self.assertNotIn("credential-not-for-logs", message)

    def test_auth_missing_is_blocked(self):
        with patch.dict(os.environ, {"CENTRAL_CI_TOKEN": ""}):
            with self.assertRaises(ci.Blocked):
                ci.main()


class IsolationTests(unittest.TestCase):
    def test_child_commands_have_no_credentials_or_public_workflow_output_files(self):
        with patch.dict(os.environ, {"CENTRAL_CI_TOKEN": "do-not-pass", "GITHUB_TOKEN": "hidden",
                                     "GIT_CONFIG_COUNT": "1", "GITHUB_STEP_SUMMARY": "private"}):
            env = ci.clean_env()
        self.assertNotIn("CENTRAL_CI_TOKEN", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("GIT_CONFIG_COUNT", env)
        self.assertNotIn("GITHUB_STEP_SUMMARY", env)

    def test_test_stdout_stderr_are_redacted_and_nonzero_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertFalse(ci.command([sys.executable, "-c",
                "import sys; print('private'); print('private', file=sys.stderr); sys.exit(1)"], Path(tmp)))
        self.assertEqual(output.getvalue(), "")

    def test_suite_failure_cannot_be_masked_by_later_success(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"RUNNER_TEMP": tmp}), patch.object(ci, "checkout"), \
                patch.object(ci, "command", side_effect=[True, True, True, False, True]):
            outcome = ci.verify("english-content", "a" * 40,
                                {"pin": "b" * 40}, "content-quality", "not-in-child")
        self.assertEqual(outcome, "FAIL")

    def test_checkout_checks_exact_sha_and_never_persists_credentials(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ci, "command", return_value=True) as command, \
                patch.object(ci.subprocess, "check_output", return_value="b" * 40 + "\n"):
            with self.assertRaises(ci.Blocked):
                ci.checkout("english", "a" * 40, Path(tmp) / "source", "credential")
        fetch = command.call_args_list[2]
        self.assertIn("a" * 40, fetch.args[0])
        self.assertNotIn("credential", " ".join(fetch.args[0]))
        self.assertEqual(fetch.kwargs["env"]["GIT_CONFIG_COUNT"], "1")



class WorkflowBoundaryTests(unittest.TestCase):
    def test_central_runner_has_readonly_local_permissions_and_explicit_secret(self):
        workflow = (ci.ROOT.parent / "workflows/central-private-ci.yml").read_text()
        self.assertRegex(workflow, r"permissions:\s*\n\s*contents: read")
        self.assertIn("CENTRAL_CI_TOKEN: " + "$" + "{{ secrets.english }}", workflow)
        self.assertNotRegex(workflow, r"contents:\s*write|secrets:\s*inherit|(?i:mongodb(?:\+srv)?:|git push|git commit|npm publish)")

    def test_central_discovery_only_targets_the_two_authorized_repositories(self):
        self.assertEqual(set(ci.CONFIG["targets"]), {"english", "english-content"})


if __name__ == "__main__":
    unittest.main()
