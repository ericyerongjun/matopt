---
name: btw
description: >
  Alias for /side: answer a by-the-way question without derailing the
  current task or touching its files. Use for /btw <question>.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# /btw (alias for /side)

Follow the `side` skill exactly (`load_skill side` if needed): answer the aside briefly with read-only tools,
prefix it with "Side note:", change nothing, then continue the main task unchanged.
