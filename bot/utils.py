import datetime
import os
import traceback

from aiogram import Bot
from redis import Redis


_log_bot = None

_db_connetion = None


async def remove_search_from_db(chat_id):
    _db_connetion.delete(chat_id)


def get_log_traceback(logger_name):
    timezone_offset = datetime.timedelta(hours=3)  # Moscow
    time = datetime.datetime.utcnow() + timezone_offset
    tb = traceback.format_exc()
    exception_text = f'{time} - {logger_name} - ERROR\n{tb}'
    return exception_text


async def handle_exception(log_bot, logger_name, text=None):
    log_traceback = get_log_traceback(logger_name)
    if text:
        log_traceback += '\n' + text
    await send_error_log_async_to_telegram(log_bot, log_traceback)


def get_db_connection():
    global _db_connetion
    if not _db_connetion:
        _db_connetion = Redis(
            host=os.environ['DB_HOST'],
            port=os.environ['DB_PORT'],
            password=os.environ['DB_PASS'],
        )
    return _db_connetion


def split_text_on_parts(text, message_max_length):
    parts = []
    while text:
        if len(text) <= message_max_length:
            parts.append(text)
            break
        part = text[:message_max_length]
        first_lnbr = part.rfind('\n')
        if first_lnbr != -1:
            parts.append(part[:first_lnbr])
            text = text[first_lnbr+1:]
        else:
            parts.append(part)
            text = text[message_max_length:]
    return parts


def get_logger_bot():
    global _log_bot
    if not _log_bot:
        tg_bot_token = os.environ.get('TG_LOG_BOT_TOKEN')
        proxy = os.environ.get('TG_PROXY')
        _log_bot = Bot(token=tg_bot_token, proxy=proxy)
    return _log_bot


async def send_error_log_async_to_telegram(logger_bot, text):
    chat_id = os.environ.get('TG_LOG_CHAT_ID')
    message_max_length = 4096

    if len(text) <= message_max_length:
        await logger_bot.send_message(chat_id, text)
        return

    parts = split_text_on_parts(text, message_max_length)
    for part in parts:
        await logger_bot.send_message(chat_id, part)
