"""
Entry point – initialise DB and start the scheduler.
"""

from storage.database import init_db
from config.settings import settings
from scheduler.job import start

if __name__ == "__main__":
    init_db(settings.db_url)
    start()
