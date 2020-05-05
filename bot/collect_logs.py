import os
import json
import redis
import pprint
from dotenv import load_dotenv
load_dotenv()


db = redis.Redis(
    host=os.environ['DB_HOST'],
    port=os.environ['DB_PORT'],
    password=os.environ['DB_PASS'],
)


def main():
    logs_db_key = os.environ['LOGS_KEY']
    logs_path = os.environ['LOGS_PATH']
    download_logs(logs_db_key, logs_path)
    db.delete(logs_db_key)

def download_logs(logs_key, logs_path):
    db_logs = get_logs_from_db(db, logs_key)
    if not db_logs:
        print('No new logs')
        return
    update_log_file(logs_path, db_logs)

def get_logs_from_db(db, logs_key):
    logs = db.lrange(logs_key, 0, -1)
    logs = [ json.loads(log.decode('UTF-8')) for log in logs]
    return logs

def update_log_file(file_path, logs):
    stored_logs = get_logs_from_file(file_path)
    if not stored_logs:
        stored_logs = []
    stored_logs.extend(logs)

    logs_json = json.dumps(stored_logs)
    with open(file_path, 'w') as json_file:
        json_file.write(logs_json)

def get_logs_from_file(file_path):
    if not os.path.exists(file_path):
        return
    with open(file_path, 'r') as log_file:
        logs_json = log_file.read()
    return json.loads(logs_json)


if __name__ == '__main__':
    main()
