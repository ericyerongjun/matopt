---
name: review
description: >
  Review the current changes or a given target and choose the right
  reviewer: general correctness, bugs and regressions (review-bugbot), or
  security (review-security). Use for /review.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Review: choose a code reviewer

1. Find the target: the argument (a path, a branch, a PR number via `gh pr diff <n>`), else the working-tree diff
   (`git diff HEAD` plus untracked files), else the files changed in this conversation.
2. Pick the reviewer and say which in one line:
   - risky logic, state, concurrency, data handling → **bugbot** (`load_skill review-bugbot`);
   - auth, input from users/network/files, secrets, shell/SQL/HTML construction, permissions → **security**
     (`load_skill review-security`);
   - otherwise a general pass: correctness first, then clarity, tests, consistency with the codebase.
   For a large diff run both, or delegate one to a `reviewer` sub-agent (`spawn_agent role=reviewer`).
3. Report findings most-severe first: `file:line` — what is wrong — a concrete failing input — the fix. No style
   nits unless asked. Say plainly when nothing was found and what you did not check.
4. Do not edit files unless the user asks you to apply the fixes.
