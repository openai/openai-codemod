"""Trusted review fixtures: no GitHub writes or actual reviewer execution."""

from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import zipfile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import review

HEAD = "07572d8821db34edece6eeb9cb76ca1706b1afc5"
FILE = review.ROOT + "backend/codemod/store/__init__.py"
PLUGIN = Path(__file__).resolve().parents[1]


@contextmanager
def fixture_directory():
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            yield Path(directory)
        finally:
            os.chdir(previous)


def receipt():
    return {
        "pr": 2259,
        "head": HEAD,
        "base": "b" * 40,
        "run": 20,
        "attempt": 1,
        "personas": ["generalist"],
        "files": [FILE],
        "findings": [],
        "complete": False,
        "pass": False,
        "override": "",
        "reason": "",
    }


def finding(severity="P1", persona="generalist"):
    return {
        "id": "",
        "persona": persona,
        "file": FILE,
        "line": 1,
        "severity": severity,
        "title": "Preserve the request transaction",
        "evidence": "The request exits the transaction before storing the result.",
        "failure": "A repeated request may duplicate its effect.",
        "fix": "Save the result inside the same transaction.",
    }


def skipped_review(persona="generalist"):
    return {
        "persona": persona,
        "reason": "input_too_large",
        "actual_chars": review.MAX_PROMPT_CHARS + 1,
        "max_chars": review.MAX_PROMPT_CHARS,
    }


def write_aggregate_inputs(metadata=None, expert=None):
    Path("review-bundle").mkdir(exist_ok=True)
    Path("review-results").mkdir(exist_ok=True)
    review.write_json("review-bundle/metadata.json", metadata or receipt())
    review.write_json(
        "review-results/generalist.json",
        expert or {"persona": "generalist", "head": HEAD, "findings": [], "resolutions": []},
    )
    Path("receipt.json").unlink(missing_ok=True)


