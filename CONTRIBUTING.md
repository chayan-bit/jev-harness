# Contributing to jev-harness

Thanks for your interest in improving jev-harness.
Bug reports, documentation fixes, and focused pull requests are all welcome.
Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Search [existing issues](https://github.com/chayan-bit/jev-harness/issues) first, and open one for large or API-changing work so the design can be discussed before you write code.
- Ask usage questions in [Discussions](https://github.com/chayan-bit/jev-harness/discussions) rather than in issues.
- Report security problems privately as described in [SECURITY.md](SECURITY.md), never in a public issue.

## Development setup

Install [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer, then create the locked environment:

```sh
git clone https://github.com/chayan-bit/jev-harness.git
cd jev-harness
uv sync --frozen
```

## Checks

Every check below runs in CI on each pull request, and all of them run offline without a TypeSafe API key.

| Check | Command |
|---|---|
| Tests | `uv run --frozen python -m unittest discover -s tests -v` |
| Lint | `uv run --frozen ruff check src tests examples scripts` |
| Types | `uv run --frozen mypy src scripts` |
| README snippets | `uv run --frozen python scripts/check_readme.py README.md` |
| Examples | `uv run --frozen python examples/advisory.py` (and `shadow_judgments.py`, `calibration.py`) |
| Wheel | `uv build`, then install the wheel into a fresh virtual environment and run the examples and `jev-harness hook-config` |
| Demo tapes | `for tape in docs/demos/[!_]*.tape; do vhs "$tape"; done` (needs [VHS](https://github.com/charmbracelet/vhs)) |

Add or update tests for every behavior change, and keep new tests offline and deterministic.
The README check executes every code block in `README.md`, so a README example that stops working fails CI.
If you change dependencies, update `pyproject.toml` and regenerate the lockfile with `uv lock`.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/), because the changelog and version numbers are generated from them.

| Type | Use it for | Changelog section |
|---|---|---|
| `feat` | A new user-facing capability | Added |
| `fix` | A bug fix | Fixed |
| `perf` | A performance improvement | Performance |
| `refactor` | A behavior-preserving code change | Changed |
| `revert` | Reverting an earlier commit | Reverted |
| `deps` | A dependency update | Dependencies |
| `docs`, `test`, `ci`, `build`, `style`, `chore` | Everything else | Not listed |

Mark breaking changes with `!` after the type (for example `feat!: rename HostSnapshot.phase`) or a `BREAKING CHANGE:` footer.
Examples: `fix(hooks): ignore reports for another session` and `docs: explain deny policies`.

## Pull requests

- Keep each pull request focused on one change and explain what changed and why.
- **Include a demo video.** Every pull request description must embed a short recording (GIF or MP4) of what the change does, for example the new behavior running, the examples, or the checks passing.
  Record it with [VHS](https://github.com/charmbracelet/vhs) using the shared look in `docs/demos/_settings.tape`, or any screen recorder, and drag the file into the pull request description.
  Changes that affect a README demo should also update its tape in `docs/demos/` and re-render the GIF.
- Fill in the test plan in the pull request template with the commands you ran.
- Make sure CI passes; `main` only accepts changes whose required checks are green.
- Never commit API keys, `.env` files, captured live payloads, real decision captures or labels, personal paths, or other private data; fixtures must be synthetic.

## Releases

Releases are automated with [release-please](https://github.com/googleapis/release-please).

1. Every push to `main` updates an open release pull request titled like `chore(main): release 0.2.0`.
   It bumps the version in `pyproject.toml`, `uv.lock`, `jev_harness.__version__`, and the README install command, and adds a `CHANGELOG.md` section built from the Conventional Commits since the last release.
2. Because the release pull request is opened by GitHub Actions, CI does not start on it automatically.
   Close and reopen it (or push a commit to its branch) to run the required checks.
3. When the maintainer merges it, release-please tags the merge commit (for example `v0.2.0`) and publishes a GitHub release with the same notes.

Never edit `CHANGELOG.md` by hand.
The history up to `v0.1.0` was generated with [git-cliff](https://git-cliff.org/) (`git cliff --latest -o CHANGELOG.md`, configured in `cliff.toml`), and release-please appends every later release in the same format.
jev-harness is not published to PyPI yet, so users install releases from their Git tags.

## Depending on Jev-Frame

jev-harness pins [Jev-Frame](https://github.com/chayan-bit/Jev-Frame) to a release tag in `pyproject.toml`, and `FRAMEWORK_REVISION` in `src/jev_harness/agent.py` records the matching commit.
Bump both together, run `uv lock`, and only when jev-harness needs a change from a newer Jev-Frame release.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
