## Summary

<!-- What does this change do, and why? Link related issues with "Refs #123". -->

## Demo

<!-- Required: embed a short GIF or MP4 of what this pull request does. -->
<!-- Record it with VHS using docs/demos/_settings.tape, or any screen recorder, and drag the file here. -->

## Test plan

<!-- The exact commands you ran and what they showed. -->

- [ ] `uv run --frozen python -m unittest discover -s tests -v`
- [ ] `uv run --frozen ruff check src tests examples scripts`
- [ ] `uv run --frozen mypy src scripts`
- [ ] `uv run --frozen python scripts/check_readme.py README.md`

## Checklist

- [ ] The title follows [Conventional Commits](https://www.conventionalcommits.org/) (for example `fix(runtime): ...`).
- [ ] Tests cover the change and run offline.
- [ ] The README, docs, and demo tapes are updated if behavior or output changed.
- [ ] No API keys, `.env` files, personal paths, or private data are included.
- [ ] `CHANGELOG.md` is untouched (it is generated).
