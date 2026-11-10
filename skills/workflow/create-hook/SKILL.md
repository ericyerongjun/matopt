---
name: create-hook
description: >
  Write a MatOpt hook: a script that runs at an agent event (SessionStart,
  UserPromptSubmit, PreToolUse, PostToolUse, Stop) to add context, block a
  prompt, deny or rewrite a tool call, or keep the agent going. Use for
  /create-hook.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Create a hook (scripts for agent events)

Hooks use the Codex/Claude Code protocol. Config is `hooks.json` in `<data_dir>` (default `backend/data/hooks.json`,
or `HOOKS_PATH`), and only run when the operator sets `HOOKS_ENABLED=true`.

```json
{"hooks": {"PreToolUse": [{"matcher": "bash|python",
                           "hooks": [{"type": "command", "command": "python3 /abs/path/guard.py", "timeout": 30}]}]}}
```

- Events: `SessionStart` and `UserPromptSubmit` (add context, or block the prompt), `PreToolUse` (deny, or rewrite
  arguments via `hookSpecificOutput.updatedInput`), `PostToolUse` (feed a reason back), `Stop` (block to make the
  agent continue; bounded per turn). `matcher` is a regex over the tool name, read only for the tool events.
- The hook gets one JSON object on stdin (event name, conversation id, tool name and input for tool events).
- Answer: `exit 0` with optional JSON on stdout (plain text = extra context where allowed); `exit 2` blocks, with
  the reason on stderr; any other exit is a logged non-blocking error.

Steps:
1. Ask what should happen and on which event; choose the narrowest matcher.
2. Write the script under `hooks/<name>.py` in the workspace: read stdin JSON, decide, exit 0/2. No network, no
   secrets, fast (well under the timeout).
3. Test it locally by piping a sample event: `echo '{"hook_event_name":"PreToolUse","tool_name":"bash","tool_input":{"command":"rm -rf /"}}' | python3 hooks/<name>.py; echo $?`.
4. Give the user the `hooks.json` entry with the absolute script path and tell them to copy the script outside the
   workspace (a hook must not live where the agent can rewrite it) and restart the backend. Do not edit the
   operator's hooks.json yourself.
