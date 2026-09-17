from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


QUOTA_PATH = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"

# AGY refuses a local request that carries no CSRF token, under this header
# name. Nothing standard matches it, which is why a release that added the
# check read as "the endpoint is down" from out here.
CSRF_HEADER = "x-codeium-csrf-token"

# The token is minted per run and never written to disk. AGY does put it in
# the environment of every process it spawns, along with the address of the
# language server it belongs to, and those processes are ours to read. That is
# the only supply we have, and it is the same one AGY's own tooling uses.
TOKEN_VARIABLE = "ANTIGRAVITY_CSRF_TOKEN"
ADDRESS_VARIABLE = "ANTIGRAVITY_LS_ADDRESS"
CLI_START_TIMEOUT = 30.0
CLI_START_POLL_INTERVAL = 0.25
CLI_START_COOLDOWN = 300.0


@dataclass(frozen=True)
class AgyQuotaWindow:
    group_id: str
    group_label: str
    cadence: str
    used_percent: int
    remaining_fraction: float
    reset_at: Optional[str]


@dataclass(frozen=True)
class AgyQuotaSnapshot:
    updated_at: str
    windows: tuple[AgyQuotaWindow, ...]


def default_cache_path() -> Path:
    return Path(
        os.environ.get(
            "AGY_RATE_CACHE",
            Path.home() / ".cache" / "rate-limit-indicator" / "agy-quota.json",
        )
    )


