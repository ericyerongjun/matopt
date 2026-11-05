---
name: side
description: >
  Open a side conversation: answer a quick question that is off the main
  task without changing the task's files, plan or direction, then return to
  the main task. Use for /side <question>; /btw is an alias.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# /side: a side conversation

1. Treat the text after `/side` (or `/btw`) as an aside, not a new instruction for the main task.
2. Answer it briefly from what you know or with read-only tools (`read_file`, `grep`, `web_search`). Do not write or
   edit files, run state-changing commands, call `update_plan`, or start sub-agents for it.
3. Prefix the answer with "Side note:" and keep it short.
4. If a task was in progress, end with one line saying the main task continues, then continue it unchanged. If the
   aside reveals something the main task should change, say so and ask; do not change course on your own.
