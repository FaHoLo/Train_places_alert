import os
import re
import json
import redis
import config
import asyncio
import datetime
import logging
import logging.config
from textwrap import dedent
from itertools import product
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.redis import RedisStorage2


# loggers settings
bot_logger = logging.getLogger('trains_bot_logger')
hunter_logger = logging.getLogger('place_hunter_logger')

# db redis settings
redis_db = redis.Redis(
    host=os.environ['DB_HOST'],
    port=os.environ['DB_PORT'],
    password=os.environ['DB_PASS'],
)

# bot settings
bot = Bot(token=os.environ['TG_BOT_TOKEN'])
dispatcher = Dispatcher(
    bot=bot, 
    storage=RedisStorage2(
        host=os.environ['DB_HOST'],
        port=os.environ['DB_PORT'],
        password=os.environ['DB_PASS'] 
    ),
)


class Form(StatesGroup):
    typing_url = State()
    typing_numbers = State()
    choosing_limit = State()
    searching = State() 


def main():
    logging.config.dictConfig(config.LOGGER_CONFIG)
    place_hunt = asyncio.get_event_loop()
    process = place_hunt.create_task(start_searching())
    executor.start_polling(dispatcher)
    place_hunt.close()

# Hunter
async def start_searching():
    while True:
        try:
            searches = await collect_searches()
            if not searches:
                await asyncio.sleep(10)
                continue
            await search_places(searches)
        except Exception:
            hunter_logger.exception('')
        await asyncio.sleep(5)

async def collect_searches():
    search_keys = await collect_search_keys()
    searches = {}
    for key in search_keys:
        searches[key.decode('UTF-8')] = {
            key.decode('UTF-8'): value.decode('UTF-8')
            for key, value in redis_db.hgetall(key).items()
        }
    return searches

async def collect_search_keys():
    db_keys = redis_db.keys()
    keys = []
    for key in db_keys:
        if key.startswith(b'tg-') or key.startswith(b'vk-'):
            keys.append(key)
    return keys

async def search_places(searches):
    for search_id, search_info in searches.items():
        if search_info.get('price_limit') == None:
            continue
        answer = await check_search(search_info)
        if answer:
            await bot.send_message(chat_id=search_id[3:], text=answer)
            await remove_search_from_db(search_id)
        await asyncio.sleep(5)

async def check_search(search):
    train_numbers = search['train_numbers'].split(', ')
    response = await make_rzd_request(search['url'])
    if not response:
        return
    trains_with_places, trains_that_gone, trains_without_places = await collect_trains(response)
    if trains_with_places == 'Bad url':
        return 'Битая ссылка. Скорее всего, неверная дата. Прочитай /help и начни новый поиск'
    if not trains_with_places and not trains_that_gone and not trains_with_places:
        return
    answer = await check_for_wrong_train_numbers(train_numbers, trains_with_places, trains_that_gone, trains_without_places)
    if answer:
        return answer
    answer = await check_for_places(train_numbers, trains_with_places, int(search['price_limit']))
    if answer:
        return answer
    answer = await check_for_all_gone(train_numbers, trains_that_gone)
    if answer:
        return answer

async def make_rzd_request(url):
    # ChromeBrowser (heroku offical supports it), easy guide: https://youtu.be/Ven-pqwk3ec?t=184)
    chrome_options = webdriver.ChromeOptions()
    # chrome_options.binary_location =  os.environ.get('GOOGLE_CHROME_BIN')
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--no-sandbox')
    driver = webdriver.Chrome(executable_path=os.environ.get('CHROMEDRIVER_PATH'), options=chrome_options)
    # driver = webdriver.Firefox(executable_path='C:\Program Files\Mozilla Firefox\geckodriver')
    try:
        driver.get(url)
    except TimeoutException:
        driver.close()
        return
    except Exception:
        hunter_logger.exception('')
        driver.close()
        return
    while True:
        data = driver.page_source
        if data.count('Подбираем поезда') < 2:
            break
        await asyncio.sleep(1)
    driver.close()
    return data

