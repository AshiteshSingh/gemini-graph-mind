"""
security.py - Centralized security controls for Omni-Dev.

This module is the single source of truth for the safety-critical decisions that
were previously scattered (and inconsistently implemented) across the tools:

- Autonomous-mode detection (one canonical parser, used everywhere).
- Shell metacharacter detection (command-injection / chaining / substitution).
- Command safety classification (allowlist of base commands, no prefix-substring
  bypass, no metacharacters).
- SSRF protection for outbound HTTP / browser navigation (blocks loopback,
  link-local, private, and other non-public destinations).
- Filesystem sandboxing (workspace boundary + sensitive-file denylist) for the
  file read/write/edit tools.

Design goals:
- Pure and import-safe (``python -c "import src.security"`` must succeed).
- Fail closed for the genuinely dangerous paths (SSRF, secrets), but provide
  explicit, documented environment overrides for power users.
"""
from __future__ import annotations

import ipaddress
import os
import re
import shlex
import socket
from typing import Iterable, Optional, Tuple

# ---------------------------------------------------------------------------
# Autonomous mode
# ---------------------------------------------------------------------------

#: Environment values that count as "off" for OMNI_AUTONOMOUS.
_FALSE_VALUES = ("", "0", "false", "no", "off")


def is_autonomous(ctx: object = None) -> bool:
    """Return whether Autonomous_Mode is active.

    Canonical, project-wide definition (previously each module parsed
    ``OMNI_AUTONOMOUS`` differently). Autonomous mode is on when the
    ``OMNI_AUTONOMOUS`` environment variable is set to any value other than the
    recognized "off" values, OR when ``ctx`` carries a truthy ``autonomous``
    flag (dict key or attribute).

    NOTE: Autonomous mode only bypasses *interactive approval prompts*. It does
    NOT disable the hard safety controls in this module (SSRF protection,
    sensitive-file blocking) — those guard against prompt-injection and model
    mistakes regardless of mode.
    """
    env = os.environ.get("OMNI_AUTONOMOUS", "")
    if env.strip().lower() not in _FALSE_VALUES:
        return True
    if ctx is None:
        return False
    if isinstance(ctx, dict):
        return bool(ctx.get("autonomous"))
    return bool(getattr(ctx, "autonomous", False))


def _env_flag(name: str) -> bool:
    """Return True if an environment flag is explicitly enabled."""
    return os.environ.get(name, "").strip().lower() not in _FALSE_VALUES


# ---------------------------------------------------------------------------
# Shell metacharacter / command-injection detection
# ---------------------------------------------------------------------------

#: Tokens / characters that enable command chaining, substitution, redirection,
#: or backgrounding. The presence of ANY of these means a command cannot be
#: treated as a single, verifiable command and must NOT be auto-approved.
SHELL_METACHARACTERS: Tuple[str, ...] = (
    ";", "&", "|", "$(", "`", "${", ">", "<", "\n", "\r",
    "&&", "||", ">>", "<<", "$(", "<(", ">(",
)


def has_shell_metacharacters(command: str) -> bool:
    """Return True if ``command`` contains any shell chaining/substitution/redirection token."""
    if not command:
        return False
    return any(token in command for token in SHELL_METACHARACTERS)


def _normalize_base_command(token: str) -> str:
    """Reduce a command token to a comparable base name.

    Strips surrounding quotes, takes the path basename, lowercases, and drops a
    Windows executable extension so ``/usr/bin/rm``, ``"rm"``, and ``RM.EXE`` all
    normalize to ``rm``.
    """
    if not token:
        return ""
    # Shells collapse embedded quotes (e.g. r''m -> rm, "rm" -> rm), so remove
    # ALL quote characters before comparing, not just surrounding ones.
    t = token.replace("'", "").replace('"', "").replace("`", "").strip()
    # Path basename (handle both separators regardless of platform).
    t = re.split(r"[\\/]", t)[-1]
    t = t.lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if t.endswith(ext):
            t = t[: -len(ext)]
            break
    return t


def command_base_names(command: str) -> list[str]:
    """Return the normalized base command name(s) in ``command``.

    Splits on shell operators so each pipeline/chain segment's leading command is
    returned. Used by the banned-command check so quoting/path tricks cannot hide
    a forbidden command.
    """
    if not command:
        return []
    # Split into segments on chaining/pipe/redirection operators.
    segments = re.split(r"[;&|\n\r]+|\|\||&&", command)
    names: list[str] = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # Skip leading environment-variable assignments (FOO=bar cmd ...).
        try:
            tokens = shlex.split(seg, posix=(os.name != "nt"))
        except ValueError:
            tokens = seg.split()
        idx = 0
        while idx < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[idx]):
            idx += 1
        if idx < len(tokens):
            names.append(_normalize_base_command(tokens[idx]))
    return names


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------

#: Override to permit requests to private/loopback addresses (e.g. for testing a
#: locally-hosted service). Off by default.
_ALLOW_PRIVATE_ENV = "OMNI_ALLOW_PRIVATE_NETWORK"


