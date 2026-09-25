# A source-bound shadow judgment wired through the three ports, offline.
# Render from the repository root after `uv sync --frozen`: vhs docs/demos/shadow-judgments.tape
Output docs/demos/shadow-judgments.gif

Source docs/demos/_settings.tape
Require python
Source docs/demos/_setup.tape

Type "# Classify a test failure from digest-bound evidence (family jd-03):"
Enter
Type "python examples/shadow_judgments.py"
Enter
Wait+Screen@30s /questions spent/
Sleep 4s
