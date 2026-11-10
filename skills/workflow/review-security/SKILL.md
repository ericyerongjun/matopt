---
name: review-security
description: >
  Audit code or a diff for security issues: injection, authn/authz gaps,
  secret exposure, unsafe deserialization, path traversal, SSRF, XSS,
  dependency risks. Reports exploitable findings with evidence. Use for
  /review-security.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Security review

1. Scope: the diff or path given; map trust boundaries first (HTTP handlers, CLI args, files, env, MCP/tool inputs,
   LLM output that reaches a shell or HTML).
2. Check, with the data flow from source to sink:
   - injection: shell (`shell=True`, string-built commands), SQL string formatting, template/HTML without escaping,
     `eval`/`exec`, YAML/pickle loads of untrusted data;
   - paths: user-controlled paths without resolve-and-contain checks, symlinks, zip slip;
   - network: SSRF (user URLs fetched server-side), open redirects, CORS `*` with credentials;
   - authn/authz: endpoints missing checks, IDOR, tokens compared non-constant-time, sessions without expiry;
   - secrets: keys in code, logs, error messages, URLs or client bundles;
   - dependencies: pinned versions with known CVEs (`pip-audit`, `npm audit` when available).
3. For each finding give: severity (critical/high/medium/low), `file:line`, the attacker-controlled input, the impact,
   a proof (request, payload or test), and the fix. Separate confirmed issues from hardening suggestions.
4. Never exploit live systems or exfiltrate data; test only locally. Do not edit code unless asked.
