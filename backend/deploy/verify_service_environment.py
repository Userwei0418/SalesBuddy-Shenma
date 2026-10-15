"""Bounded native-systemd regression; only private files and 30-second probe units.

Run as root on Linux. No existing service, environment file, manager environment
or credentials are read/modified. Output contains assertion counts only.
"""
import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from inherit_service_environment import inherited_command


def main():
    units = []
    with tempfile.TemporaryDirectory(prefix="salegent-env-probe-", dir="/run") as directory:
        root = Path(directory)
        try:
            for remove_mode in (False, True):
                suffix = uuid.uuid4().hex[:12]
                source, probe = "salegent-env-source-" + suffix, "salegent-env-check-" + suffix
                units.extend([source, probe])
                base, observed = root / (suffix + ".env"), root / (suffix + ".json")
                base.write_text("AUTH_MODE=password\n")
                base.chmod(0o600)
                # Deliberately exercises quotes, newline, $, backtick and backslash
                # in nonsecret fixture content without displaying any value.
                sentinel = 'fixture "quoted"\n$literal=`tick`\\tail'
                conditions = """import os,json
result={'direct_production':os.environ.get('APP_ENV')=='production',
'file_precedence':os.environ.get('AUTH_MODE')==EXPECTED_MODE,
'unset_final': 'DROP_SENTINEL' not in os.environ,
'direct_value_roundtrip':os.environ.get('DIRECT_SENTINEL')==EXPECTED_SENTINEL}
assert all(result.values()), 'Environment precedence mismatch'
""".replace("EXPECTED_MODE", repr(None if remove_mode else "password")) \
                    .replace("EXPECTED_SENTINEL", repr(sentinel))
                source_code = conditions + "\nfrom pathlib import Path;import time\nPath(" + repr(str(observed)) + ").write_text(json.dumps(result));time.sleep(30)"
                source_script, check_script = root / (suffix + "-source.py"), root / (suffix + "-check.py")
                source_script.write_text(source_code)
                check_script.write_text(conditions + "\nprint(json.dumps(result,sort_keys=True))")
                subprocess.run([
                    "systemd-run", "--quiet", "--collect", "--unit=" + source,
                    "--property=Environment=APP_ENV=production",
                    "--property=Environment=AUTH_MODE=demo",
                    "--property=Environment=DIRECT_SENTINEL=" + '"' + sentinel.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"',
                    "--property=Environment=DROP_SENTINEL=remove-fixture",
                    "--property=EnvironmentFile=" + str(base),
                    "--property=UnsetEnvironment=DROP_SENTINEL",
                    *( ["--property=UnsetEnvironment=AUTH_MODE"] if remove_mode else [] ),
                    sys.executable, str(source_script),
                ], check=True, capture_output=True, text=True, timeout=10)
                for _ in range(30):
                    if observed.exists():
                        break
                    time.sleep(0.1)
                assert observed.exists(), "Probe source did not initialize"
                invocation = inherited_command(source + ".service", probe,
                    [sys.executable, str(check_script)],
                    direct_environment_file=root / (suffix + "-direct.env"))
                result = subprocess.run(invocation, check=True, capture_output=True, text=True, timeout=10)
                assert json.loads(result.stdout) == json.loads(observed.read_text())
            # A mistakenly supplied persistent service must also finish the
            # operator's check and terminate its child, not leave an SSH waiter.
            bounded = "salegent-env-timeout-" + uuid.uuid4().hex[:12]
            units.append(bounded)
            child_file, sleeper = root / "child.pid", root / "sleep.py"
            sleeper.write_text("import subprocess,sys,time\nfrom pathlib import Path\n"
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                "Path(" + repr(str(child_file)) + ").write_text(str(p.pid))\ntime.sleep(60)\n")
            invocation = inherited_command(source + ".service", bounded,
                [sys.executable, str(sleeper)], max_runtime=1,
                direct_environment_file=root / "timeout-direct.env")
            started = time.monotonic()
            result = subprocess.run(invocation, check=False, capture_output=True, text=True, timeout=10)
            assert result.returncode != 0 and time.monotonic() - started < 8
            assert child_file.exists(), "Bounded probe did not start"
            child_stat = Path("/proc") / child_file.read_text() / "stat"
            assert not child_stat.exists() or child_stat.read_text().split(") ", 1)[1].startswith("Z "), "Probe child still running"
            print(json.dumps({"passed": 3, "checks": ["native_direct_file_precedence_roundtrip", "native_final_unset_precedence", "native_probe_timeout_stops_process_group"]}))
        finally:
            for unit in units:
                subprocess.run(["systemctl", "stop", unit + ".service"], check=False,
                               capture_output=True, timeout=10)


if __name__ == "__main__":
    main()