async def collect_trains(data):
    if data.find('за пределами периода') != -1:
        return 'Bad url', None, None

    soup = BeautifulSoup(data, 'html.parser')

    trains_with_places_div = soup.find_all('div', {'class': 'route-item'})
    trains_that_gone_div = soup.find_all('div', {'class': 'route-item__train-is-gone'})
    trains_without_places_div = soup.find_all('div',{'class': 'route-item__train-without-places'})

    trains_with_places = []
    for train_div in trains_with_places_div:
        if train_div in trains_that_gone_div: 
            continue
        if train_div in trains_without_places_div:
            continue
        trains_with_places.append(str(train_div))

    trains_that_gone = [str(train_div) for train_div in trains_that_gone_div]

    trains_without_places = []
    for train_div in trains_without_places_div:
        if train_div in trains_that_gone_div:
            continue
        trains_without_places.append(str(train_div))

    return trains_with_places, trains_that_gone, trains_without_places

async def check_for_wrong_train_numbers(train_numbers, trains_with_places, trains_that_gone, trains_without_places):
    status = 'Not found'
    for train_number in train_numbers:
        for train in trains_with_places:
            if train_number in train:
                status = 'Found'
                break
        if status == 'Found':
            break
        for train in trains_that_gone:
            if train_number in train:
                status = 'Found'
                break
        if status == 'Found':
            break
        for train in trains_without_places:
            if train_number in train:
                status = 'Found'
                break
    if status == 'Not found':
        if len(train_numbers) == 1:
            return 'Неверный номер поезда, не нашел его в списках на эту дату. Прочитай /help и начни новый поиск'
        return 'Неверные номера поездов, не нашел ни одного в списках на эту дату. Прочитай /help и начни новый поиск'

async def check_for_places(train_numbers, trains_with_places, price_limit):
    time_pattern = r'route_time\">\d{1,2}:\d{2}'
    for train_data, train_number in product(trains_with_places, train_numbers):
        if train_number not in train_data:
            continue
        time = re.search(time_pattern, train_data)[0][-5:]
        if price_limit == 1:
            return f'Нашлись места в поезде {train_number}\nОтправление в {time}'
        price = await check_for_satisfying_price(train_data, price_limit)
        if price:
            price = await put_spaces_into_price(price)
            return f'Нашлись места в поезде {train_number}\nЦена билета: {price} ₽\nОтправление в {time}'

async def check_for_satisfying_price(train_data, price_limit):
    soup = BeautifulSoup(train_data, 'html.parser')
    html_price_pattern = rb'\d{1,3}(,\d{3})*(,\d{3})*'
    # Use next pattern for chromedriver > v80 
    # html_price_pattern = rb'\d+(\xc2\xa0\d{3})*(\xc2\xa0\d{3})*'
    for span_price in soup.find_all('span', {'class': 'route-cartype-price-rub'}):
        html_price = re.search(html_price_pattern, str(span_price).encode('UTF-8')).group(0)
        # Read previous comment
        # price = int(html_price.replace(b'\xc2\xa0', b''))
        price = int(html_price.replace(b',', b''))
        if price <= price_limit:
            return price

async def put_spaces_into_price(price):
    price = str(price)
    price_parts = []
    while len(price) > 3:
        price_parts.append(price[-3:])
        price = price[:-3]
    price_parts.append(price)
    price_parts.reverse()
    return ' '.join(price_parts)

async def check_for_all_gone(train_numbers, trains_that_gone):
    gone_trains = []
    for train, train_number in product(trains_that_gone, train_numbers):
        if train_number not in train: 
            continue
        gone_trains.append(train_number)
    if len(gone_trains) == len(train_numbers):
        return 'Места не появились, все поезда ушли 😔'


# Bot
@dispatcher.errors_handler()
async def errors_handler(update, exception):
    bot_logger.error(exception)

