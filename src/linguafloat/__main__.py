import sys
from pathlib import Path


def main() -> int:
    if getattr(sys, 'frozen', False) and sys.platform == 'win32':
        from linguafloat.setup_gate import handle_blocked_start
        args = iter(sys.argv[1:])
        health_job = next(args, None) if '--update-health' in args else None
        if handle_blocked_start(Path(sys.executable).resolve().parent, Path(health_job) if health_job else None):
            return 0
    from linguafloat.app import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

