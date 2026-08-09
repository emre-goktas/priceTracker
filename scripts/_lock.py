from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

LOCK_DIR = Path(__file__).resolve().parent.parent / "logs"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextmanager
def single_instance(name: str):
    """Aynı isimli bir çalıştırma hâlâ sürüyorsa (örn. bir site normalden çok yavaşsa,
    bir sonraki cron tetiklemesi üst üste binmesin) bu çalıştırmayı sessizce atlar."""
    LOCK_DIR.mkdir(exist_ok=True)
    lock_path = LOCK_DIR / f"{name}.lock"

    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip())
        except ValueError:
            pid = None
        if pid is not None and _pid_alive(pid):
            print(f"{name}: önceki çalıştırma hâlâ sürüyor (pid {pid}), bu çalıştırma atlanıyor")
            sys.exit(0)

    lock_path.write_text(str(os.getpid()))
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
