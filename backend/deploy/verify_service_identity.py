"""Verify effective service identity before executing a maintenance command."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--gid", required=True, type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if (args.uid <= 0 or args.gid <= 0 or os.geteuid() != args.uid or os.getegid() != args.gid
            or not command or not command[0].startswith("/")):
        raise SystemExit("SERVICE_PROBE_IDENTITY_MISMATCH")
    # No shell; the checked identity remains the identity of the final process.
    try:
        os.execv(command[0], command)
    except OSError:
        raise SystemExit("SERVICE_PROBE_EXEC_FAILED") from None


if __name__ == "__main__":
    main()
