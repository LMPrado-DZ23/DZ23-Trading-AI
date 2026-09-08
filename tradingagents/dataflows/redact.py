"""Centralized secret redaction for error paths (#security-fase0, rodada 3).

Truncation and delimiter-neutralization protect prompt structure, not
secrecy: a remote body that echoes the request URL (or the key itself) in
its first bytes would still leak. Before any remote body, exception text or
URL is logged, returned from a tool, or forwarded to an LLM, it must pass
through ``redact_secrets``.

Two complementary strategies, both applied:

1. VALUE-BASED — the actual values of credential-bearing environment
   variables (``*_API_KEY``, ``*_TOKEN``, ``*_SECRET``, ...) are replaced
   wherever they appear, regardless of surrounding syntax. This catches a
   body that echoes the key verbatim.
2. PATTERN-BASED — ``api_key=...`` style query params and ``Bearer ...``
   headers are masked even when the value is not a configured credential
   (covers keys of OTHER tenants echoed by a proxy, and rotation races).

Pure stdlib.
"""

import os
import re

_CREDENTIAL_ENV_NAME_RE = re.compile(
    r"(_API_KEY|_APIKEY|_TOKEN|_SECRET|_PASSWORD|AUTHORIZATION)$", re.IGNORECASE
)

# api_key=..., token:..., authorization = ... — value runs to &, whitespace,
# quote or end. Group 1 keeps the parameter name and separator.
_PARAM_RE = re.compile(
    r"((?:api[_-]?key|token|secret|authorization|password)\s*[:=]\s*)"
    r"(?!(?:Bearer|Basic)\b)([^&\s\"']+)",
    re.IGNORECASE,
)

# Bearer keeps the 8+ floor (real bearer tokens are long; a bare short word
# after "Bearer" in prose is more likely prose). Basic is SEPARATE (achado
# r8-1): "Basic YTpi" is a VALID credential for a:b — Basic validity imposes
# no minimum length, so any non-empty token-shaped value is redacted.
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_BASIC_RE = re.compile(r"(Basic\s+)[A-Za-z0-9+/=_.~-]+", re.IGNORECASE)

# JSON handling (achados r7 -> r8-2 -> r9): three properties are required
# and a single literal regex cannot give all of them:
#   (1) escaped quotes inside VALUES must not split the value (r8-2);
#   (2) escaped KEYS ("api\u005fkey" == "api_key") must still match (r9);
#   (3) TRUNCATED input (no closing quote) must never return the secret just
#       because parsing failed (r9): a conservative fallback removes the
#       value up to a safe delimiter or the end of the input.
# Strategy: when the WHOLE text parses as JSON, redact recursively after the
# parser's semantic normalization (solves 1+2 exactly). Otherwise a textual
# scanner walks key/value pairs with an explicit escape state machine: keys
# are json-decoded before matching, values are consumed to the first
# UNESCAPED quote -- or to end-of-input when truncated (solves 1+2+3 on
# fragments). Never both passes on the same text (idempotency).
import json as _json

_SENSITIVE_KEY_RE = re.compile(
    r"^(?:api[_-]?key|apikey|token|secret|password|authorization)$",
    re.IGNORECASE,
)
_KV_START_RE = re.compile(r'"((?:[^"\\]|\\.)*)"\s*:\s*"', re.DOTALL)


def _decode_json_key(raw: str) -> str:
    try:
        return _json.loads('"' + raw + '"')
    except ValueError:
        return raw


def _estimate_max_depth(text: str, limit: int) -> int:
    """String-aware O(n) nesting estimate, stopping early past `limit`.
    Runs BEFORE any parse so the budget bounds the parser's work (F2: the
    content cap lives downstream of redaction and therefore bounds nothing
    here)."""
    depth = max_depth = 0
    in_str = esc = False
    for c in text:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
            if depth > max_depth:
                max_depth = depth
                if max_depth > limit:
                    return max_depth
        elif c in "}]":
            depth = max(0, depth - 1)
    return max_depth


_MAX_JSON_DEPTH = 64
_MAX_STRUCTURED_CHARS = 200_000


def _iterative_redact(obj):
    """Depth-independent traversal (explicit stack): no recursion, no
    RecursionError, regardless of input shape."""
    changed = False
    root = {"v": None}
    stack = [(obj, root, "v")]
    while stack:
        node, parent, slot = stack.pop()
        if isinstance(node, dict):
            new = {}
            parent[slot] = new
            for k, v in node.items():
                if isinstance(k, str) and _SENSITIVE_KEY_RE.match(k.strip()):
                    new[k] = _REDACTED
                    changed = True
                else:
                    new[k] = None
                    stack.append((v, new, k))
        elif isinstance(node, str):
            # Achado do marco broker: um VALOR string pode carregar JSON
            # escapado com chave sensível dentro ('{\"secret\": ...}').
            # O scanner textual roda sobre a folha; inocente => idêntica.
            scanned = _redact_json_textual(node)
            if scanned != node:
                changed = True
            parent[slot] = scanned
        elif isinstance(node, list):
            new = [None] * len(node)
            parent[slot] = new
            for idx, v in enumerate(node):
                stack.append((v, new, idx))
        else:
            parent[slot] = node
    return root["v"], changed


