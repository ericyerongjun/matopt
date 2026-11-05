---
name: autopilot
description: >
  Handle pull request feedback and CI: read review comments and failing
  checks on a GitHub PR, fix what is actionable, reply, push, and repeat
  until checks pass. Use for /autopilot [PR number].
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Autopilot: PR feedback and CI

Requires the `gh` CLI authenticated for the repository (or a connected GitHub MCP server).

1. Identify the PR: the argument, else `gh pr view --json number,headRefName,url`.
2. Collect work:
   - checks: `gh pr checks <n>`; for each failure `gh run view <run-id> --log-failed` (read the first real error);
   - review comments: `gh api repos/{owner}/{repo}/pulls/<n>/comments` and `gh pr view <n> --comments`;
     skip resolved and outdated threads.
3. Triage into: fix (clear and in scope), discuss (disagreement or design question — draft a reply, don't change
   code), ignore (nits the author already declined). Show the list before editing when it has more than a few items.
4. Fix on the PR branch, one commit per logical fix, running the relevant tests locally first. Never force-push,
   never disable or skip tests to go green, never change CI config to hide a failure.
5. Push only after the user has agreed to pushing for this PR. Reply to each addressed comment with what changed
   (commit SHA); post replies only with the user's agreement.
6. Re-check CI. To keep watching, offer `/loop` (e.g. every 10 minutes: "check PR <n> checks; report only when all
   pass or one fails").
