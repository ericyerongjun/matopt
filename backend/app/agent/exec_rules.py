"""Prefix rules for shell commands, ported from Codex's ``execpolicy`` crate.

``bash`` used to refuse a command when its lower-cased text contained one of a
dozen substrings. That is easy to evade (``rm -fr /``, ``r''m -rf /``) and it
also refuses harmless text (``echo "sudo is not installed"``). Codex instead
parses the command into argv and matches rules on *token prefixes*:

    prefix_rule(pattern=["git", ["push", "reset"]], decision="prompt", ...)

Each pattern element is one token or a list of alternatives. When several
rules match, the strictest decision wins (forbidden > prompt > allow), and a
rule's ``match`` / ``not_match`` examples are checked when the rules load, so
a policy file is also its own unit test.

MatOpt reads the same shape from TOML (``settings.exec_rules_path``)::

    [[rule]]
    pattern = ["git", ["push", "reset"]]
    decision = "prompt"
    justification = "Rewrites shared history"
    match = ["git push origin main"]
    not_match = ["git status"]

and adds the built-in rules below. A command line is split into its pipeline
and list segments; every segment is checked, after leading ``VAR=value``
assignments and wrappers (``env``, ``nice``, ``nohup``, ``time``, ``timeout N``,
``command``) are stripped, so the wrapped program is what is matched. MatOpt
has no approval broker yet, so ``prompt`` refuses with its justification too;
it is kept distinct so an approval UI can later allow it per call.

This is a guard against obvious mistakes and injected instructions, not a
sandbox: arbitrary code in ``python -c`` or a script file is out of its view.
That is what ``app/agent/sandbox.py`` is for.
"""

from __future__ import annotations

import logging
import os
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Optional, Union

logger = logging.getLogger(__name__)

Decision = Literal["allow", "prompt", "forbidden"]
_RANK = {"allow": 0, "prompt": 1, "forbidden": 2}
PatternToken = Union[str, tuple[str, ...]]

