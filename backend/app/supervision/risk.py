"""Risk engine: deterministic rules for destructive and sensitive operations.

Rules look at what is about to run - a shell command, a file path, a URL - so
they apply to any platform whose adapter reports the tool input. They are
deliberately conservative pattern rules, not a sandbox: they catch the
recognisable dangerous cases (wiping a tree, force-pushing, piping the
internet into a shell, touching credentials) and say exactly which rule
matched. They never claim an unmatched command is safe.

Inputs are matched in the backend only when the adapter sent them; a
privacy-first adapter that sends fingerprints only gets no risk screening,
and the decision says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RiskRule:
    id: str
    category: str
    severity: str
    description: str
    pattern: re.Pattern[str]
    #: Which extracted field the pattern applies to.
    target: str  # "command" | "path" | "url"


def _r(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


RULES: tuple[RiskRule, ...] = (
    # --- Destructive filesystem ------------------------------------------
    RiskRule(
        "fs.recursive_delete_root",
        "destructive",
        "critical",
        "Recursive delete of a root, home or parent directory",
        _r(r"\brm\s+(-[a-z]*r[a-z]*f?|-[a-z]*f[a-z]*r|--recursive)\b[^\n;&|]*\s(/|~|\$HOME|\.\.|/\*|\*)(\s|$)"),
        "command",
    ),
    RiskRule(
        "fs.recursive_delete",
        "destructive",
        "high",
        "Recursive forced delete",
        _r(r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b|\bRemove-Item\b[^\n]*-Recurse[^\n]*-Force|\brmdir\s+/s\b|\bdel\s+/[sq]\b"),
        "command",
    ),
    RiskRule(
        "fs.disk_overwrite",
        "destructive",
        "critical",
        "Raw disk write or filesystem format",
        _r(r"\b(mkfs(\.\w+)?|dd\s+if=[^\n]*\bof=/dev/|format\s+[a-z]:|diskpart)\b"),
        "command",
    ),
    RiskRule(
        "fs.permissions_world_writable",
        "risky",
        "medium",
        "Makes files world-writable",
        _r(r"\bchmod\s+(-R\s+)?0?777\b"),
        "command",
    ),
    # --- Version control ---------------------------------------------------
    RiskRule(
        "git.force_push",
        "destructive",
        "high",
        "Force-push rewrites remote history",
        _r(r"\bgit\s+push\b[^\n;&|]*(\s--force\b|\s-f\b|\s--force-with-lease\b)"),
        "command",
    ),
    RiskRule(
        "git.hard_reset",
        "destructive",
        "high",
        "Hard reset or clean discards uncommitted work",
        _r(r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s+\.|restore\s+--source)\b"),
        "command",
    ),
    RiskRule(
        "git.branch_delete_force",
        "destructive",
        "medium",
        "Force-deletes a branch",
        _r(r"\bgit\s+branch\s+-D\b"),
        "command",
    ),
    # --- Data stores ---------------------------------------------------------
    RiskRule(
        "db.drop",
        "destructive",
        "critical",
        "Drops or truncates a database object",
        _r(r"\b(drop\s+(table|database|schema)|truncate\s+table)\b"),
        "command",
    ),
    RiskRule(
        "db.unbounded_delete",
        "destructive",
        "high",
        "DELETE without a WHERE clause",
        _r(r"\bdelete\s+from\s+[\w.\"`]+\s*(;|$)"),
        "command",
    ),
    # --- Remote code / exfiltration --------------------------------------
    RiskRule(
        "exec.pipe_to_shell",
        "remote_code",
        "high",
        "Downloads and executes a remote script",
        _r(r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|fi)?sh\b|\biex\s*\(|\bInvoke-Expression\b"),
        "command",
    ),
    RiskRule(
        "system.privilege",
        "risky",
        "medium",
        "Runs with elevated privileges",
        _r(r"(^|[;&|]\s*)sudo\s+"),
        "command",
    ),
    RiskRule(
        "system.shutdown",
        "destructive",
        "high",
        "Shuts down or reboots the machine",
        _r(r"\b(shutdown|reboot|halt|poweroff|Stop-Computer|Restart-Computer)\b"),
        "command",
    ),
    RiskRule(
        "system.kill_all",
        "destructive",
        "medium",
        "Kills processes broadly",
        _r(r"\b(killall|pkill\s+-9|taskkill\s+/f\s+/im)\b"),
        "command",
    ),
    RiskRule(
        "publish.package",
        "external_effect",
        "high",
        "Publishes a package or deploys",
        _r(r"\b(npm\s+publish|twine\s+upload|cargo\s+publish|gh\s+release\s+create|vercel\s+--prod|fly\s+deploy|terraform\s+(apply|destroy))\b"),
        "command",
    ),
    # --- Runaway execution --------------------------------------------------
    # Found in a live Claude Code run: after two failed identical calls the
    # agent moved its retry loop *inside* one background shell command
    # (``until curl ...; do sleep 1; done``). Tool-level loop detection
    # cannot see repetition inside a single call, and the loop never ends.
    RiskRule(
        "exec.unbounded_loop",
        "runaway",
        "high",
        "Shell loop with no bound or timeout",
        _r(r"(?s)^(?!.*(\b(timeout|max|attempts?|tries|retries)\b|\bseq\s+\d|\$\{?\w+\}?\s*-(lt|le|gt|ge)\b)).*?\b(while\s+(true|:|\[\s*1\s*\]|\(\(\s*1\s*\)\))|until\s.*?;\s*do\b|for\s*\(\(\s*;\s*;\s*\)\))"),

        "command",
    ),
    # --- Credentials -------------------------------------------------------
    RiskRule(
        "secrets.read_command",
        "credentials",
        "high",
        "Reads credential material",
        _r(r"(\.ssh/id_|\.aws/credentials|\.netrc|\.pgpass|\.docker/config\.json|\bprintenv\b|\benv\b\s*$|security\s+find-generic-password)"),
        "command",
    ),
    RiskRule(
        "secrets.path",
        "credentials",
        "high",
        "Accesses a credentials or secrets file",
        _r(r"(^|[\\/])(\.env(\.[\w-]+)?|id_rsa|id_ed25519|credentials(\.json)?|\.netrc|\.pgpass|secrets?\.(ya?ml|json|toml))$"),
        "path",
    ),
    RiskRule(
        "fs.outside_system",
        "risky",
        "high",
        "Writes to a system location",
        _r(r"^(/etc/|/usr/|/bin/|/sbin/|/boot/|C:\\Windows\\|C:/Windows/)"),
        "path",
    ),
)

#: Tools whose input carries a shell command.
SHELL_TOOLS = {"bash", "shell", "powershell", "run_shell", "terminal", "execute_command", "local_shell"}
#: Tools that write rather than read; credential *reads* are flagged too.
WRITE_TOOLS = {"write", "edit", "multiedit", "notebookedit", "apply_patch", "write_file"}


@dataclass
class RiskFinding:
    rule: str
    category: str
    severity: str
    description: str
    matched: str


def _extract(tool_name: str, tool_input: Any) -> dict[str, str]:
    """Pull the fields rules match against out of a tool input."""
    fields: dict[str, str] = {}
    if isinstance(tool_input, dict):
        for key in ("command", "cmd", "script", "code"):
            if isinstance(tool_input.get(key), str):
                fields["command"] = tool_input[key]
                break
        for key in ("file_path", "path", "notebook_path", "filename", "target"):
            if isinstance(tool_input.get(key), str):
                fields["path"] = tool_input[key]
                break
        if isinstance(tool_input.get("url"), str):
            fields["url"] = tool_input["url"]
    elif isinstance(tool_input, str) and tool_name.lower() in SHELL_TOOLS:
        fields["command"] = tool_input
    return fields


def assess(tool_name: str | None, tool_input: Any) -> list[RiskFinding]:
    """Every rule the pending call matches, most severe first."""
    if not tool_name or tool_input is None:
        return []
    fields = _extract(tool_name, tool_input)
    findings: list[RiskFinding] = []
    for rule in RULES:
        value = fields.get(rule.target)
        if not value:
            continue
        match = rule.pattern.search(value)
        if match:
            findings.append(
                RiskFinding(
                    rule=rule.id,
                    category=rule.category,
                    severity=rule.severity,
                    description=rule.description,
                    matched=match.group(0)[:200],
                )
            )
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: order.get(f.severity, 9))
    # A recursive delete of root also matches the generic recursive delete;
    # keep the most specific finding per category.
    seen: set[str] = set()
    unique = []
    for f in findings:
        if f.category in seen:
            continue
        seen.add(f.category)
        unique.append(f)
    return unique


def catalogue() -> list[dict[str, str]]:
    """The rule list, for the dashboard."""
    return [
        {
            "id": r.id,
            "category": r.category,
            "severity": r.severity,
            "description": r.description,
            "applies_to": r.target,
        }
        for r in RULES
    ]
