# Hero demo: bounded advisory from a host snapshot, offline with no API key.
# Render from the repository root after `uv sync --frozen`: vhs docs/demos/advisory.tape
Output docs/demos/advisory.gif
Output docs/demos/advisory.mp4

Source docs/demos/_settings.tape
Require python
Source docs/demos/_setup.tape

Type "# Four Jev choices over options the host supplied; advice only, never authority."
Enter
Sleep 500ms
Type "python examples/advisory.py"
Enter
Wait+Screen@30s /carries no permission/
Sleep 4s
