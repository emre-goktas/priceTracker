#!/bin/bash
set -e

# configs/schedule.yaml'dan crontab'ı tazeler (idempotent) - her container başlangıcında
# (restart dahil) çalışır, schedule.yaml'daki değişiklikler bir sonraki restart'ta uygulanır.
python -m scripts.install_cron
exec cron -f
