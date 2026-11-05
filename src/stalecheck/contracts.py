"""Small independent validators; no sibling imports and no assertion-based checks."""
import hashlib
import ipaddress
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

MAX_FILE = 2_000_000
MAX_TOTAL = 12_000_000
ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
VERDICTS = {"SUPPORTED_AS_OF", "STALE", "CONTRADICTED_AS_OF", "ABSTAIN"}


class ValidationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def obj(value, label):
    require(isinstance(value, dict), f"{label} must be an object")
    return value


def text(value, label, maximum=20_000):
    require(isinstance(value, str) and 0 < len(value) <= maximum, f"{label} must be nonempty text (max {maximum})")
    return value


def identifier(value, label="id"):
    require(isinstance(value, str) and ID.fullmatch(value) is not None, f"invalid {label}")
    return value


def day(value, label):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None, f"{label} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"invalid {label}") from exc


def timestamp(value, label):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value) is not None, f"invalid {label}; RFC3339 timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"invalid {label}") from exc
    require(parsed.tzinfo is not None, f"{label} requires timezone")
    return parsed


def string_list(value, label):
    require(isinstance(value, list) and all(isinstance(x, str) for x in value), f"invalid {label}")


def unique(items, label):
    require(isinstance(items, list), f"{label} must be array")
    seen = set()
    for item in items:
        obj(item, label)
        key = identifier(item.get("id"))
        require(key not in seen, f"duplicate {label} id: {key}")
        seen.add(key)
    return seen


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(x):
    raise ValidationError(f"invalid JSON constant: {x}")


def decode(data):
    require(len(data) <= MAX_FILE, "JSON exceeds size bound")
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc


def read_json(path):
    with Path(path).open("rb") as stream:
        return decode(stream.read(MAX_FILE + 1))


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def safe_url(value):
    text(value, "evidence url", 2048)
    require(not any(ord(c) <= 32 for c in value) and "\\" not in value, "unsafe evidence URL")
    try:
        parsed = urlsplit(value)
        require(parsed.scheme == "https" and parsed.hostname is not None and "." in parsed.hostname, "evidence URL must be absolute HTTPS")
        require(parsed.username is None and parsed.password is None and parsed.port in (None, 443), "unsafe URL authority")
        host = parsed.hostname.lower()
        require(not host.endswith((".localhost", ".local", ".internal", ".lan")), "private evidence host forbidden")
        require(re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host) is not None, "invalid evidence hostname")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValidationError("IP literal evidence URLs forbidden")
    except ValueError as exc:
        raise ValidationError("invalid evidence URL") from exc
    # URLs are provenance labels only: never fetched or executed.
    return value


def safe_path(root, value):
    text(value, "artifact path", 240)
    parts = value.split("/")
    require(not any(p in ("", ".", "..") for p in parts), "unsafe artifact path")
    require(not any(c in value for c in "\\:\x00"), "unsafe artifact path")
    require(all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts), "unsafe artifact path characters")
    require(all(not p.endswith((".", " ")) and p.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))} for p in parts), "reserved artifact path")
    current = Path(root)
    # Check ancestors too: resolve() alone would erase evidence of symlinks.
    for component in [current, *current.parents]:
        if component.exists() or component.is_symlink():
            info = component.lstat()
            require(not component.is_symlink() and not (getattr(info, "st_file_attributes", 0) & 0x400), "symlink/reparse point forbidden")
    for part in parts:
        current = current / part
        if current.exists() or current.is_symlink():
            info = current.lstat()
            require(not current.is_symlink() and not (getattr(info, "st_file_attributes", 0) & 0x400), "symlink/reparse point forbidden")
    return current
