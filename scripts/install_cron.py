from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEDULE_PATH = PROJECT_ROOT / "configs" / "schedule.yaml"
LOG_DIR = PROJECT_ROOT / "logs"

BEGIN_MARKER = "# BEGIN price-bot"
END_MARKER = "# END price-bot"


def _cron_line(time_str: str, module: str, log_name: str) -> str:
    hh, mm = time_str.split(":")
    python = sys.executable
    return (
        f"{int(mm)} {int(hh)} * * * cd {PROJECT_ROOT} && {python} -m {module} "
        f">> {LOG_DIR / log_name} 2>&1"
    )


def build_block() -> str:
    """configs/schedule.yaml'dan crontab bloğu üretir - saatler koda hardcode değil,
    schedule.yaml değişip bu script yeniden çalıştırılınca crontab otomatik güncellenir."""
    schedule = yaml.safe_load(SCHEDULE_PATH.read_text(encoding="utf-8"))
    tz = schedule["timezone"]

    lines = [BEGIN_MARKER, f"CRON_TZ={tz}"]
    for t in schedule["price_pipeline"]["times"]:
        lines.append(_cron_line(t, "scripts.run_price_pipeline", "price_pipeline.log"))
    for t in schedule["crawler_discovery"]["times"]:
        lines.append(_cron_line(t, "scripts.run_discovery", "discovery.log"))
    lines.append(END_MARKER)
    return "\n".join(lines)


def install(new_block: str) -> None:
    """Mevcut crontab'daki BEGIN/END işaretli bloğu değiştirir (idempotent, tekrar
    çalıştırılabilir) - işaretli blok yoksa sona ekler, crontab dışındaki satırlara dokunmaz."""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    current = result.stdout if result.returncode == 0 else ""
    lines = [line for line in current.splitlines()]

    if BEGIN_MARKER in lines:
        start = lines.index(BEGIN_MARKER)
        end = lines.index(END_MARKER)
        lines = lines[:start] + new_block.splitlines() + lines[end + 1:]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(new_block.splitlines())

    new_crontab = "\n".join(lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)


def main() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    block = build_block()
    install(block)
    print("Kuruldu:")
    print(block)
    print()
    print("Doğrulamak için: crontab -l")


if __name__ == "__main__":
    main()
