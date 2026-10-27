"""Run preparation with the service's Environment/Files/Pass/Unset precedence.

Direct Environment values use a private temporary file, never argv or output.
Existing EnvironmentFile contents are read only by systemd itself. Unknown metadata
or value-specific UnsetEnvironment fails closed before deployment stops.
"""

import argparse
import json
import os
import grp
import pwd
import re
import tempfile
from pathlib import Path
import subprocess
import sys


def environment_files(value):
    """Fail closed on unsupported/escaped metadata instead of dropping a file."""
    pattern = re.compile(r"(/[^\s()\\]+) \(ignore_errors=(yes|no)\)")
    files = []
    cursor = 0
    for match in pattern.finditer(value):
        if value[cursor:match.start()].strip():
            raise ValueError("Unsupported systemd EnvironmentFiles metadata")
        path, optional = match.groups()
        files.append(("-" if optional == "yes" else "") + path)
        cursor = match.end()
    if value[cursor:].strip() or not files:
        raise ValueError("No complete systemd EnvironmentFiles metadata")
    return files


def service_property(service, property_name, *, runner, value_type="as"):
    # systemd's unit object path escapes every non-alphanumeric byte as _xx.
    suffix = ''.join(char if char.isascii() and char.isalnum() else f"_{ord(char):02x}" for char in service)
    try:
        result = runner([
            "busctl", "--system", "--json=short", "get-property", "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1/unit/" + suffix, "org.freedesktop.systemd1.Service", property_name,
        ], check=True, capture_output=True, text=True, timeout=10)
        value = json.loads(result.stdout)
        data = value.get("data")
        valid = ((value_type == "as" and isinstance(data, list) and all(isinstance(item, str) for item in data))
                 or (value_type == "s" and isinstance(data, str))
                 or (value_type == "b" and isinstance(data, bool)))
        if value.get("type") != value_type or not valid:
            raise ValueError
        return data
    except (ValueError, AttributeError, subprocess.SubprocessError):
        # Do not include property values or command output in the exception.
        raise ValueError("Unable to read complete systemd environment metadata") from None


def service_identity(service, *, runner):
    """Only explicit, static non-root identities can validate private file access."""
    dynamic = service_property(service, "DynamicUser", runner=runner, value_type="b")
    user = service_property(service, "User", runner=runner, value_type="s")
    group = service_property(service, "Group", runner=runner, value_type="s")
    name = r"(?:[A-Za-z_][A-Za-z0-9_.-]*\$?|[0-9]+)"
    if dynamic or not re.fullmatch(name, user) or not re.fullmatch(name, group):
        raise ValueError("Service probes require explicit static User and Group")
    try:
        account = pwd.getpwuid(int(user)) if user.isdecimal() else pwd.getpwnam(user)
        membership = grp.getgrgid(int(group)) if group.isdecimal() else grp.getgrnam(group)
        if account.pw_uid <= 0 or membership.gr_gid <= 0:
            raise ValueError
        return account.pw_uid, membership.gr_gid
    except (KeyError, ValueError, OverflowError):
        raise ValueError("Service probes require a resolvable non-root User and Group") from None


def inherited_command(service, unit, command, *, extra_files=(), direct_environment_file=None, max_runtime=120,
                      as_service_user=False, runner=subprocess.run):
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", service):
        raise ValueError("Invalid service name")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", unit):
        raise ValueError("Invalid transient unit name")
    if not command or not command[0].startswith("/"):
        raise ValueError("An absolute executable path is required")
    if not isinstance(max_runtime, int) or not 1 <= max_runtime <= 3600:
        raise ValueError("Probe runtime must be between 1 and 3600 seconds")
    identity_properties = []
    if as_service_user:
        uid, gid = service_identity(service, runner=runner)
        identity_properties = [f"--property=User={uid}", f"--property=Group={gid}"]
        command = [sys.executable, str(Path(__file__).with_name("verify_service_identity.py")),
                   "--uid", str(uid), "--gid", str(gid), "--", *command]
    result = runner(
        ["systemctl", "show", service, "--property=EnvironmentFiles", "--value"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    files = environment_files(result.stdout)
    direct = service_property(service, "Environment", runner=runner)
    passed = service_property(service, "PassEnvironment", runner=runner)
    unset = service_property(service, "UnsetEnvironment", runner=runner)
    name_pattern = r"[A-Za-z_][A-Za-z0-9_]*"
    # NAME=value unsetting requires systemd's final value comparison. Never turn
    # it into unconditional deletion or expose the comparison value in argv.
    if any(not re.fullmatch(name_pattern, name) for name in [*passed, *unset]):
        raise ValueError("Unsupported systemd PassEnvironment/UnsetEnvironment; use variable names only")
    overrides = {}
    for assignment in direct:
        name, separator, value = assignment.partition("=")
        if not separator or not re.fullmatch(name_pattern, name) or "\0" in value:
            raise ValueError("Unsupported systemd Environment metadata")
        overrides[name] = value
    if overrides:
        if direct_environment_file is None:
            raise ValueError("Environment inheritance requires a private temporary file")
        # Systemd EnvironmentFile double quotes preserve embedded whitespace/newlines;
        # backslash escapes backslash, quote, dollar and backtick. No shell evaluates it.
        def quoted(value):
            for char in ("\\", '"', "$", "`"):
                value = value.replace(char, "\\" + char)
            return '"' + value + '"'
        content = ''.join(name + "=" + quoted(value) + "\n" for name, value in overrides.items())
        descriptor = os.open(direct_environment_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(content)
        files.insert(0, str(direct_environment_file))
    for path in extra_files:
        if not re.fullmatch(r"/[^\s()\\]+", path):
            raise ValueError("Invalid additional environment file name")
        if path not in files:
            files.append(path)
    return [
        "systemd-run", "--quiet", "--collect", "--pipe", "--wait", "--unit=" + unit,
        "--property=RuntimeMaxSec=" + str(max_runtime), "--property=TimeoutStopSec=5",
        *identity_properties,
        *("--property=PassEnvironment=" + name for name in passed),
        *("--property=EnvironmentFile=" + path for path in files),
        *("--property=UnsetEnvironment=" + name for name in unset), *command,
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--extra-environment-file", action="append", default=[])
    parser.add_argument("--max-runtime", type=int, default=120, help="Probe lifetime in seconds (default: 120)")
    parser.add_argument("--as-service-user", action="store_true",
                        help="Require and verify the service's explicit static non-root User/Group")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    # The parent directory is 0700 and the file 0600. systemd reads it at process
    # launch; synchronous --wait keeps it available until the probe has exited.
    # /run is ephemeral on the deployment host; TemporaryDirectory also cleans on failure.
    with tempfile.TemporaryDirectory(prefix="salegent-service-env-", dir="/run") as directory:
        invocation = inherited_command(args.service, args.unit, command,
            extra_files=args.extra_environment_file, direct_environment_file=Path(directory) / "direct.env",
            max_runtime=args.max_runtime, as_service_user=args.as_service_user)
        # The operator explicitly supplies the absolute executable; no shell is used.
        # systemd bounds the service and its process group even if this client dies.
        try:
            return subprocess.run(invocation, check=False, timeout=args.max_runtime + 30).returncode  # noqa: S603
        except subprocess.TimeoutExpired:
            raise SystemExit("Service probe exceeded its bounded runtime") from None


if __name__ == "__main__":
    raise SystemExit(main())
