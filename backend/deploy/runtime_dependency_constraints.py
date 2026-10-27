"""Preserve the installed stack, replacing only the reviewed credential dependency set."""
import argparse
from importlib.metadata import distributions
import json
from pathlib import Path
import re
import tomllib

REVIEWED = frozenset({"cryptography", "cffi", "pycparser"})
EXCLUDED = frozenset({"sales-assistant-backend", "pip", "setuptools"})


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_dependencies(path):
    with Path(path).open("rb") as stream:
        packages = tomllib.load(stream)["package"]
    selected = {}
    for package in packages:
        name = canonical(package["name"])
        if name not in REVIEWED:
            continue
        version = package.get("version", "")
        if name in selected or not re.fullmatch(r"[0-9][A-Za-z0-9.!+_-]*", version):
            raise ValueError("Credential dependency lock is missing or ambiguous")
        if any(canonical(item["name"]) not in REVIEWED for item in package.get("dependencies", [])):
            raise ValueError("Credential dependency closure changed; review required")
        selected[name] = version
    if selected.keys() != REVIEWED:
        raise ValueError("Credential dependency lock is incomplete")
    return selected


def installed_versions():
    return {canonical(item.metadata["Name"]): item.version for item in distributions()}


def build_constraints(lock, installed):
    preserved = {canonical(name): version for name, version in installed.items()
                 if canonical(name) not in EXCLUDED | REVIEWED}
    return {**preserved, **locked_dependencies(lock)}


def verify_constraints(lock, constraints, installed):
    locked = locked_dependencies(lock)
    if any(installed.get(name) != version for name, version in locked.items()):
        raise ValueError("Installed credential dependencies differ from the release lock")
    # Old unused packages need not be installed into the new runtime. Packages
    # that remain installed must retain the previous versions, outside REVIEWED.
    for line in Path(constraints).read_text().splitlines():
        name, separator, version = line.partition("==")
        if not separator or (name in installed and installed[name] != version):
            raise ValueError("Installed dependency differs from the preserved stack")
    return {"status": "ready", "locked_dependencies": locked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--lock", required=True)
    parser.add_argument("--constraints", required=True)
    args = parser.parse_args()
    try:
        installed = installed_versions()
        if args.action == "build":
            versions = build_constraints(args.lock, installed)
            Path(args.constraints).write_text(''.join(f"{name}=={version}\n" for name, version in sorted(versions.items())))
        else:
            print(json.dumps(verify_constraints(args.lock, args.constraints, installed), sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("RELEASE_DEPENDENCY_CONSTRAINTS_FAILED") from None


if __name__ == "__main__":
    main()