def _ip_is_blocked(ip: "ipaddress._BaseAddress") -> bool:
    """Return True for any non-public address we must refuse (SSRF protection)."""
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_outbound_url(url: str, allow_private: Optional[bool] = None) -> Tuple[bool, str]:
    """Validate ``url`` for outbound fetching, blocking SSRF targets.

    Returns ``(ok, reason)``. Rejects:
      - non-http(s) schemes (file://, gopher://, ftp://, data:, etc.),
      - URLs with no resolvable host,
      - hosts that resolve to loopback / link-local / private / reserved IPs
        (e.g. ``127.0.0.1``, ``169.254.169.254`` cloud metadata, ``10.x``,
        ``192.168.x``).

    Set ``allow_private=True`` (or ``OMNI_ALLOW_PRIVATE_NETWORK=1``) to permit
    private/loopback destinations for trusted local services.
    """
    from urllib.parse import urlparse

    if not url or not isinstance(url, str):
        return False, "No URL provided."

    parsed = urlparse(url if "://" in url else "https://" + url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"Blocked URL scheme '{scheme or '(none)'}'. Only http and https are allowed."

    host = parsed.hostname
    if not host:
        return False, "URL has no host."

    if allow_private is None:
        allow_private = _env_flag(_ALLOW_PRIVATE_ENV)

    # Resolve the host to every address it maps to and check them all. A host
    # that resolves to ANY blocked address is refused (defeats DNS rebinding to
    # internal ranges and 'localhost'-style aliases).
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, f"Could not resolve host '{host}'."
    except Exception as exc:  # noqa: BLE001 - resolution must fail closed
        return False, f"Could not resolve host '{host}': {exc}"

    addrs = {info[4][0] for info in infos}
    if not addrs:
        return False, f"Host '{host}' did not resolve to any address."

    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False, f"Host '{host}' resolved to an invalid address '{addr}'."
        if _ip_is_blocked(ip) and not allow_private:
            return False, (
                f"Blocked request to non-public address ({addr}) for host '{host}'. "
                f"This protects against access to internal/metadata services. "
                f"Set {_ALLOW_PRIVATE_ENV}=1 to allow local/private destinations."
            )

    return True, ""


# ---------------------------------------------------------------------------
# Filesystem sandboxing
# ---------------------------------------------------------------------------

#: Fully disable filesystem sandboxing (workspace boundary + secret denylist).
_UNRESTRICTED_FS_ENV = "OMNI_FILE_ACCESS_UNRESTRICTED"

#: Filename patterns that commonly hold secrets / credentials. Reads of these are
#: refused unless sandboxing is disabled, so a misbehaving model can't exfiltrate
#: them via the read tool + an outbound tool.
_SENSITIVE_FILE_PATTERNS = (
    re.compile(r"(^|[\\/])\.env(\.|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.env$", re.IGNORECASE),
    re.compile(r"id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$", re.IGNORECASE),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.pfx$", re.IGNORECASE),
    re.compile(r"\.p12$", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.ssh([\\/]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.aws([\\/]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.gcp([\\/]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.netrc$", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.git-credentials$", re.IGNORECASE),
    re.compile(r"(^|[\\/])credentials(\.json|\.yml|\.yaml)?$", re.IGNORECASE),
    re.compile(r"(^|[\\/])secrets?(\.json|\.yml|\.yaml|\.txt)$", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.omni-dev([\\/]|$)", re.IGNORECASE),  # our own config (tokens)
)


def fs_unrestricted() -> bool:
    """Return True when filesystem sandboxing has been explicitly disabled."""
    return _env_flag(_UNRESTRICTED_FS_ENV)


def workspace_root() -> str:
    """Return the workspace root used as the sandbox boundary.

    Honors ``OMNI_WORKSPACE_ROOT`` when set, otherwise the current working
    directory.
    """
    root = os.environ.get("OMNI_WORKSPACE_ROOT") or os.getcwd()
    return os.path.abspath(root)


def is_sensitive_file(path: str) -> bool:
    """Return True if ``path`` matches a known secret/credential pattern."""
    norm = os.path.abspath(path)
    return any(p.search(norm) or p.search(path) for p in _SENSITIVE_FILE_PATTERNS)


def _within(child: str, parent: str) -> bool:
    """Return True if ``child`` is inside ``parent`` (after resolving)."""
    try:
        parent_real = os.path.realpath(parent)
        child_real = os.path.realpath(child)
    except OSError:
        return False
    try:
        return os.path.commonpath([parent_real, child_real]) == parent_real
    except ValueError:
        # Different drives on Windows, etc.
        return False


def validate_file_access(path: str, *, write: bool) -> Tuple[bool, str]:
    """Validate a file read/write/edit target against the sandbox.

    Returns ``(ok, reason)``. Refuses, unless sandboxing is disabled:
      - access to paths outside the workspace root,
      - reads of sensitive/credential files.

    Sandboxing is fully disabled by ``OMNI_FILE_ACCESS_UNRESTRICTED=1``.
    """
    if fs_unrestricted():
        return True, ""

    if not path:
        return False, "No path provided."

    abs_path = os.path.abspath(path)

    # Sensitive-file denylist (applies to reads and writes).
    if is_sensitive_file(abs_path):
        return False, (
            f"Access to '{path}' is blocked because it looks like a credentials/secrets file. "
            f"Set {_UNRESTRICTED_FS_ENV}=1 to override (not recommended)."
        )

    root = workspace_root()
    if not _within(abs_path, root):
        action = "Writing to" if write else "Reading"
        return False, (
            f"{action} '{path}' is outside the workspace ({root}). "
            f"For safety, file access is restricted to the workspace. "
            f"Set {_UNRESTRICTED_FS_ENV}=1 to override (not recommended)."
        )

    return True, ""
