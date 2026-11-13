# CodeMod agent review

This is internal `openai/fde` CI. It does not change plugin installation or run
against customer workspaces. The implementation follows Refactory's trusted
prepare → independent expert matrix → validated aggregate pattern.

## Review policy

If a listed CodeMod owner opens a PR that changes the plugin, Codeownerous
requires one other reviewer to approve it. That reviewer can be a non-owner.

PRs to `master` that touch the plugin, its review workflow/action, or the shared
component gate and CodeMod receipt evaluator get a
path-selected matrix using **gpt-5.6-sol**, medium reasoning, with at most two
experts running concurrently across all CodeMod PRs. The four available personas
are `architecture`, `code_quality`, `simplicity`, and `generalist`. The routing and rubrics live in
[`review.py`](../scripts/review.py).

The helper uses Python's standard library. The repository-root workflow
`.github/workflows/codemod_codex_review.yml` checks out `github.workflow_sha`,
sets up Python 3.12, and copies `scripts/review.py` into
`review-bundle/codemod_review.py` alongside the review schema. Preparation runs
`PYTHONPATH=review-bundle python -c 'from codemod_review import prepare; prepare()'`
and uploads that bundle for the selected reviewers and decision job.

The decision job sets up Python 3.12 and downloads the trusted preparation
artifact. It invokes `aggregate()` and `enforce()` from the bundled
`codemod_review` module using the same `PYTHONPATH=review-bundle` import path.
These functions operate on the retained inputs and reviewer results without a
repository checkout or application dependencies. The receipt format, reviewer
selection, override rules, and Git object trust boundary remain unchanged.
This helper is developer/CI tooling and is excluded from the customer plugin
archive.

The `generalist` persona also receives the current PR description as untrusted
context, including on asset-only frontend changes. For visible UI or interaction
changes, it checks for relevant screenshots, GIFs, videos, or concrete written or
linked documentation explaining the changed experience. Either form suffices;
empty placeholders and unrelated media do not. Missing evidence is a P2 advisory
finding tied to a changed UI file. Refactors and tooling changes without a UI/UX
effect do not need media. Reviewers do not fetch linked content or verify media
contents. Editing the PR description triggers another review through the existing
`pull_request_target: edited` event.

Each selected persona reviews the full applicable PR diff independently. P0/P1
findings require direct evidence, a current supported failure, and a bounded fix;
P2/P3 findings are advisory. Exact duplicate findings are combined using their
file, line, and title, retaining the strongest severity. Every scheduled result
must be present and valid. Documentation and static-asset-only changes outside the frontend require no new
personas, but prior blockers still select their owning active reviewer. Findings
from removed personas are omitted from subsequent review generations. Skill
Markdown and `AGENTS.md` remain reviewable because they control agent behavior.

Follow-up reviews receive prior blockers and current file context. An old blocker
remains blocking unless its reviewer explicitly reports it resolved with evidence.
An unrelated commit, omitted resolution, or weaker new finding cannot clear it.
The current implementation supplies the full diff rather than a separate
incremental-review/resolver pipeline. Before scheduling each persona, preparation
counts Unicode characters in its fully rendered prompt, including instructions,
the diff, PR description where applicable, and prior findings/current-file context.
Prompts above **917,504 characters** are automatically skipped. This reserves
**131,072 characters** (12.5%) below Codex 0.145.0's 1,048,576-character input
limit for additional overhead. The budget applies to all four personas; smaller
prompts still run, and no diff is truncated.

An oversized persona has an explicit `skipped` receipt entry with reason
`input_too_large`, its character count, and the budget. The report lists each
skipped reviewer, and the receipt remains `complete: false` when any reviewer was
skipped. This is an automatic exception to required model review: the component
gate accepts the size skips if the remaining reviewers succeed and no blocking
findings remain. Existing blockers assigned to skipped reviewers are retained.
Missing results from reviewers that should have run still fail. A size-skipped
review cannot receive a maintainer override; reduce the input and rerun to resolve
retained blockers. Other required CI checks remain enforced.

The workflow reads PR Git objects without checking out or executing PR code.
The helper, schema, prompts, and composite action come from the trusted workflow
revision. As in Refactory, the secret-bearing job ends with the credential-isolated
Codex composite; only a pinned artifact upload follows model execution.

## Rate limits and concurrency

PR review events and PR comments starting with `/codemod-review override ` share
the concurrency group `codemod-review-plugins-delivery-plugins-codemod`. Other
comments use a unique group per run and skip preparation without entering the
review queue.
One workflow runs at a time for this plugin root; its matrix starts at most two
personas. Other PRs wait, including comment-override runs, so this limit applies
across PRs rather than separately to each PR. Refactory and Doc Authoring have
independent groups and are unaffected. This is a concurrency cap, not a token
budget shared with other users of the same model quota.

The workflow uses GitHub's [`queue: max`](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency)
to retain up to 100 pending runs without canceling the active review or replacing
other pending PRs. Additional runs beyond that limit are canceled. Long queues
remain pending in the required GitHub status until a review completes; canceled
runs cannot pass the gate.

The expert action follows Refactory's bounded TPM retry runner: an initial attempt
plus up to three retries, with exponential backoff and random waits of 5–10,
10–20, and 20–40 seconds. Only the recognized TPM error on stderr is retried;
other failures exit immediately. Failed attempts cannot publish partial or stale
results. The 20-minute persona timeout includes retries and waiting between them.

## Required GitHub status

