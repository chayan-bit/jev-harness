# Offline threshold calibration: validation selection, one held-out report, no activation.
# Render from the repository root after `uv sync --frozen`: vhs docs/demos/calibration.tape
Output docs/demos/calibration.gif

Source docs/demos/_settings.tape
Require python
Source docs/demos/_setup.tape

Type "# Freeze a threshold on validation groups, then report once on held-out groups:"
Enter
Type "python examples/calibration.py"
Enter
Wait+Screen@60s /activation_allowed/
Sleep 4s
