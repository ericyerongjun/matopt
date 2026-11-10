---
name: prompt-blame
description: >
  Trace a line of code back to the prompt, conversation or PR that produced
  it, using git blame/log, commit trailers, PR descriptions and saved agent
  transcripts. Use for /prompt-blame <file:line> (the equivalent of
  cursor-blame).
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Trace code to its prompt

1. Find the commit: `git blame -w -C -C -L <line>,<line> -- <file>`; for code that moved, `git log -L <line>,<line>:<file>`
   or `git log -S '<snippet>' --oneline`.
2. Read the commit: `git show --stat <sha>` and its message. Note agent trailers (`Co-Authored-By: Claude …`,
   `Generated with …`, session or conversation links).
3. Find the PR: `gh pr list --search <sha> --state all` or `gh api repos/{owner}/{repo}/commits/<sha>/pulls`; read
   the description and review thread for the task statement.
4. Find the conversation: search saved transcripts for the snippet or the file name — MatOpt workspaces
   (`<data_dir>/workspaces/<conversation-id>/`), exported conversations, `openspec/changes/archive/*/proposal.md`
   for the requirement that asked for it.
5. Report the chain: line → commit (author, date, message) → PR → prompt/requirement (quoted briefly) and how sure
   each link is. Say clearly when the trail ends.