class ReviewTests(unittest.TestCase):
    def test_routing_and_exact_head_schema(self):
        for path in review.GATE_PATHS | {FILE, review.ROOT + "scripts/review.py"}:
            self.assertEqual(review.choose([path], []), sorted(review.PERSONAS))
        self.assertFalse(review.in_scope("ci/README.md"))
        self.assertFalse(review.in_scope("ci/doc_authoring_component_gates.py"))
        self.assertEqual(review.choose([review.ROOT + "docs/README.md"], []), [])
        self.assertEqual(
            review.choose([review.ROOT + "frontend/static/example.png"], []), ["generalist"]
        )
        self.assertEqual(review.choose([], [{"severity": "P1", "persona": "removed"}]), [])
        self.assertTrue(review.reviewable(review.ROOT + "skills/codemod/SKILL.md"))
        template = (PLUGIN / "ci/review.schema.json").read_bytes()
        schema = review.review_schema(template, HEAD)
        self.assertEqual(schema["properties"]["head"], {"type": "string", "enum": [HEAD]})
        self.assertEqual(schema["properties"]["findings"]["type"], "array")
        self.assertIn("resolutions", schema["required"])
        with self.assertRaisesRegex(ValueError, "invalid head"):
            review.review_schema(template, "One concrete defect found.")

    def test_superseded_reviews_use_creation_order_and_trusted_identity(self):
        pull = {"number": 2337, "head": {"sha": HEAD}, "base": {"ref": "master"}}
        newer = {
            "id": 21,
            "created_at": "2026-09-11T18:35:15Z",
            "run_attempt": 1,
            "path": review.WORKFLOW,
            "event": "pull_request_target",
            "display_title": "codemod-review-pr-2337-" + HEAD,
            "head_repository": {"full_name": "openai/fde"},
            "pull_requests": [pull],
        }
        cases = [
            ({}, True),
            ({"created_at": "2026-09-11T18:35:00Z"}, True),
            ({"id": 20, "created_at": "2026-09-11T18:35:00Z"}, False),
            ({"created_at": "2026-09-11T18:34:00Z", "run_attempt": 10}, False),
            ({"path": ".github/workflows/other.yml"}, False),
            ({"head_repository": {"full_name": "other/fde"}}, False),
            ({"display_title": "codemod-review-pr-999-" + HEAD}, False),
        ]
        for changes, expected in cases:
            run = {**newer, **changes}

            def api(method, path):
                self.assertEqual(method, "GET")
                if path == "/actions/runs/20":
                    return {"id": 20, "created_at": "2026-09-11T18:35:00Z"}
                self.assertEqual(
                    parse_qs(urlsplit(path).query)["created"],
                    [">=2026-09-11T18:35:00Z"],
                )
                return {"workflow_runs": [run]}

            with self.subTest(changes=changes), patch.dict(os.environ, GITHUB_RUN_ID="20"):
                with patch.object(review, "api", side_effect=api):
                    self.assertEqual(review.superseded_review(pull, HEAD), expected)
        with patch.object(review, "api") as api:
            self.assertTrue(review.superseded_review(pull, "a" * 40))
            api.assert_not_called()

    def test_aggregate_requires_identity_arrays_and_own_run(self):
        with (
            fixture_directory(),
            patch.dict(os.environ, GITHUB_RUN_ID="20", GITHUB_RUN_ATTEMPT="2"),
        ):
            write_aggregate_inputs()
            result = review.aggregate()
            self.assertTrue(result["pass"])
            self.assertTrue(result["complete"])
            self.assertEqual(result["attempt"], 2)
            for changes in [
                {"head": "One concrete user-visible defect found."},
                {"persona": "simplicity"},
                {"findings": None},
                {"resolutions": None},
            ]:
                expert = {"persona": "generalist", "head": HEAD, "findings": [], "resolutions": []}
                expert.update(changes)
                write_aggregate_inputs(expert=expert)
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    review.aggregate()
                self.assertFalse(Path("receipt.json").exists())
            for changes in [{"run": 19}, {"attempt": 3}, {"attempt": 0}]:
                write_aggregate_inputs({**receipt(), **changes})
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    review.aggregate()
                self.assertFalse(Path("receipt.json").exists())

    def test_git_credential_helper_is_scoped_to_the_actions_fetch(self):
        tokens = dict.fromkeys(
            ("GITHUB_TOKEN", "GH_TOKEN", "CODEMOD_REVIEW_GITHUB_TOKEN"), "test-token"
        )
        with patch.object(review.subprocess, "run") as run, patch.dict(os.environ, tokens):
            run.return_value.returncode = 0
            for actions, command in (
                ("true", "fetch"),
                ("true", "diff"),
                ("true", "show"),
                ("false", "fetch"),
            ):
                with (
                    self.subTest(actions=actions, command=command),
                    patch.dict(os.environ, GITHUB_ACTIONS=actions),
                ):
                    review.git(command)
                    argv = run.call_args.args[0]
                    if actions == "true" and command == "fetch":
                        self.assertIn("credential.helper=", argv)
                        self.assertIn(
                            "credential.https://github.com.helper=!gh auth git-credential", argv
                        )
                    else:
                        self.assertEqual(argv, ["git", command])
                    if actions == "true" and command != "fetch":
                        self.assertTrue(tokens.keys().isdisjoint(run.call_args.kwargs["env"]))

    def test_prepare_reads_git_objects_without_checkout_or_diff_helpers(self):
        with fixture_directory() as directory:
            pull = {
                "number": 2259,
                "state": "open",
                "body": None,
                "head": {"sha": HEAD, "repo": {"full_name": "openai/fde"}},
                "base": {"ref": "master", "sha": "c" * 40},
            }
            review.write_json("event.json", {"number": 2259, "pull_request": pull})
            Path("review-bundle").mkdir()
            Path("review-bundle/review.schema.json").write_bytes(
                (PLUGIN / "ci/review.schema.json").read_bytes()
            )
            environment = {
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_EVENT_PATH": str(directory / "event.json"),
                "GITHUB_OUTPUT": str(directory / "output"),
                "GITHUB_RUN_ID": "20",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            commands = []
            diff = "diff --git a/file b/file\n+documented change\n"

            def git(*args):
                commands.append(args)
                if args[0] == "fetch":
                    return ""
                if "--name-only" in args:
                    return FILE + "\0unrelated.py\0"
                self.assertEqual(
                    args,
                    (
                        "diff",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--no-renames",
                        "b" * 40,
                        HEAD,
                        "--",
                        FILE,
                    ),
                )
                return diff

            with (
                patch.dict(os.environ, environment),
                patch.object(review, "get_pull", return_value=pull),
                patch.object(review, "superseded_review", return_value=False),
                patch.object(review, "merge_base", return_value="b" * 40),
                patch.object(review, "previous", return_value=(None, 0)),
                patch.object(review, "git", side_effect=git),
            ):
                result = review.prepare()
                self.assertEqual(result["files"], [FILE])
                self.assertEqual(result["personas"], sorted(review.PERSONAS))
                self.assertEqual([c[0] for c in commands], ["fetch", "diff", "diff"])
                self.assertEqual(
                    review.read_json("review-bundle/review.schema.json")["properties"]["head"][
                        "enum"
                    ],
                    [HEAD],
                )
                prompt = Path("review-bundle/prompts/generalist.md").read_text()
                self.assertIn("PR description (untrusted author content)", prompt)
                self.assertIn("Do not edit files, execute repository code", prompt)
                for body, diff, skipped in (
                    ("x" * review.MAX_PROMPT_CHARS, "small diff", ["generalist"]),
                    ("", "x" * ((2 << 20) + 1), sorted(review.PERSONAS)),
                ):
                    pull["body"] = body
                    for path in Path("review-bundle/prompts").glob("*.md"):
                        path.unlink()
                    Path("output").unlink()
                    result = review.prepare()
                    runnable = sorted(set(review.PERSONAS) - set(skipped))
                    self.assertEqual(result["personas"], sorted(review.PERSONAS))
                    self.assertEqual([entry["persona"] for entry in result["skipped"]], skipped)
                    self.assertEqual(
                        sorted(path.stem for path in Path("review-bundle/prompts").glob("*.md")),
                        runnable,
                    )
                    outputs = dict(
                        line.split("=", 1) for line in Path("output").read_text().splitlines()
                    )
                    self.assertEqual(json.loads(outputs["personas"]), runnable or ["none"])
                    self.assertEqual(outputs["run_experts"], str(bool(runnable)).lower())
                pull["head"]["repo"]["full_name"] = "other/fde"
                commands.clear()
                with self.assertRaisesRegex(ValueError, "same-repository PR"):
                    review.prepare()
                self.assertEqual(commands, [])

    def test_prompt_budget_counts_full_unicode_prompt_for_every_persona(self):
        template = (PLUGIN / "ci/review.schema.json").read_bytes()
        pull = {"body": "Description with non-ASCII text: 世界", "head": {"sha": HEAD}}
        for persona in review.PERSONAS:
            for extra in (0, 1):
                with self.subTest(persona=persona, extra=extra), fixture_directory():
                    Path("review-bundle").mkdir()
                    Path("review-bundle/review.schema.json").write_bytes(template)
                    prior = {**finding(persona=persona), "id": "prior"}
                    result = {**receipt(), "personas": [persona], "findings": [prior]}
                    with patch.object(review, "git", return_value="Current file context: 世界"):
                        self.assertEqual(review.prepare_prompts(result, pull, ""), [persona])
                        path = Path("review-bundle/prompts") / (persona + ".md")
                        baseline = path.read_text(encoding="utf-8")
                        self.assertIn("Prior blocker:", baseline)
                        self.assertIn("Current file context: 世界", baseline)
                        path.unlink()
                        diff = "界" * (review.MAX_PROMPT_CHARS - len(baseline) + extra)
                        self.assertGreater(len(diff.encode()), 2 << 20)
                        runnable = review.prepare_prompts(result, pull, diff)
                    if extra == 0:
                        self.assertEqual(runnable, [persona])
                        self.assertFalse(result.get("skipped"))
                        self.assertEqual(
                            len(path.read_text(encoding="utf-8")), review.MAX_PROMPT_CHARS
                        )
                    else:
                        self.assertEqual(runnable, [])
                        self.assertEqual(result["skipped"], [skipped_review(persona)])
                        self.assertFalse(path.exists())

    def test_aggregate_skips_preserve_blockers_and_require_runnable_results(self):
        for mixed, blocker, missing in (
            (False, False, False),
            (True, False, False),
            (True, True, False),
            (True, False, True),
        ):
            with (
                self.subTest(mixed=mixed, blocker=blocker, missing=missing),
                fixture_directory(),
                patch.dict(os.environ, GITHUB_RUN_ID="20", GITHUB_RUN_ATTEMPT="1"),
            ):
                result = {
                    **receipt(),
                    "personas": sorted(review.PERSONAS),
                    "findings": [{**finding(), "id": "prior"}] if blocker else [],
                    "skipped": [
                        skipped_review(persona)
                        for persona in sorted(review.PERSONAS)
                        if not mixed or persona == "generalist"
                    ],
                }
                Path("review-bundle").mkdir()
                Path("review-results").mkdir()
                review.write_json("review-bundle/metadata.json", result)
                if mixed and not missing:
                    for persona in review.PERSONAS.keys() - {"generalist"}:
                        review.write_json(
                            "review-results/" + persona + ".json",
                            {"persona": persona, "head": HEAD, "findings": [], "resolutions": []},
                        )
                if missing:
                    with self.assertRaises(FileNotFoundError):
                        review.aggregate()
                    self.assertFalse(Path("receipt.json").exists())
                    continue
                actual = review.aggregate()
                self.assertFalse(actual["complete"])
                self.assertEqual(actual["pass"], not blocker)
                self.assertEqual(actual["skipped"], result["skipped"])
                self.assertEqual(actual["findings"], result["findings"])
                if blocker:
                    with self.assertRaises(ValueError):
                        review.enforce()
                else:
                    self.assertEqual(review.enforce(), actual)

    def test_reject_invalid_oversized_input_receipts(self):
        valid = {**receipt(), "pass": True, "skipped": [skipped_review()]}
        cases = [
            {"skipped": []},
            {"skipped": None},
            {"skipped": [skipped_review(), skipped_review()]},
            {"personas": ["unknown"], "skipped": [skipped_review("unknown")]},
            {"personas": ["generalist", "generalist"]},
            {"skipped": [skipped_review("simplicity")]},
            {"complete": True},
            {"override": "maintainer"},
            {"findings": None},
            {"findings": [finding()]},
            {"pass": False},
        ]
        cases.extend(
            {"skipped": [{**skipped_review(), **change}]}
            for change in (
                {"reason": "cancelled"},
                {"max_chars": review.MAX_PROMPT_CHARS - 1},
                {"actual_chars": review.MAX_PROMPT_CHARS},
                {"actual_chars": True},
                {"actual_chars": str(review.MAX_PROMPT_CHARS + 1)},
                {"max_chars": True},
            )
        )
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                review.validate_decision({**valid, **change})
        with (
            fixture_directory(),
            patch.dict(os.environ, GITHUB_RUN_ID="20", GITHUB_RUN_ATTEMPT="1"),
        ):
            write_aggregate_inputs({**valid, "override": "maintainer"})
            with self.assertRaises(ValueError):
                review.aggregate()
            self.assertFalse(Path("receipt.json").exists())

    def test_aggregate_deduplicates_and_preserves_unresolved_blockers(self):
        with (
            fixture_directory(),
            patch.dict(os.environ, GITHUB_RUN_ID="20", GITHUB_RUN_ATTEMPT="1"),
        ):
            expert = {
                "persona": "generalist",
                "head": HEAD,
                "findings": [finding("P2"), finding("P1")],
                "resolutions": [],
            }
            write_aggregate_inputs(expert=expert)
            first = review.aggregate()
            self.assertFalse(first["pass"])
            self.assertEqual(len(first["findings"]), 1)
            blocker = first["findings"][0]
            self.assertEqual(blocker["severity"], "P1")
            self.assertEqual(len(blocker["id"]), 16)
            for resolutions, expected in [
                ([], False),
                ([{"id": blocker["id"], "status": "resolved", "evidence": "  "}], False),
                (
                    [
                        {
                            "id": blocker["id"],
                            "status": "resolved",
                            "evidence": "Result persistence is now inside the transaction.",
                        }
                    ],
                    True,
                ),
            ]:
                expert.update(findings=[finding("P2")], resolutions=resolutions)
                write_aggregate_inputs(first, expert)
                result = review.aggregate()
                self.assertEqual(result["pass"], expected)
                self.assertEqual(result["findings"][0]["severity"], "P2" if expected else "P1")
            invalid = finding()
            invalid["file"] = "ci/unrelated.py"
            expert.update(findings=[invalid])
            write_aggregate_inputs(expert=expert)
            with self.assertRaisesRegex(ValueError, "scope or evidence"):
                review.aggregate()
            self.assertFalse(Path("receipt.json").exists())

    def test_prepare_override_verifies_artifact_permission_and_preserves_source(self):
        original = {
            **receipt(),
            "run": 101,
            "attempt": 2,
            "complete": True,
            "findings": [finding()],
        }
        overridden = {
            **original,
            "run": 202,
            "attempt": 3,
            "source_run": 101,
            "source_attempt": 2,
            "override": "previous-maintainer",
            "reason": "Earlier approval.",
            "pass": True,
        }
        cases = [
            (original, "write", "maintain", None),
            (overridden, "admin", "admin", None),
            ({**original, "complete": False}, "admin", "admin", "completed review"),
            (
                {**original, "complete": False, "skipped": [skipped_review()]},
                "admin",
                "admin",
                "completed review",
            ),
            (original, "write", "write", "maintain or admin"),
            ({**original, "head": "a" * 40}, "admin", "admin", "completed review"),
        ]
        for old, permission, role, error in cases:
            with self.subTest(error=error, run=old["run"]), fixture_directory() as directory:
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, "w") as zipped:
                    zipped.writestr("receipt.json", json.dumps(old))
                pull = {
                    "number": 2259,
                    "state": "open",
                    "head": {"sha": HEAD, "repo": {"full_name": "openai/fde"}},
                    "base": {"ref": "master", "sha": "c" * 40},
                }
                responses = {
                    "/pulls/2259": pull,
                    "/compare/" + "c" * 40 + "..." + HEAD: {"merge_base_commit": {"sha": "b" * 40}},
                    "/issues/2259/comments": [
                        {
                            "id": 99,
                            "body": f"<!-- codemod-review-run:{old['run']}:{old['attempt']} -->",
                            "user": {"login": "github-actions[bot]"},
                        }
                    ],
                    f"/actions/runs/{old['run']}/attempts/{old['attempt']}": {
                        "path": review.WORKFLOW,
                        "event": "issue_comment" if old["override"] else "pull_request_target",
                        "run_attempt": old["attempt"],
                        "display_title": "codemod-review-pr-2259-" + HEAD,
                    },
                    f"/actions/runs/{old['run']}/artifacts": {
                        "artifacts": [
                            {
                                "id": 77,
                                "name": f"codemod-review-receipt-{old['attempt']}",
                                "expired": False,
                            }
                        ]
                    },
                    "/actions/artifacts/77/zip": archive.getvalue(),
                    "/collaborators/maintainer/permission": {
                        "permission": permission,
                        "role_name": role,
                    },
                }

                def api(method, path, **kwargs):
                    self.assertEqual(method, "GET")
                    return copy.deepcopy(responses[path.split("?")[0]])

                event = {
                    "issue": {"number": 2259},
                    "comment": {
                        "body": "/codemod-review override "
                        + HEAD
                        + "\nreason: Accepted the documented risk.",
                        "user": {"login": "maintainer"},
                    },
                }
                review.write_json("event.json", event)
                environment = {
                    "GITHUB_RUN_ID": "303",
                    "GITHUB_RUN_ATTEMPT": "4",
                    "GITHUB_EVENT_NAME": "issue_comment",
                    "GITHUB_EVENT_PATH": str(directory / "event.json"),
                    "GITHUB_OUTPUT": str(directory / "output"),
                }
                with (
                    patch.dict(os.environ, environment),
                    patch.object(review, "api", side_effect=api),
                ):
                    if error:
                        with self.assertRaisesRegex(ValueError, error):
                            review.prepare()
                        self.assertFalse(Path("review-bundle/metadata.json").exists())
                        continue
                    result = review.prepare()
                    self.assertEqual(
                        result,
                        {
                            **old,
                            "run": 303,
                            "attempt": 4,
                            "source_run": 101,
                            "source_attempt": 2,
                            "override": "maintainer",
                            "reason": "Accepted the documented risk.",
                            "pass": True,
                        },
                    )
                    self.assertIn("run_experts=false", Path("output").read_text())
                    self.assertEqual(review.aggregate(), result)
                    self.assertEqual(review.enforce(), result)

    def test_receipt_pointer_requires_trusted_workflow_and_exact_identity(self):
        trusted = {
            "path": review.WORKFLOW,
            "event": "pull_request_target",
            "run_attempt": 1,
            "display_title": "codemod-review-pr-2259-" + HEAD,
        }
        for changed in [
            {"path": ".github/workflows/other.yml"},
            {"event": "pull_request"},
            {"run_attempt": 2},
            {"display_title": "codemod-review-pr-999-" + HEAD},
        ]:
            with (
                self.subTest(changed=changed),
                patch.object(review, "api", return_value={**trusted, **changed}) as api,
            ):
                with self.assertRaisesRegex(ValueError, "trusted workflow"):
                    review.load_receipt(20, 1, 2259)
                self.assertEqual(api.call_count, 1)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("receipt.json", json.dumps({**receipt(), "pr": 999}))
        with patch.object(
            review,
            "api",
            side_effect=[
                trusted,
                {"artifacts": [{"name": "codemod-review-receipt-1", "id": 77}]},
                archive.getvalue(),
            ],
        ):
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                review.load_receipt(20, 1, 2259)

    def test_wait_checks_current_head_and_merge_base_without_polling_after_success(self):
        pull = {"head": {"sha": HEAD}}
        passed = {**receipt(), "complete": True, "pass": True}
        with patch.dict(os.environ, BUILDKITE_PULL_REQUEST="2259", BUILDKITE_COMMIT=HEAD):
            for value in (
                passed,
                {**passed, "complete": False, "skipped": [skipped_review()]},
            ):
                with (
                    self.subTest(skipped=value.get("skipped", [])),
                    patch.object(review, "get_pull", return_value=pull),
                    patch.object(review, "previous", return_value=(value, 99)),
                    patch.object(review, "merge_base", return_value=value["base"]),
                    patch.object(review.time, "sleep") as sleep,
                ):
                    review.wait()
                    sleep.assert_not_called()
            with patch.object(review, "get_pull", return_value={"head": {"sha": "a" * 40}}):
                with self.assertRaisesRegex(ValueError, "no longer the PR head"):
                    review.wait()


if __name__ == "__main__":
    unittest.main()
