# The jev-harness command line: offline preview, hook configuration, and an exact deny.
# Render from the repository root after `uv sync --frozen`: vhs docs/demos/cli.tape
Output docs/demos/cli.gif

Source docs/demos/_settings.tape
Require python
Source docs/demos/_setup.tape

Type "# Compile the four advisory questions without any provider access:"
Enter
Type `jev-harness preview < examples/snapshot.json | python -m json.tool | grep '"judgment_id"'`
Enter
Wait+Screen@20s /"compaction"/
Sleep 2s
Type "# Print a Codex hooks fragment to review; nothing is installed:"
Enter
Type "jev-harness hook-config | python -m json.tool | head -n 12"
Enter
Wait+Screen@20s /statusMessage/
Sleep 3s
Type "clear"
Enter
Type "# An exact deny policy is the only thing that can deny a tool call:"
Enter
Type `echo '{"schemaVersion":"1.0.0","denyToolNames":["apply_patch"],"denyCommandSha256":[]}' > /tmp/deny.json`
Enter
Type `echo '{"session_id":"s-1","turn_id":"t-1","cwd":"/work","model":"m","hook_event_name":"PreToolUse","permission_mode":"default","tool_name":"apply_patch","tool_input":{}}' | jev-harness hook --policy /tmp/deny.json`
Enter
Wait+Screen@20s /"permissionDecision":"deny"/
Sleep 4s