In the internal `openai/fde` repository, the trusted shared reporter workflow
`.github/workflows/doc_authoring_component_gates.yml` publishes the existing
required `fde/component-gates` status on each PR's current head commit using
GitHub Actions' built-in token. Its single status writer,
`ci/component_gates.py`, combines Doc Authoring checks with the CodeMod receipt
evaluator, `ci/codemod_review_gate.py`. These root CI files are not included in
the standalone export.
A change that touches both components must pass both. The existing
`codemod/agent-review` job remains diagnostic; CodeMod adds no separate required
status. Doc Authoring's `doc-authoring/component-gates` status records its own
progress and recovery without approving the shared gate by itself.

The reporter runs outside the model review queue, on PR lifecycle events, review
run state changes, and updates to `master`. It also supports manual reconciliation
from `master`. It reads current GitHub metadata and trusted review artifacts;
it never checks out or executes proposed PR code. The shared writer serializes
publication for each commit and checks every applicable open PR at that commit.

PRs outside both components receive an explicit successful result after their
complete changed file set is checked, including renamed paths. CodeMod drafts
and queued reviews remain pending. Failed or canceled reviews, missing or invalid
receipts, and blocking findings cannot pass. An invalid override comment cannot
replace a valid review with a passing result. A Doc Authoring-only PR cannot
clear a CodeMod failure on another PR at the same commit.

The receipt's `base` is the common ancestor used for the PR diff, obtained from
GitHub's comparison of the current base and head commits. Preparation, publication,
overrides, and the gate use this same merge-base identity. An ordinary advance of
`master` leaves the reviewed diff unchanged and does not invalidate its receipt.
A different PR head or merge-base requires a fresh review. This reviews the PR
diff, not the result of merging it into the latest `master`.

A green status from another check, resolved thread, or copied bot comment is not
approval data. A passing result requires the trusted workflow, current run attempt,
PR, head, merge-base, validated aggregate receipt, and passing decision to agree.
Incomplete receipts are rejected except for the explicit, validated input-size
exception described above.

### Rerunning failed jobs

GitHub's **Re-run failed jobs** reuses artifacts from successful preparation and
reviewer jobs. Retried reviewers replace their own output for that preparation;
the aggregate requires every scheduled reviewer job to succeed and publishes a
receipt for the new workflow attempt. **Re-run all jobs** creates a new preparation
and reviewer artifact set. Both paths still validate the current PR head and
merge-base before publication. If retained input artifacts have expired, rerun
all jobs. Rerunning a review after an override requires a fresh override for that
review attempt; an earlier approval does not carry forward.

A rerun retains the original workflow revision. To use a repaired workflow or
helper, first land the repair on trusted `master`, then trigger a new review
event, for example by editing the PR description or updating its branch. A
workflow change confined to the proposed PR cannot change the trusted
`pull_request_target` workflow that reviews it. If the broken workflow blocks
its own repair, a repository maintainer must resolve that bootstrap through the
repository's authorized process. The finding override below requires a complete
review receipt and cannot repair failed preparation or missing reviewer output.

### Buildkite checks

CodeMod review is enforced by `fde/component-gates`. The legacy Buildkite review
waiter's project registration and polling pipeline have been removed, so
Buildkite no longer needs a GitHub read credential to wait for the same review.
The normal CodeMod build and test checks still contribute to `buildkite/fde/pr`.

## Comment override

After a complete review, a user with repository `maintain` or `admin` permission
may comment on the PR:

```text
/codemod-review override <full current head SHA>
reason: Why accepting these findings is appropriate
```

The command checks the authenticated comment author and requires a completed
review for the current merge-base and head. It cannot override missing or invalid
reviewer output. The consolidated report records the actor, reason, commit and
original findings; a new receipt records the override and its source review run
and attempt. An override cannot approve a later rerun or an incomplete latest
review. New commits require fresh
review or a new override, and the old findings are still rechecked. Builds,
ownership checks, title validation and other gates remain enforced.

## Installation and rollout

1. Create GitHub environment `codemod-agent-review`, restrict it to `master`, and
   add `CODEX_OPENAI_API_KEY`. The key must support `gpt-5.6-sol` on the existing
   `github-private-network` runner. This follows Refactory's runner/action pattern
   with a separate CodeMod credential boundary.
2. Land the Python reviewer workflow and helper on trusted `master`, then trigger
   a new review event as described above. The preparation artifact and expert
   action must come from that trusted workflow revision. CodeMod review is
   required through `fde/component-gates`; the duplicate Buildkite review waiter
   is removed.
3. Verify `fde/component-gates` on a real passing CodeMod review, a blocked or
   failed review, an authorized override, a Doc Authoring-only PR, a mixed PR,
   and an unrelated PR. Validate the exact commit and the status's GitHub Actions
   identity. Local tests do not establish model access, runner readiness, or
   live branch-protection enforcement.
4. Preserve the existing required `fde/component-gates` status and all other
   required checks. No new status or branch-protection change is needed. Confirm
   that a missing or nonpassing shared result blocks merging, including when
   only one component passes.
5. Update existing PR branches from `master` and run fresh checks. After their
   reviews finish, run the shared reporter's manual reconciliation on `master`
   if their component statuses need refreshing.

Input-size skipping also requires the trusted helper, workflow publisher, and
receipt evaluator on `master`. Trigger a fresh review after that change lands;
rerunning a workflow pinned to the old helper does not add the new behavior.

The reporter needs only its declared `GITHUB_TOKEN` permissions. No new GitHub
App, Secret Broker target, Buildkite credential, worker deployment, or
`openai/openai` change is needed.

Receipts are retained for 90 days. Expired or missing receipts fail verification;
keep the artifact available while its PR remains open. This feature does not
change shared Refactory/Doc Authoring model environments or secrets. The existing
Doc Authoring reporter workflow now also owns shared component-status publication.