def write_cache(
    snapshot: AgyQuotaSnapshot,
    cache_path: Optional[Path] = None,
) -> Path:
    path = cache_path or default_cache_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    payload = {
        "updated_at": snapshot.updated_at,
        "windows": [asdict(window) for window in snapshot.windows],
    }
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def read_cache(cache_path: Optional[Path] = None) -> Optional[AgyQuotaSnapshot]:
    path = cache_path or default_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        windows = tuple(
            AgyQuotaWindow(
                group_id=str(item["group_id"]),
                group_label=str(item["group_label"]),
                cadence=str(item["cadence"]),
                used_percent=int(item["used_percent"]),
                remaining_fraction=float(item["remaining_fraction"]),
                reset_at=str(item["reset_at"]) if item.get("reset_at") else None,
            )
            for item in payload.get("windows", [])
            if isinstance(item, Mapping)
        )
        return AgyQuotaSnapshot(
            updated_at=str(payload["updated_at"]),
            windows=windows,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


@dataclass(frozen=True)
class AgyCredentials:
    """What a live AGY will accept a quota request with."""

    token: str
    address: Optional[str] = None

    @property
    def port(self) -> Optional[int]:
        if not self.address:
            return None
        _, separator, port = self.address.rpartition(":")
        return int(port) if separator and port.isdigit() else None


def find_agy_credentials(proc_root: Optional[Path] = None) -> Optional[AgyCredentials]:
    """Borrow the CSRF token from a process AGY started.

    Reading another process's environment is a strong thing to do, so this
    only ever reads processes belonging to this user, only looks for these two
    variables, and never logs what it finds. The alternative is no Gemini
    numbers at all: the token exists for one run of AGY, is never written
    down, and is not accepted from the outside.

    Linux only. /proc is where this lives, and its absence is a "no token"
    rather than an error, so macOS keeps whatever the cache holds.
    """

    override = os.environ.get("AGY_CSRF_TOKEN", "").strip()
    if override:
        return AgyCredentials(
            token=override, address=os.environ.get("AGY_LS_ADDRESS", "").strip() or None
        )

    root = proc_root or Path("/proc")
    try:
        entries = sorted(
            (entry for entry in root.iterdir() if entry.name.isdigit()),
            key=lambda entry: int(entry.name),
        )
    except OSError:
        return None

    uid = os.getuid()
    for entry in entries:
        try:
            if entry.stat().st_uid != uid:
                continue
            raw = (entry / "environ").read_bytes()
        except OSError:
            # Processes come and go while this loop runs, and other users'
            # are not ours to read. Both are ordinary.
            continue
        values = {}
        for item in raw.split(b"\0"):
            name, separator, value = item.partition(b"=")
            if separator and name.decode("utf-8", "replace") in (
                TOKEN_VARIABLE,
                ADDRESS_VARIABLE,
            ):
                values[name.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
        token = values.get(TOKEN_VARIABLE, "").strip()
        if token:
            return AgyCredentials(
                token=token, address=values.get(ADDRESS_VARIABLE, "").strip() or None
            )
    return None


def find_agy_ports() -> tuple[int, ...]:
    try:
        processes = subprocess.run(
            ["pgrep", "-x", "agy"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot find the AGY process: {exc}") from exc

    pids = [
        line.strip() for line in processes.stdout.splitlines() if line.strip().isdigit()
    ]
    if not pids:
        raise RuntimeError("AGY is not running")

    ports: list[int] = []
    for pid in pids:
        try:
            result = subprocess.run(
                [
                    "lsof",
                    "-nP",
                    "-a",
                    "-p",
                    pid,
                    "-iTCP",
                    "-sTCP:LISTEN",
                    "-Fn",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"cannot inspect AGY listening ports: {exc}") from exc
        for line in result.stdout.splitlines():
            if not line.startswith("n"):
                continue
            address = line[1:]
            host, separator, port_text = address.rpartition(":")
            if (
                separator
                and host in {"127.0.0.1", "localhost"}
                and port_text.isdigit()
                and int(port_text) not in ports
            ):
                ports.append(int(port_text))
    if not ports:
        raise RuntimeError("AGY is running but has no localhost listening port")
    return tuple(ports)


# AGY listens on more than one local port and does not speak the same thing on
# all of them: one is TLS, another is plain HTTP. Which is which changes
# between releases, so both are tried rather than guessed at.
SCHEMES = ("https", "http")

# How much a failure is worth saying out loud. A server that answered knows
# why it refused us; a transport error only knows it could not ask. The
# "wrong version number" from sending TLS to a plaintext port is the least
# informative of all -- it is this function's own doing, not a fault -- and
# reporting it is what hid a plain 401 for a whole release.
ERROR_ANSWERED = 3
ERROR_TRANSPORT = 2
ERROR_WRONG_SCHEME = 1


def _error_rank(exc: Exception) -> int:
    if isinstance(exc, urllib.error.HTTPError):
        return ERROR_ANSWERED
    if isinstance(exc, ssl.SSLError) or isinstance(
        getattr(exc, "reason", None), ssl.SSLError
    ):
        return ERROR_WRONG_SCHEME
    return ERROR_TRANSPORT


def describe_error(exc: Exception) -> str:
    """What to show for a failed attempt, preferring the server's own words.

    A Connect RPC refusal carries a JSON body saying what was wrong -- a
    missing CSRF token, an expired session -- and that sentence is the whole
    reason anybody reads this message. Without it the reader gets "HTTP Error
    401: Unauthorized", which says who refused but not what to do.
    """

    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            message = body.get("message") or body.get("code")
            detail = f" {message}" if message else ""
        except (AttributeError, ValueError, OSError, json.JSONDecodeError):
            detail = ""
        return f"HTTP {exc.code}{detail}"
    return str(exc)


def quota_ports(
    ports: Optional[tuple[int, ...]] = None,
    credentials: Optional[AgyCredentials] = None,
) -> tuple[int, ...]:
    """The ports to try, with the one the token belongs to first.

    AGY listens on several, and the token is minted for the language server at
    ANTIGRAVITY_LS_ADDRESS. Trying that one first is what turns four requests
    into one.
    """

    found = tuple(ports or find_agy_ports())
    port = credentials.port if credentials else None
    if port is None:
        return found
    return (port,) + tuple(other for other in found if other != port)


def quota_headers(credentials: Optional[AgyCredentials], base: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
    }
    if credentials:
        # Origin and Referer ride along because AGY's own client sends them,
        # and a CSRF check that is satisfied by a header alone today may well
        # want the pair tomorrow.
        headers[CSRF_HEADER] = credentials.token
        headers["Origin"] = base
        headers["Referer"] = base + "/"
    return headers


def fetch_quota_snapshot(
    ports: Optional[tuple[int, ...]] = None,
    *,
    timeout: float = 3.0,
    credentials: Optional[AgyCredentials] = None,
) -> AgyQuotaSnapshot:
    credentials = credentials or find_agy_credentials()
    candidates = quota_ports(ports, credentials)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    payload = json.dumps({"forceRefresh": True}).encode("utf-8")
    best_error: Optional[Exception] = None
    best_rank = 0

    for port in candidates:
        for scheme in SCHEMES:
            base = f"{scheme}://127.0.0.1:{port}"
            request = urllib.request.Request(
                f"{base}{QUOTA_PATH}",
                data=payload,
                headers=quota_headers(credentials, base),
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=timeout, context=context
                ) as response:
                    body = json.loads(response.read().decode("utf-8"))
                snapshot = parse_quota_payload(body)
                if snapshot.windows:
                    return snapshot
            except (
                OSError,
                TimeoutError,
                ValueError,
                json.JSONDecodeError,
                urllib.error.URLError,
            ) as exc:
                rank = _error_rank(exc)
                # Ties keep the first: the ports come back in listening order,
                # and the first one to answer is the one to talk about.
                if rank > best_rank:
                    best_error, best_rank = exc, rank

    if best_error is None:
        raise RuntimeError("AGY quota endpoint is unavailable")
    # Described once: an HTTPError's body is a stream, and reading it twice
    # gets an empty string the second time.
    description = describe_error(best_error)
    if credentials is None and _is_csrf_refusal(best_error, description):
        # Naming the cause is the difference between "AGY is broken" and "open
        # AGY once, so that something it starts is carrying the token".
        raise RuntimeError("AGY quota needs a CSRF token; none is available yet")
    return_message = f"AGY quota endpoint is unavailable: {description}"
    raise RuntimeError(return_message)


def _is_csrf_refusal(exc: Exception, description: str) -> bool:
    return (
        isinstance(exc, urllib.error.HTTPError)
        and exc.code == 401
        and "CSRF" in description.upper()
    )


def default_start_stamp_path() -> Path:
    return Path(
        os.environ.get(
            "AGY_START_STAMP",
            Path.home() / ".cache" / "rate-limit-indicator" / "agy-start-attempt",
        )
    )


def start_is_in_cooldown(
    stamp_path: Optional[Path] = None,
    *,
    now: Optional[float] = None,
) -> bool:
    path = stamp_path or default_start_stamp_path()
    now = time.time() if now is None else now
    try:
        last_attempt = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    # A clock that jumped backwards must not lock the CLI out until it catches
    # up, so a negative age counts as no cooldown at all.
    return 0.0 <= now - last_attempt < CLI_START_COOLDOWN


def record_start_attempt(
    stamp_path: Optional[Path] = None,
    *,
    now: Optional[float] = None,
) -> None:
    path = stamp_path or default_start_stamp_path()
    now = time.time() if now is None else now
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(f"{now}\n", encoding="utf-8")
    except OSError:
        # Losing the stamp costs the cooldown, not the read. Failing the whole
        # poll over an unwritable cache directory would be worse.
        pass


def find_agy_cli() -> Optional[str]:
    """Locate the Antigravity CLI, tolerating a launcher with a bare PATH."""

    override = (os.environ.get("AGY_CLI") or "").strip()
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which("agy")
    if found:
        return found
    for candidate in (
        Path.home() / ".local" / "bin" / "agy",
        Path("/opt/homebrew/bin/agy"),
        Path("/usr/local/bin/agy"),
    ):
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def fetch_quota_with_cli(
    *,
    enabled: Optional[bool] = None,
    timeout: float = 3.0,
    spawner: Optional[Any] = None,
    stamp_path: Optional[Path] = None,
    now: Optional[float] = None,
    sleep: Any = time.sleep,
) -> Optional[AgyQuotaSnapshot]:
    """Start the Antigravity CLI briefly and read quota while it listens.

    `agy models` is used because it authenticates, opens the local quota server
    within about a second, and spends no model quota. That server only lives for
    the few seconds the command runs, so the read has to race it rather than
    start it and come back later. A cold start also has to clear macOS keyring
    authentication first, which is why the deadline is generous.
    """

    # Gemini has no poller of its own, so the switch reaches this module from
    # the shared config through the caller. The environment is the fallback for
    # anyone driving agy_rate directly.
    if enabled is None:
        enabled = _flag_enabled(os.environ.get("AGY_AUTO_START"))
    if not enabled:
        return None
    agy_bin = find_agy_cli()
    if agy_bin is None:
        return None

    now = time.time() if now is None else now
    stamp = stamp_path or default_start_stamp_path()
    # Stamping before the spawn means a hang or a crash still counts, so a CLI
    # that cannot sign in does not earn a process on every poll.
    if start_is_in_cooldown(stamp, now=now):
        return None
    record_start_attempt(stamp, now=now)

    spawn = spawner or _spawn_agy_models
    try:
        process = spawn(agy_bin)
    except Exception:
        return None

    try:
        deadline = time.monotonic() + CLI_START_TIMEOUT
        while True:
            try:
                return fetch_quota_snapshot(timeout=timeout)
            except RuntimeError:
                if time.monotonic() >= deadline:
                    return None
                sleep(CLI_START_POLL_INTERVAL)
    finally:
        _stop_process(process)


def _spawn_agy_models(agy_bin: str) -> subprocess.Popen:
    return subprocess.Popen(
        (agy_bin, "models"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_process(process: Any) -> None:
    """Stop the CLI once its quota server has served its purpose."""

    try:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    except Exception:
        pass


def _flag_enabled(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def parse_quota_payload(
    payload: Mapping[str, Any],
    *,
    updated_at: Optional[str] = None,
) -> AgyQuotaSnapshot:
    response = payload.get("response")
    if not isinstance(response, Mapping):
        response = payload
    groups = response.get("groups")
    if not isinstance(groups, list):
        groups = []

    windows: list[tuple[int, int, AgyQuotaWindow]] = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        group_name = str(group.get("displayName") or "").strip()
        group_id, group_label, group_rank = _classify_group(group_name)
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, Mapping) or bucket.get("disabled") is True:
                continue
            remaining = _as_float(bucket.get("remainingFraction"))
            if remaining is None:
                continue
            cadence, cadence_rank = _classify_cadence(
                str(bucket.get("bucketId") or ""),
                str(bucket.get("displayName") or ""),
            )
            if cadence is None:
                continue
            remaining = min(1.0, max(0.0, remaining))
            windows.append(
                (
                    group_rank,
                    cadence_rank,
                    AgyQuotaWindow(
                        group_id=group_id,
                        group_label=group_label,
                        cadence=cadence,
                        used_percent=round((1.0 - remaining) * 100),
                        remaining_fraction=remaining,
                        reset_at=(
                            str(bucket["resetTime"])
                            if bucket.get("resetTime")
                            else None
                        ),
                    ),
                )
            )

    ordered = tuple(item[2] for item in sorted(windows, key=lambda item: item[:2]))
    return AgyQuotaSnapshot(
        updated_at=updated_at or datetime.now(timezone.utc).isoformat(),
        windows=ordered,
    )


def _classify_group(name: str) -> tuple[str, str, int]:
    lowered = name.lower()
    if "gemini" in lowered:
        return "gemini", "Gemini", 0
    if "claude" in lowered or "gpt" in lowered:
        return "claude-gpt", "Claude/GPT", 1
    return _slug(name) or "quota", name or "Quota", 2


def _classify_cadence(bucket_id: str, display_name: str) -> tuple[Optional[str], int]:
    value = f"{bucket_id} {display_name}".lower().replace("_", "-")
    if (
        "5h" in value
        or "five hour" in value
        or "five-hour" in value
        or "session" in value
    ):
        return "5h", 0
    if "weekly" in value or "7d" in value:
        return "7d", 1
    return None, 2


def _slug(value: str) -> str:
    return "-".join(value.lower().split())


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
