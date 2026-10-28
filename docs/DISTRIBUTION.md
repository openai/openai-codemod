# Distributing CodeMod

CodeMod is distributed under the existing `codemod` plugin identity. Version 0.2
replaces the local application with workflow skills, references, and templates.
The package has no MCP server, local dashboard, Python runtime, SQLite store,
Node dependencies, or WSL launcher. Read [Upgrades](UPGRADES.md) before updating
an installation used with the previous application. Loading the skills requires
Codex; migration work still needs Git sources, access to the selected record
provider, and the tools required by its execution environment.

Keep the shared plugin customer-neutral. Customer source, data, credentials,
generated reports, and custom instructions belong in the approved migration
workspace or report store, outside the shared plugin source.

## Internal marketplace

The existing root and delivery marketplace registrations both resolve `codemod`.
Contributors with FDE repository access can install from the root marketplace:

```sh
codex plugin marketplace add git@github.com:openai/fde.git --ref master
codex plugin add codemod@openai-fde
```

Restart Codex, then ask `@codemod help me start a migration`. For a local source
checkout, see the [developer guide](../DEVELOPMENT.md). A local installation does
not publish the plugin to Cloud or make it available in another environment.

## Build and verify a customer package

Maintainers need Git and Python 3.11 or later. From the CodeMod source directory:

```sh
python3 tests/check-package.py --self-test
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/package.py build --output /tmp/codemod-package/openai-codemod.zip
python3 scripts/package.py verify --archive /tmp/codemod-package/openai-codemod.zip
```

Use a dedicated output directory for each candidate. The build writes three
files: the requested ZIP, `RELEASE.json`, and `SHA256SUMS`. It verifies the staged
artifact before replacing these files. The ZIP extracts to `openai-codemod/` and
contains the local marketplace manifest, installation instructions, embedded
release evidence, and `plugins/codemod/`.

The plugin payload includes only tracked source files under `skills/` and
`assets/`, its manifest, `README.md`, and `docs/UPGRADES.md`, plus generated
`distribution.json`. Ownership files, contributor instructions, hidden files,
tests, developer scripts, review helpers, and other repository documentation are
excluded. No frontend build or dependency installation occurs. The packager
normalizes customer author metadata and retains the existing plugin name and
version. Identical inputs produce identical ZIP bytes.

`RELEASE.json` records `plugin`, `marketplace`, `version`, `source_commit`,
`dirty`, and a SHA-256 inventory of every other ZIP member. `distribution.json`
retains the installer contract: `mode: distribution`, `source_commit`,
`plugin_version`, and `dirty`. The external and embedded release records must
agree. `SHA256SUMS` identifies the exact ZIP filename and digest.

Verification checks the archive digest, file inventory and hashes, provenance,
marketplace and plugin identity, all ten workflow skills, required user guidance,
and manifest assets. It rejects unsafe archive paths, links, executable or special
files, duplicate members, and files outside the skills-only boundary. It then
extracts the verified members into a temporary directory and runs the trusted
source checkout's Markdown and provider-route checker against those exact files.
It never executes code from the ZIP. Keep `tests/check-package.py` alongside the
verifier in the source checkout; neither is a customer runtime dependency.

Normal builds require a clean CodeMod subtree. For a local preview, add
`--allow-dirty` to both build and verify. The preview is marked `dirty: true` and
cannot be consumed by managed release automation. New shipping files must be
tracked before packaging, even for previews; untracked files are never included.
Commit the final source and rebuild without this flag before release.

## Hosted releases

The existing `codemod/package` GitHub Actions workflow builds the artifact and
verifies the same ZIP on Linux and macOS. It uses Python's standard library and
requires no uv, npm, frontend build, or runtime startup. Hosted artifact names
remain `openai-codemod-<source-commit>.zip`, accompanied by `RELEASE.json` and
`SHA256SUMS`; validated customer artifacts are retained for 14 days.

Relevant pushes to `master` and manual runs produce artifacts. A
`codemod/v<plugin-version>` tag must match the committed manifest version and
publishes the verified files as a prerelease in the private FDE repository,
without marking it latest. Share that ZIP with its evidence, rather than a
GitHub source-code archive. Building or verifying a package does not publish it.

