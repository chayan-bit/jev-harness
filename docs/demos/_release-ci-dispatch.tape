# PR demo (not rendered in CI): the release workflow's CI dispatch step, run with a stubbed gh.
# Render from the repository root: vhs docs/demos/_release-ci-dispatch.tape (needs actionlint, yq, jq, and a stub gh that echoes its arguments in /private/tmp/claude-501/stubbin).
Output "/private/tmp/claude-501/demos/release-ci-dispatch.gif"

Source docs/demos/_settings.tape
Hide
Type "export PS1='$ ' PATH=/private/tmp/claude-501/stubbin:$PATH GITHUB_REPOSITORY=owner/repo && clear"
Enter
Show

Type "actionlint .github/workflows/release-please.yml && echo 'actionlint: ok'"
Enter
Sleep 2s
Type "yq '.jobs.release-please.steps[1]' .github/workflows/release-please.yml"
Enter
Sleep 4s
Type "yq -r '.jobs.release-please.steps[1].run' .github/workflows/release-please.yml > /private/tmp/claude-501/step.sh"
Enter
Type "# release-please reported one release PR:"
Enter
Type `PRS='[{"headBranchName":"release-please--branches--main","number":7}]' bash /private/tmp/claude-501/step.sh`
Enter
Sleep 3s
Type "# no release PR: prs is empty, nothing is dispatched"
Enter
Type "PRS='' bash /private/tmp/claude-501/step.sh; echo exit=$?"
Enter
Sleep 3s
