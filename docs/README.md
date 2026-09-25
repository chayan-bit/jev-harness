# jev-harness documentation

Start with the [project README](../README.md) for installation, the quickstart, the usage guides, and how to attach jev-harness to your harness.

| Document | What it covers |
|---|---|
| [README: usage guides](../README.md#usage-guides) | Advisory, Codex hooks and deny policies, the command line, shadow judgments, and calibration. |
| [README: attach to your harness](../README.md#attach-to-your-harness) | The three ports, the candidate catalog, providers, and plain-Python integration. |
| [Demo recordings](demos/) | The [VHS](https://github.com/charmbracelet/vhs) tapes behind every GIF in the README and how to re-render them. |
| [Changelog](../CHANGELOG.md) | Release history, generated from Conventional Commits. |
| [Contributing](../CONTRIBUTING.md) | Development setup, checks, commit conventions, demo videos for pull requests, and the release process. |
| [Security policy](../SECURITY.md) | How to report a vulnerability privately, the authority boundary, and how to handle API keys. |
| [Code of Conduct](../CODE_OF_CONDUCT.md) | Community standards. |
| [Jev-Frame documentation](https://github.com/chayan-bit/Jev-Frame/tree/main/docs) | The underlying framework's architecture and contracts. |

## Demo recordings

Each `.tape` file in [`demos/`](demos/) is a script for [VHS](https://github.com/charmbracelet/vhs).
The files starting with `_` hold the shared look (theme, font, and window size) and the hidden setup that activates the project environment with a neutral `$ ` prompt.
Re-render every demo from the repository root with:

```sh
uv sync --frozen
for tape in docs/demos/[!_]*.tape; do vhs "$tape"; done
```

CI renders every tape on each pull request to prove the recordings still run, but it never commits the output.
