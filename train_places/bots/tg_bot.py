"""Telegram bot module.

Bot needs environment varibles:
    TG_PROXY: Bot proxy (default = None).
    TG_BOT_TOKEN: Bot token.
    TG_LOG_BOT_TOKEN Telegram log bot token.
    TG_LOG_CHAT_ID: Telegram log chat id.
    DB_HOST: Redis database host.
    DB_PORT: Redis database port.
    DB_PASS: Redis database password.
    LOGS_KEY: Redis database key for list where logs will be stored (default = search_logs)

Handler list:
    errors_handler, send_welcome, send_help, cancel_handler,
    start_search, get_url, get_numbers, get_limit, answer_searching

All handlers except errors_handler returns None value.

"""

import datetime
import json
import os

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.redis import RedisStorage2
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils.exceptions import TerminatedByOtherGetUpdates
from dotenv import load_dotenv

from train_places.phrases import phrases
from train_places.utils import utils


load_dotenv()

# Logger bot
log_bot = utils.get_logger_bot()
# Logger name. Use it for errors handling with utils.handle_exception(LOGGER_NAME)
LOGGER_NAME = 'trains_bot_logger'

# DB conncetion
redis_db = utils.get_db_connection()

# bot settings
bot = Bot(token=os.environ['TG_BOT_TOKEN'], proxy=os.environ.get('TG_PROXY'))
dispatcher = Dispatcher(
    bot=bot,
    storage=RedisStorage2(
        host=os.environ['DB_HOST'],
        port=os.environ['DB_PORT'],
        password=os.environ['DB_PASS']
    ),
)


class SearchConv(StatesGroup):
    """Base states group of conversation with user."""

    typing_url = State()
    typing_numbers = State()
    choosing_limit = State()
    searching = State()


def main():
    """Start bot polling."""
    executor.start_polling(dispatcher)


@dispatcher.errors_handler()
async def errors_handler(update: types.Update, exception: Exception) -> bool:
    """Bot errors handler.

    Args:
        update: Update from error.
        exception: Raised exceptions.

    Returns:
        True: return True all the time.
    """
    if type(exception) == TerminatedByOtherGetUpdates:
        return True

    await utils.handle_exception(log_bot, LOGGER_NAME)
    return True


@dispatcher.message_handler(state='*', commands=['start'])
async def send_welcome(message: types.Message, state: FSMContext):
    """Start command handler for all states. Sends welcome message.

    Args:
        message: Message from user.
        state: User state in conversation.
    """
    current_state = await state.get_state()
    if current_state:
        await state.finish()
    await message.answer(phrases.welcome)


@dispatcher.message_handler(state='*', commands=['help'])
async def send_help(message: types.Message, state: FSMContext):
    """Help command handler for all states. Sends help message.

    Args:
        message: Message from user.
        state: User state in conversation.
    """
    current_state = await state.get_state()
    if current_state:
        await state.finish()

    await message.answer(phrases.help_half_1, disable_web_page_preview=True)
    await message.answer(phrases.help_half_2, disable_web_page_preview=True)


@dispatcher.message_handler(state='*', commands=['cancel'])
async def cancel_handler(message: types.Message, state: FSMContext):
    """Cancel command handler for all states. Cancels all states.

    Args:
        message: Message from user.
        state: User state in conversation.
    """
    current_state = await state.get_state()

    # TODO make refactoring here:
    if current_state == 'SearchConv:typing_url':
        await message.answer(phrases.cancel_msg)
    else:
        if not await check_for_existing_search(f'tg-{message.chat.id}'):
            await message.answer(phrases.useless_cancel)
        else:
            await utils.remove_search_from_db(f'tg-{message.chat.id}')
            await message.answer(phrases.cancel_msg)

    if current_state is not None:
        await state.set_state(None)


@dispatcher.message_handler(state='*', commands=['start_search'])
async def start_search(message: types.Message):
    """Start search command handler for all states.

    Handler checks for exisitng search, starts new search conversation
    if there are none.

    Args:
        message: Message from user.
    """
    if await check_for_existing_search(f'tg-{message.chat.id}'):
        await message.answer(phrases.second_search)
        return

    await SearchConv.typing_url.set()
    await message.answer(phrases.waiting_url)


async def check_for_existing_search(chat_id):
    """Check for exisiting user's search in db.

    Args:
        chat_id: User chat id with platform prefix ('tg-' for telegram).
    """
    if redis_db.exists(chat_id):
        return True


@dispatcher.message_handler(state=SearchConv.typing_url)
async def get_url(message: types.Message, state: FSMContext):
    """Parse search url from user message.

    Args:
        message: Message from user.
        state: User state in conversation.
    """
    url = message.text
    if 'https://pass.rzd.ru/tickets' not in url:
        await message.answer(phrases.wrong_url_webpage)
        return

    got_url_time = str(datetime.datetime.now())
    redis_db.hmset(
        f'tg-{message.chat.id}',
        {
            'url': url,
            'id': f'tg-{message.chat.id}',
            'got_url_time': got_url_time
        }
    )

    await SearchConv.next()
    await message.answer(phrases.waiting_train_numbers)


@dispatcher.message_handler(state=SearchConv.typing_numbers)
async def get_numbers(message: types.Message, state: FSMContext):
    """Parse train numbers from user message.

    Args:
        message: Message from user.
        state: User state in conversation.
    """
    train_numbers = utils.parse_train_numbers(message.text)
    redis_db.hset(f'tg-{message.chat.id}', 'train_numbers', ','.join(train_numbers))

    await SearchConv.next()
    await message.answer(phrases.waiting_price_limit)


@dispatcher.message_handler(state=SearchConv.choosing_limit)
async def get_limit(message: types.Message, state: FSMContext):
    """Parse price limit from user message.

    Args:
        message: Message from user.
        state: User state in conversation.
    """
    try:
        price_limit = int(message.text)
    except ValueError:
        await message.answer(phrases.bad_price)
        return
    chat_id = f'tg-{message.chat.id}'
    start_search_time = str(datetime.datetime.now())
    redis_db.hmset(
        f'tg-{message.chat.id}',
        {
            'price_limit': price_limit,
            'start_search_time': start_search_time
        }
    )
    logs_key = os.environ.get('LOGS_KEY', 'search_logs')
    update_search_logs(chat_id, logs_key)

    await SearchConv.next()
    await message.answer(phrases.start_placehunt)


def update_search_logs(chat_id, logs_key):
    """Update search logs from new user search.

    Fetch user search from db and push it to db log key (list of logs)

    Args:
        chat_id: User chat id with platform prefix ('tg-' for telegram).
        logs_key: Db key for log list.
    """
    data_of_search = redis_db.hgetall(chat_id)
    dump = json.dumps({
        key.decode('UTF-8'): value.decode('UTF-8') for key, value in data_of_search.items()
    })
    redis_db.rpush(logs_key, dump)


@dispatcher.message_handler(state='*')
async def answer_searching(message: types.Message, state: FSMContext):
    """All not predicted messages handler. Sends little help to user.

    Args:
        message: Message from user.
        state: User state in conversation.
    """
    await message.answer(phrases.my_commands)


if __name__ == "__main__":
    main()