def _redact_json_structured(text: str):
    """Whole-text JSON: parse, iteratively redact sensitive keys, reserialize.

    Budgeted BEFORE the parse (F2): oversized or over-deep input skips this
    pass entirely and falls to the bounded textual scanner — never an
    exception, never unredacted passthrough. Returns None when the text is
    not entirely valid JSON; returns the ORIGINAL text when nothing needed
    redaction (innocent JSON stays byte-identical)."""
    if len(text) > _MAX_STRUCTURED_CHARS:
        return None
    if _estimate_max_depth(text, _MAX_JSON_DEPTH) > _MAX_JSON_DEPTH:
        return None
    try:
        obj = _json.loads(text)
    except (ValueError, RecursionError):
        return None
    redacted, changed = _iterative_redact(obj)
    if not changed:
        return text
    return _json.dumps(redacted, ensure_ascii=False)


def _consume_string(text: str, j: int):
    """Consume a JSON string body starting AFTER its opening quote; returns
    index just past the closing quote, or len(text) when truncated."""
    esc = False
    while j < len(text):
        c = text[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif c == '"':
            return j + 1
        j += 1
    return len(text)


def _consume_value(text: str, j: int):
    """Consume ONE JSON value of ANY type starting at j (F1): string, object,
    array, number, true/false/null — bounded single pass; truncated input
    consumes to EOF; unrecognized bytes are consumed to the next safe
    delimiter (fail closed: ambiguity never returns the value's bytes).
    Returns the end index (exclusive)."""
    n = len(text)
    while j < n and text[j] in " \t\r\n":
        j += 1
    if j >= n:
        return j
    c = text[j]
    if c == '"':
        return _consume_string(text, j + 1)
    if c in "{[":
        # Typed stack of EXPECTED closers (F1-r11): a bare depth counter let
        # ']' close '{' and '}' close '[', so a mismatched closer in
        # adversarial malformed JSON terminated the sensitive value early
        # and the secret suffix survived. A mismatched closer is NEVER a
        # safe terminator: fail closed by consuming to end-of-input.
        expected: list[str] = []
        in_str = esc = False
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    expected.append("}")
                elif ch == "[":
                    expected.append("]")
                elif ch in "}]":
                    if not expected or expected[-1] != ch:
                        return n  # mismatched closer: consume to EOF
                    expected.pop()
                    if not expected:
                        return j + 1
            j += 1
        return n  # truncated container: everything to EOF
    # number / true / false / null / garbage: to the next safe delimiter
    while j < n and text[j] not in ",}]\r\n":
        j += 1
    return j


_KEY_RE = re.compile(r'"((?:[^"\\]|\\.)*)"\s*:', re.DOTALL)


def _redact_json_textual(text: str) -> str:
    """Fragment/invalid/truncated JSON: single-pass, iterative, escape-aware
    scanner. Keys are json-decoded before matching (escaped keys count); the
    value of a sensitive key is consumed WHATEVER ITS JSON TYPE (F1) up to a
    safe delimiter or end-of-input — the secret is removed, never returned
    because parsing failed."""
    out = []
    i = 0
    while True:
        m = _KEY_RE.search(text, i)
        if m is None:
            out.append(text[i:])
            break
        key = _decode_json_key(m.group(1))
        if not _SENSITIVE_KEY_RE.match(key.strip()):
            out.append(text[i:m.end()])
            i = m.end()
            continue
        out.append(text[i:m.end()])
        end = _consume_value(text, m.end())
        out.append(" " + _REDACTED)
        i = end
    return "".join(out)


_REDACTED = "[REDACTED]"

# Values shorter than this are ignored by the value-based pass: redacting
# 3-char strings would mangle ordinary text more than it protects.
_MIN_SECRET_LEN = 8


def _configured_secret_values(env: dict | None = None) -> list[str]:
    env = os.environ if env is None else env
    values = [
        v
        for k, v in env.items()
        if _CREDENTIAL_ENV_NAME_RE.search(k) and v and len(v) >= _MIN_SECRET_LEN
    ]
    # Longest first so partial overlaps redact the full value.
    return sorted(set(values), key=len, reverse=True)


def redact_secrets(
    text: str,
    *,
    extra_secrets: tuple[str, ...] = (),
    env: dict | None = None,
) -> str:
    """Return ``text`` with configured credential values and credential-shaped
    parameters masked. Idempotent; safe on empty input."""
    if not text:
        return text
    out = str(text)
    for value in _configured_secret_values(env):
        out = out.replace(value, _REDACTED)
    for value in sorted({s for s in extra_secrets if s and len(s) >= _MIN_SECRET_LEN},
                        key=len, reverse=True):
        out = out.replace(value, _REDACTED)
    # Bearer FIRST: otherwise the param pattern consumes the literal word
    # "Bearer" as the value of "authorization:" and the token survives.
    out = _BEARER_RE.sub(rf"\g<1>{_REDACTED}", out)
    out = _BASIC_RE.sub(rf"\g<1>{_REDACTED}", out)
    structured = _redact_json_structured(out)
    out = structured if structured is not None else _redact_json_textual(out)
    out = _PARAM_RE.sub(rf"\g<1>{_REDACTED}", out)
    return out
