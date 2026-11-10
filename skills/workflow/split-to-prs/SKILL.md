---
name: split-to-prs
description: >
  Split a large set of changes into a stack of small, reviewable pull
  requests that each build and pass tests. Use for /split-to-prs.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Split changes into PRs

1. Inventory: `git status`, `git diff --stat <base>...HEAD`, `git log --oneline <base>..HEAD`. Group hunks by
   purpose: refactors/renames, new modules, behavior changes, tests, docs, generated files.
2. Propose an order where each PR stands alone: mechanical refactors first, then foundations, then features that use
   them, then cleanups. Each PR: title, files, why it is independent, how to test. Ask for approval.
3. Build branches from the base without losing work: `git switch -c split/1-<slug> <base>`, then
   `git checkout <source-branch> -- <paths>` or `git restore -p --source <source-branch>` for partial files; commit;
   run the tests for that slice. Stack later branches on earlier ones when they depend on them.
4. Verify nothing is lost: the diff between the last branch and the original branch must be empty
   (`git diff split/N-<slug> <source-branch>`).
5. Push and open PRs (`gh pr create --base <previous-branch-or-base>`) only with the user's agreement; note the stack
   order in each PR description. Never rewrite or delete the original branch.
