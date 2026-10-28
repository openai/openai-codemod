# CodeMod contributor guidance

CodeMod is a skills-only migration plugin. `codemod:codemod` routes requests;
`codemod:workflow` coordinates stages. The selected record provider is the
authority. There is no local control plane or workflow database.
Read [DEVELOPMENT.md](DEVELOPMENT.md) and the relevant skills before editing.

- Keep product changes in this package. Update repository packaging, CI, and
  export references only when the requested change requires that integration.
  Preserve `OWNERS`, the `codemod` identity, existing marketplace memberships,
  and release/review controls. Do not publish or change installed plugins unless
  the user requests it.
- Preserve unrelated work and keep changes to the requested scope. Inspect and
  report for review/explanation requests; implement for change requests.
- Stage skills own scope and prerequisites; provider adapters own native records.
  Keep shared evidence, approval, and continuation rules in their current owners.
  Do not add another state store, synchronize providers, or silently switch one.
- Preserve old project files. Follow [UPGRADES.md](docs/UPGRADES.md) when a change
  affects compatibility; old SQLite projects are not automatically converted.
- Keep skill directory/frontmatter names unqualified and user-facing names
  qualified, such as `codemod:assess`. Keep references portable in installed ZIPs.
- Run `python3 tests/check-package.py --self-test` and relevant standard-library
  unit tests. Run `make validate-plugins` from the repository root. Test the built
  ZIP for packaging changes; use [WORKFLOW_SMOKE.md](docs/WORKFLOW_SMOKE.md) for
  release verification and report live checks that were not exercised.
- PRs use a Conventional Commit title with a Linear issue ID and the `codemod`
  label. Describe externally exported changes using the repository template.

Prefer focused changes and existing checks. Add tests only for consequential
behavior or regressions; synthetic fixtures do not prove live migration execution.