Package checks establish artifact integrity and structural workflow contracts.
They do not demonstrate authenticated provider access, native worker execution,
or completion of a migration. Before releasing, run the operator-led
[workflow smoke](WORKFLOW_SMOKE.md), including the old-project upgrade boundary.

## Managed canary and stable releases

The existing internal `codemod/release` workflow consumes a successful
`codemod/package` artifact from a push to `master`. Keep automation disabled until
the exact intended source has a verified package and the existing one-time
configuration below is complete. Manual package builds and tag builds do not
automatically deploy to this channel.

The release controller stages the verified ZIP as a detached release and updates
CodeMod's canary pointer in `cloud-plugins-release-experiments`. It does not
rebuild the package or create a second plugin. `codex_codemod_early_access`
controls access and `codex_codemod_canary` selects the canary cohort. The mapping
uses `before_release_id` for stable and `after_release_id` for canary. Candidate
manifest versions must be newer than both current versions; bump the manifest
through the source PR.

Validate the exact canary release using its recorded source, version, and release
ID. The internal [publication procedure](https://github.com/openai/fde/blob/master/.agents/skills/codemod-publish/references/release.md)
offers optional marketplace E2E checks on macOS and Windows before promotion.
When selected, these checks take approximately 20 minutes and may start only
after the recorded canary assignment and shared Statsig configuration have been
unchanged for at least six hours. A changed canary or configuration invalidates
that evidence. Record either the successful checks or their explicit skip;
skipping adds no waiting period and does not replace workflow smoke or production
review. See the internal [runner guide](https://github.com/openai/fde/blob/master/projects/codemod-evals/desktop/MARKETPLACE.md)
for existing registration and test-account prerequisites.

To promote, run `codemod/release` on `master` with operation `promote`, the
successful canary workflow's `receipt_run_id` and `receipt_run_attempt`, and
`canary_validation` describing evidence for that release. If marketplace E2E was
selected, provide its `marketplace_e2e_run_id` and `marketplace_e2e_run_attempt`.
The protected production environment reviews this evidence. Promotion publishes
the exact staged release and advances stable; it does not rebuild or change the
release ID.

To roll back, select `rollback` and the run ID and attempt of the transition to
undo, with the reason in the validation input. Canary rollback restores the prior
canary pointer; promotion rollback restores the prior published baseline and
stable pointer. The controller requires the current pointers and retained Statsig
revision to match its receipt. Any intervening configuration edit requires fresh
operator recovery. A rollback that updates Statsig makes its original receipt
ineligible for another rollback. Release selection rollback does not convert
project data or prove that installed clients downgraded.

Each operation retains `codemod-release-receipt-<attempt>` for 90 days, including
partial-operation journals. Keep receipts in the approved long-term release
record if longer recovery is required. The shared release queue is serialized;
coordinate separate Console or other-plugin configuration edits while a release
is running. Inspect partial writes and live state before recovery.

### Existing one-time CI configuration

The managed release workflow requires:

1. `codemod-release-canary` and `codemod-release-production` GitHub environments,
   each restricted to the `master` branch, with production reviewers for
   promotion and rollback.
2. Environment secrets `CODEMOD_MONOREPO_READ_TOKEN`, `OPENAI_API_KEY_PROD`, and
   `CODEMOD_STATSIG_CONSOLE_API_KEY` with the existing repository, production
   plugin-administration, and OpenAI Statsig release-map permissions.
3. `CODEMOD_RELEASE_MONOREPO_SHA` set to a reviewed full commit supporting
   detached staging and `DirectoryAdminClient.publish_release`.
4. The existing `github-private-network` runner's access to the pinned internal
   release dependencies, plugin administration, upload endpoints, and Statsig
   Console API. These are release-automation dependencies, outside the ZIP.
5. The existing CodeMod release mapping and canary gate configured before setting
   `CODEMOD_RELEASE_AUTOMATION_ENABLED` to `true`.

The trusted default-branch release controller owns credentials and uses retained
artifact evidence. Downloaded plugin code is never executed with those
credentials. Release automation and its internal dependencies stay outside the
standalone source export and customer ZIP.