_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "|&", "(", ")", "\n"})
_WRAPPERS = frozenset({"env", "nice", "nohup", "time", "command", "exec", "stdbuf", "ionice"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
_FETCHERS = frozenset({"curl", "wget"})


@dataclass(frozen=True)
class Rule:
    pattern: tuple[PatternToken, ...]
    decision: Decision
    justification: str = ""
    match: tuple[str, ...] = ()
    not_match: tuple[str, ...] = ()

    def matches(self, argv: list[str]) -> bool:
        if len(argv) < len(self.pattern):
            return False
        for token, expected in zip(argv, self.pattern):
            options = expected if isinstance(expected, tuple) else (expected,)
            if os.path.basename(token) not in options and token not in options:
                return False
        return True


@dataclass
class Evaluation:
    decision: Decision = "allow"
    rule: Optional[Rule] = None
    segment: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def message(self) -> str:
        what = " ".join(self.segment) or "this command"
        why = f" ({self.rule.justification})" if self.rule and self.rule.justification else ""
        if self.decision == "forbidden":
            return f"Command refused by exec policy: `{what}` is not allowed{why}."
        return (f"Command refused by exec policy: `{what}` needs the user's approval{why}, and approvals are "
                "not available in this session. Ask the user to run it, or choose another approach.")


def _rule(pattern: Iterable, decision: Decision, justification: str = "",
          match: Iterable[str] = (), not_match: Iterable[str] = ()) -> Rule:
    tokens = tuple(tuple(t) if isinstance(t, (list, tuple)) else str(t) for t in pattern)
    if not tokens:
        raise ValueError("A rule pattern needs at least one token")
    if decision not in _RANK:
        raise ValueError(f"Unknown decision {decision!r}")
    return Rule(tokens, decision, justification, tuple(match), tuple(not_match))


# Built-in rules: the previous substring denylist, expressed as argv prefixes.
DEFAULT_RULES: tuple[Rule, ...] = (
    _rule(["sudo"], "forbidden", "privileged commands are not available to the agent",
          match=["sudo apt install x"], not_match=["echo sudo"]),
    _rule(["su"], "forbidden", "privileged commands are not available to the agent"),
    _rule(["doas"], "forbidden", "privileged commands are not available to the agent"),
    _rule([["mkfs", "mkfs.ext4", "mkfs.xfs", "newfs", "diskutil", "fdisk", "parted"]], "forbidden",
          "disk formatting and partitioning", match=["mkfs.ext4 /dev/sda1"]),
    _rule([["shutdown", "reboot", "halt", "poweroff", "launchctl", "systemctl"]], "forbidden",
          "host power and service control", match=["shutdown -h now"]),
    _rule(["dd"], "prompt", "raw device copies can overwrite disks", match=["dd if=/dev/zero of=x"]),
    _rule(["chmod", "-R", "777"], "forbidden", "recursive world-writable permissions"),
    _rule(["chown", "-R"], "prompt", "recursive ownership changes"),
    _rule(["git", "push"], "prompt", "publishes to a remote", match=["git push origin main"],
          not_match=["git status"]),
)


def _strip_wrappers(argv: list[str]) -> list[str]:
    i = 0
    while i < len(argv):
        token = argv[i]
        name = os.path.basename(token)
        if "=" in token and not token.startswith("=") and token.split("=", 1)[0].isidentifier():
            i += 1                                   # VAR=value prefix
        elif name in _WRAPPERS:
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 1                               # wrapper options (env -i, nice -n 5 …)
                if i < len(argv) and argv[i].lstrip("-").isdigit():
                    i += 1
        elif name == "timeout":
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
            if i < len(argv):
                i += 1                               # the duration
        else:
            break
    return argv[i:]


def split_segments(command: str) -> Optional[list[list[str]]]:
    """argv of each pipeline/list segment, or None when the line cannot be parsed."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS or set(token) <= set(";&|()\n"):
            segments.append([])
        else:
            segments[-1].append(token)
    return [_strip_wrappers(seg) for seg in segments if seg]


def _rm_recursive_root(argv: list[str]) -> bool:
    """``rm`` with recursive force flags aimed at /, ~ or a bare glob."""
    if not argv or os.path.basename(argv[0]) != "rm":
        return False
    flags = "".join(a.lstrip("-") for a in argv[1:] if a.startswith("-") and not a.startswith("--"))
    recursive = "r" in flags.lower() or "--recursive" in argv
    targets = [a for a in argv[1:] if not a.startswith("-")]
    return recursive and any(t in {"/", "/*", "~", "~/", "~/*", "*", ".", "..", "$HOME"} for t in targets)


class ExecPolicy:
    def __init__(self, rules: Iterable[Rule] = DEFAULT_RULES):
        self.rules = tuple(rules)
        for rule in self.rules:
            for example in rule.match:
                if self.evaluate(example, only=rule).rule is not rule:
                    raise ValueError(f"Rule {rule.pattern} does not match its example {example!r}")
            for example in rule.not_match:
                if self.evaluate(example, only=rule).rule is rule:
                    raise ValueError(f"Rule {rule.pattern} matches its counter-example {example!r}")

    @classmethod
    def from_settings(cls, settings) -> "ExecPolicy":
        path = getattr(settings, "exec_rules_path", None)
        if not path:
            return cls()
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        extra = [_rule(r["pattern"], r["decision"], r.get("justification", ""), r.get("match", ()),
                       r.get("not_match", ())) for r in data.get("rule", [])]
        return cls(DEFAULT_RULES + tuple(extra))

    def evaluate(self, command: str, *, only: Optional[Rule] = None) -> Evaluation:
        segments = split_segments(command)
        if segments is None:
            # Unbalanced quoting: nothing can be matched reliably. The shell
            # would reject it too, so let it fail there with its own message.
            return Evaluation()
        rules = (only,) if only is not None else self.rules
        best = Evaluation()
        for index, argv in enumerate(segments):
            if only is None:
                if _rm_recursive_root(argv):
                    return Evaluation("forbidden", _RM_RULE, argv)
                if (argv and os.path.basename(argv[0]) in _SHELLS and index > 0
                        and segments[index - 1] and os.path.basename(segments[index - 1][0]) in _FETCHERS):
                    return Evaluation("forbidden", _PIPE_RULE, segments[index - 1] + ["|"] + argv)
            for rule in rules:
                if rule.matches(argv) and _RANK[rule.decision] >= _RANK[best.decision] and (
                        best.rule is None or _RANK[rule.decision] > _RANK[best.decision]):
                    best = Evaluation(rule.decision, rule, argv)
        return best


_RM_RULE = Rule(("rm",), "forbidden", "recursive deletion of the root, home or every file")
_PIPE_RULE = Rule(("curl",), "forbidden", "piping a download straight into a shell")
