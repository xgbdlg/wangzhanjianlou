#!/usr/bin/env python3
"""PyInstaller packaging script — python build/build_backend.py"""
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
BACKEND = ROOT / "backend"

def build():
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller", "-q"])
    print("Building CSGOEmpireSniper...")
    subprocess.run([
        sys.executable, "-m", "PyInstaller",
        "--name", "CSGOEmpireSniper", "--onedir", "--console", "--clean", "--noconfirm",
        "--add-data", f"{BACKEND};backend",
        "--hidden-import", "sqlalchemy.ext.asyncio",
        "--hidden-import", "aiosqlite", "--hidden-import", "cryptography",
        "--hidden-import", "httpx", "--hidden-import", "websockets",
        "--hidden-import", "pydantic", "--hidden-import", "fastapi",
        "--hidden-import", "uvicorn", "--hidden-import", "logging.handlers",
        "--collect-all", "fastapi", "--collect-all", "pydantic",
        str(BACKEND / "main.py"),
    ], cwd=str(ROOT), check=True)
    print(f"\nDone: {ROOT / 'dist' / 'CSGOEmpireSniper'}")

if __name__ == "__main__":
    build()
