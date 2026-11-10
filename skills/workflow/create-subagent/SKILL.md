---
name: create-subagent
description: >
  Create a custom sub-agent role (name, description, allowed tools,
  instructions, step limit) that the main agent can spawn with
  spawn_agent. Use for /create-subagent.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Create a custom sub-agent

Roles are TOML files in `<data_dir>/agents/*.toml` (default `backend/data/agents/`), read at startup next to the
built-in roles `default`, `analyst`, `researcher`, `reviewer`.

```toml
name = "spec-checker"                       # lowercase letters, digits, - or _ (≤ 40)
description = "Checks code against openspec/specs scenarios; does not modify files."
tools = ["read_file", "list_files", "find_files", "grep", "bash"]   # omit for all non-parent-only tools
instructions = """
Read the spec, then the code. For every scenario report PASS/FAIL with the evidence (file:line or command output).
Do not modify files."""
max_steps = 30
```

1. Agree on the role's single job, what it may touch, and what its final report must contain (the parent sees only
   that report).
2. Give it the fewest tools that do the job; read-only roles get no write tools.
3. Write the file to the workspace as `agents/<name>.toml`, validate it (`python -c "import tomllib,sys;print(tomllib.load(open(sys.argv[1],'rb')))" agents/<name>.toml`),
   and tell the user to copy it to `<data_dir>/agents/` and restart. Usage afterwards:
   `spawn_agent(task_name=..., message=..., role="<name>")`.
