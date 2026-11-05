---
name: shell
description: >
  Run a shell command exactly as typed after /shell and show its output,
  without extra work. Use for /shell <command>.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# /shell: run a shell command

1. Take everything after `/shell` as the command, verbatim. If it is empty, ask for the command.
2. Run it once with the `bash` tool in the workspace. Do not rewrite it, add flags, chain other commands, or "fix"
   it first. For a long-running command use a background session and `bash_poll`.
3. Show the exit code and the output (trim very long output to the head and tail and say so).
4. Stop. Do not interpret, retry or act on the result unless the user asks.
