# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub:
**Security > Report a vulnerability** on this repository
(`https://github.com/EquinoxWN/chaos-gamedays/security/advisories/new`). Do not open a public issue.

Include what you found, how to reproduce it, and the impact you expect. You will get an answer
within 7 days. Fixes are released as soon as they are ready, and you are credited unless you ask
not to be.

## Supported versions

This project is pre-1.0. Only the latest commit on `main` receives fixes.

## Scope

- **In scope:** an unsafe plan that passes `gameday validate` (for example a fault outside the
  blast radius or an abort that can never fire), a plan edit after sealing that `gameday verify`
  misses, unsafe parsing of plan, lock or series files.
- **Out of scope:** vulnerabilities in Chaos Mesh, k6 or Kubernetes themselves (report those upstream).

## How this repository protects itself

- Plans are parsed with a restricted YAML loader (no Python tags, no aliases) and size limits;
  series and lock files are size-limited and validated before use.
- Every GitHub Action is pinned to a full commit SHA, and workflows run with read-only
  permissions and without persisted credentials.
- Dependabot update pull requests are paused (`open-pull-requests-limit: 0`) so the repository keeps a single `main` branch; dependencies are updated by hand and checked by the CI audit.
- CI runs lint, mypy `--strict`, tests and a known-vulnerability check (pip-audit) on every push and pull request.
