---
name: review-bugbot
description: >
  Find bugs and regressions in a diff: trace changed code paths, compare
  old and new behavior, and report only defects with a concrete failing
  input. Use for /review-bugbot.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Bugbot: find bugs and regressions

1. Get the diff (argument, `gh pr diff <n>`, or `git diff <base>...HEAD` plus uncommitted changes). Read each changed
   function in full, and its callers.
2. For each change ask: what did this do before, what does it do now, who depends on the old behavior?
3. Hunt for: off-by-one and boundary values; null/empty/whitespace input; error paths that swallow or leak; partial
   writes and missing rollback; ordering, races, retries and idempotency; stale caches; time zones and units;
   changed defaults; removed validation; API/schema changes without callers updated; tests that no longer assert
   anything.
4. Verify each suspicion before reporting it: run the existing tests, write a minimal reproduction, or evaluate the
   expression in `python`. Drop what you cannot substantiate, or mark it "unverified".
5. Output a ranked list: severity · `file:line` · defect in one sentence · failing input → wrong result · minimal fix.
   End with the commands you ran. Do not modify the code unless asked.
