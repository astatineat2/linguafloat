"""Bootstrap supports the development tree and the relocatable runtime."""
import sys
from pathlib import Path

# PyInstaller's Windows DLL search directory can be inherited by child processes.
# Restore normal search order before loading this independent runtime's libraries.
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetDllDirectoryW(None)

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src" if (root / "src").is_dir() else root.parent / "src"))

from linguafloat.document_worker import main

if __name__ == "__main__":
    main()
