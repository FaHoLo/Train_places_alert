"""Logs collector.

This module is used to collect logs from Redis db and save them localy.
The need for this utility appeared due of using free Redis Labs database
which allows to store only 30 MB of data, so we can clear the database
from logs that take up the most space.

Module needs environment variables:
    Redis:
        DB_HOST: Database host.
        DB_PORT: Database port.
        DB_PASS: Database password.
        LOGS_KEY: Key in database where list with logs are located.

    LOGS_PATH: Path to json log file, where logs will be stored.

Examples:
    $ python3 collect_logs.py

"""

import os
import json
from typing import List, Optional

import redis
from dotenv import load_dotenv


load_dotenv()

db = redis.Redis(
    host=os.environ['DB_HOST'],
    port=int(os.environ['DB_PORT']),
    password=os.environ['DB_PASS'],
)


def main():
    """Fetch logs from db, add them to local file, delete logs from db."""
    logs_db_key = os.environ['LOGS_KEY']
    logs_path = os.environ['LOGS_PATH']
    if download_logs(logs_db_key, logs_path):
        db.delete(logs_db_key)


def download_logs(logs_key: str, logs_path: str) -> bool:
    """Download logs from db and add them to json file.

    Args:
        logs_key: Key in db where list with logs are located.
        logs_path: Path to json file.

    Returns:
        logs_downloaded: download status
    """
    db_logs = get_logs_from_db(db, logs_key)
    if not db_logs:
        print('No new logs')
        logs_downloaded = False
    update_log_file(logs_path, db_logs)
    print('Logs downloaded')
    logs_downloaded = True
    return logs_downloaded


def get_logs_from_db(db: redis.Redis, logs_key: str) -> List[dict]:
    """Get logs from list key in db.

    Args:
        db: Redis db connection.
        logs_key: Key in db where list with logs are located.

    Returns:
        logs: decoded logs.
    """
    logs = db.lrange(logs_key, 0, -1)
    logs = [json.loads(log.decode('UTF-8')) for log in logs]
    return logs


def update_log_file(file_path: str, logs: List[dict]) -> None:
    """Update log file with logs.

    Args:
        filepath: Path to log file.
        logs: Fetched logs.

    Returns:
        None
    """
    stored_logs = get_logs_from_file(file_path)
    if not stored_logs:
        stored_logs = []
    stored_logs.extend(logs)

    logs_json = json.dumps(stored_logs)
    with open(file_path, 'w') as json_file:
        json_file.write(logs_json)


def get_logs_from_file(file_path: str) -> Optional[List[dict]]:
    """Get logs from json log file.

    Args:
        file_path: Path to json file.

    Returns:
        logs: logs from file.
    """
    if not os.path.exists(file_path):
        return None
    with open(file_path, 'r') as log_file:
        logs_json = log_file.read()
    return json.loads(logs_json)


if __name__ == '__main__':
    main()
