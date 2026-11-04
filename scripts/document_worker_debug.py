"""Opt-in stack-only diagnostics for synthetic pipeline tests."""
import faulthandler
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from linguafloat.document_worker import main

if __name__ == "__main__":
    with (root / "verification" / "worker-stack.txt").open("w") as trace:
        faulthandler.dump_traceback_later(20, repeat=True, file=trace)
        try:
            main()
        finally:
            faulthandler.cancel_dump_traceback_later()