@dispatcher.message_handler(state='*', commands=['start'])
async def send_welcome(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.finish()
    text = 'Привет! Я бот, проверяю сайт РЖД на появление мест в выбранных поездах. Оповещу тебя, если места появятся или поезда так и уйдут заполненными. Жми /help'
    await message.answer(text)

@dispatcher.message_handler(state='*', commands=['help'])
async def send_help(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.finish()
    first_text = dedent('''\
    Я оповещу тебя, если места появятся или поезда так и уйдут заполненными, для этого выполни следующую инструкцию (дочитай до конца):
    1. Нажми /start_search.
    2. Зайди на сайт https://pass.rzd.ru, выбери место отправления, место назначения, дату, убери галку с поля «Только с билетами» и нажми кнопку «Расписание».
    3. Скопируй ссылку загруженной страницы и отправь её мне в сообщении.
    4. Выбери номера поездов, на которых хочешь поехать и пришли мне список поездов (их всех нужно разделить запиятыми и пробелами). Учти номера поездов содержат цифры, РУССКИЕ буквы и значки, например «123*А, 456Е».
    5. Отправь ограничение на стоимость билетов в рублях (число без букв, знаков или пробелов). Если цена не важна, отправь «1».
    ''')
    second_text = dedent('''
    Поиск можно прекратить в любой момент, введя команду /cancel
    Пример твоих сообщений:
    https://pass.rzd.ru/tickets/public/ru?STRUCTURE_ID=7... (длинная ссылка)
    00032, 002А, Е*100
    2500
    ''')
    await message.answer(first_text, disable_web_page_preview=True)
    await message.answer(second_text, disable_web_page_preview=True)

@dispatcher.message_handler(state='*', commands=['cancel'])
async def cancel_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    search_canceld_text = 'Поиск отменен. Можешь начать новый поиск командой /start_search'

    if current_state == 'Form:typing_url':
        await message.answer(search_canceld_text)
    else:
        if not await check_for_existing_search(f'tg-{message.chat.id}'):
            await message.answer('Поиск еще не запущен, начни новый /start_search')
        else:
            await remove_search_from_db(f'tg-{message.chat.id}')
            await message.answer(search_canceld_text)

    if current_state is not None:
        await state.set_state(None)

async def remove_search_from_db(chat_id):
    redis_db.delete(chat_id)

@dispatcher.message_handler(state='*', commands=['start_search'])
async def start_search(message: types.Message):
    if await check_for_existing_search(f'tg-{message.chat.id}'):
        text = 'Поиск уже запущен, ты можешь остановить его, если нужен новый (/cancel)'
        await message.answer(text)
        return
    text = dedent('''\
    Ожидаю ссылку на расписание, пример:
    https://pass.rzd.ru/tickets/public/ru?layer_name=e3-route...
    ''')
    await Form.typing_url.set()
    await message.answer(text)

async def check_for_existing_search(chat_id):
    if redis_db.exists(chat_id):
        return True

@dispatcher.message_handler(state=Form.typing_url)
async def get_url(message: types.Message, state: FSMContext):
    url = message.text
    if 'https://pass.rzd.ru/tickets' not in url:
        await message.answer('Что-то не так с твоей ссылкой. обычно она начинается с https://pass.rzd.ru/tic...\nПопробуй еще раз 😉')
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

    text = '''
    Хорошо, теперь отправь мне номера поездов, на которых ты хочешь поехать. Их нужно разделить запятой и пробелом, например:\n00032, 002А, Е*100
    '''
    await Form.next()
    await message.answer(text)

@dispatcher.message_handler(state=Form.typing_numbers)
async def get_numbers(message: types.Message, state: FSMContext):
    train_numbers = message.text
    redis_db.hset(f'tg-{message.chat.id}', 'train_numbers', train_numbers)

    text = 'Отлично, теперь отправь мне ограничение на цену билетов. Целым числом: без копеек, запятых и пробелов, например:\n5250\nЕсли цена не важна, отправь 1'
    await Form.next()
    await message.answer(text)

@dispatcher.message_handler(state=Form.choosing_limit)
async def get_limit(message: types.Message, state: FSMContext):
    try:
        price_limit = int(message.text)
    except ValueError:
        await message.answer('Неверное число. Цена должна быть в виде ОДНОГО целого числа, без лишних знаков препинания, пробелов и т.д. Например:\n1070\nПопробуй ещё раз\nОтправь 1, если цена неважна.')
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
    update_search_logs(chat_id)

    text = 'Пойду искать места, если захочешь отменить поиск нажми /cancel'
    await Form.next()
    await message.answer(text)

def update_search_logs(chat_id):
    data_of_search = redis_db.hgetall(chat_id)
    dump = json.dumps({key.decode('UTF-8'): value.decode('UTF-8') for key, value in data_of_search.items()})
    redis_db.rpush('search_logs', dump)

@dispatcher.message_handler(state='*')
async def answer_searching(message: types.Message, state: FSMContext):
    text = 'Я бот. Общаюсь на языке команд:\n/help - помощь\n/start_search - начать поиск\n/cancel - отменить поиск😔'
    await message.answer(text)


if __name__ == '__main__':
    main()
