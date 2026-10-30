# Contributing

Thanks for helping. This is a Python project with YAML experiment plans; issues and pull requests are welcome.

## Set up and check your change

Needs Python 3.11+. Caches and virtual environments stay in git-ignored folders inside the repo.

```bash
make setup   # install the package and dev tools
make lint    # ruff, ruff format, mypy --strict
make test    # full test suite
make plans   # shipped plans pass every rule, briefs current, lock file matches
make audit   # known-vulnerability check (as in CI)
```

A pull request is ready when `make lint`, `make test` and `make plans` pass and CI is green.

## Adding an experiment

1. Copy `docs/templates/experiment.yaml` to `experiments/gd-NNN-<short-name>.yaml` and fill it in.
2. `gameday validate experiments/` until it reports 0 findings.
3. Get it approved (approver is not the owner), then `gameday seal experiments/ --lock experiments/plans.lock.json`
   and `gameday brief experiments/ --out-dir docs/gamedays`.
4. Commit the plan, the lock file and the brief together. Changing a sealed plan needs a new
   approval and `--reseal`.

## Guidelines

- Keep each change focused; explain the problem it solves in the pull request.
- Add or update a test for every behaviour change (a new rule needs a firing and a passing case),
  and update `docs/results/` when numbers change.
- Significant design changes need an ADR in `docs/adr/` (copy `0001` for the format).
- Keep function comments to one short phrase; let names and tests explain the rest.
- Report security problems privately, as described in [SECURITY.md](SECURITY.md).

By contributing you agree that your work is released under the [MIT License](LICENSE).
