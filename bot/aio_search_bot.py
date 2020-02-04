import os
import re
import socket
import config
import pickle
import asyncio
import datetime
import logging
import logging.config
from itertools import product
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from db_map import Base, ActiveSearch, SearchLog

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.files import PickleStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup


# db settings
db_filename = os.getenv('DB_FILENAME')
db_engine = create_engine(f'sqlite:///{db_filename}', echo=True)
if not os.path.exists(f'{db_filename}'):
    Base.metadata.create_all(db_engine)
session_factory = sessionmaker(bind=db_engine)
db_session = scoped_session(session_factory)


# bot settings
bot = Bot(token=os.environ['TG_BOT_TOKEN'])
storage = PickleStorage('state_storage.pickle') # TODO Add pickle backup every 30 sec for tg server
dispatcher = Dispatcher(bot, storage=storage)

class Form(StatesGroup):
    typing_url = State()
    typing_numbers = State()
    choosing_limit = State()
    searching = State() 


def main():
    place_hunt = asyncio.get_event_loop()
    process = place_hunt.create_task(start_searching())
    executor.start_polling(dispatcher)
    place_hunt.close()

# Hunter
async def start_searching():
    while True:
        try:
            null_session = db_session()
            search_list = null_session.query(ActiveSearch).all()
            null_session.close()
            if not search_list:
                await asyncio.sleep(10)
                continue
            await search_places(search_list)
        except Exception:
            hunter_logger.exception('')
        await asyncio.sleep(5)

async def search_places(search_list):
    for search in search_list:
        if not search.price_limit:
            continue
        answer = await check_search(search)
        if answer:
            await bot.send_message(chat_id=search.chat_id, text=answer)
            await remove_search_from_spreadsheet(search.chat_id)
        await asyncio.sleep(5)

async def check_search(search):
    train_numbers = search.train_numbers.split(', ')
    response = await make_rzd_request(search.url)
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
    answer = await check_for_places(train_numbers, trains_with_places, search.price_limit)
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
            return 'Неверный номер поезда, не нашел его в списка на эту дату. Прочитай /help и начни новый поиск'
        return 'Неверные номера поездов, не нашел ни одного на эту дату. Прочитай /help и начни новый поиск'

async def check_for_places(train_numbers, trains_with_places, price_limit):
    time_pattern = r'route_time\">\d{1,2}:\d{2}'
    for train_data, train_number in product(trains_with_places, train_numbers):
        if train_number not in train_data:
            continue
        time = re.search(time_pattern, train_data)[0][-5:]
        if price_limit == 1:
            return f'Нашлись места в поезде {train_number}\nОтправление в {time}'
        if check_for_satisfying_price(train_data, price_limit):
            return f'Нашлись места в поезде {train_number}\nОтправление в {time}'

def check_for_satisfying_price(train_data, price_limit):
    soup = BeautifulSoup(train_data, 'html.parser')
    html_price_pattern = r'\d{1,3},\d{3}|\d{1:3},\d{3},\d{3}'
    for span_price in soup.find_all('span', {'class': 'route-cartype-price-rub'}):
        html_price = re.search(html_price_pattern, str(span_price))[0]
        if int(html_price.replace(',', '')) <= price_limit:
            return True

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
    text = 'Привет! Я бот, проверяю сайт РЖД на появление мест в выбранных поездах. Оповещу тебя, если места появятся или поезд так и уйдет заполненным. Жми /help'
    await message.answer(text)

@dispatcher.message_handler(state='*', commands=['help'])
async def send_help(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.finish()
    text = '''
    ⠀ 1. Нажми /start_search.
    2. Зайди на сайт https://pass.rzd.ru, выбери место отправления, место назначения, дату, убери галку с поля "Только с билетами" и нажми кнопку "Расписание".
    3. Скопируй ссылку загруженной страницы и отправь её мне в сообщении.
    4. Выбери номера поездов, на которых хочешь поехать и пришли мне список поездов (их всех нужно разделить запиятыми и пробелами). Учти номера поездов содержат цифры, РУССКИЕ буквы и значки, например «123*А, 456Е».
    5. Отправь ограничение на стоимость билетов (число без букв, знаков и пробелов). Если цена не важна, отправь 0.
    Важно! Поиск можно прекратить в любой момент, введя команду /cancel
    Пример твоих сообщений:
    https://pass.rzd.ru/tickets/public/ru?STRUCTURE_ID=7...
    00032, 002А, Е*100
    2500
    '''
    await message.answer(text)

@dispatcher.message_handler(state='*', commands=['cancel'])
async def cancel_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    search_check = await check_for_existing_search(int(message.chat.id))
    if not search_check:
        await message.answer('Поиск еще не запущен, начни новый /start_search')
    else:
        await remove_search_from_spreadsheet(int(message.chat.id))
        await message.answer('Поиск отменен. Начни новый поиск командой /start_search')

    if current_state is not None:
        await state.set_state(None)

async def remove_search_from_spreadsheet(chat_id):
    session = db_session()
    session.query(ActiveSearch).filter_by(chat_id=chat_id).delete()
    session.commit()
    session.close()

@dispatcher.message_handler(state='*', commands=['start_search'])
async def start_search(message: types.Message):
    search_check = await check_for_existing_search(int(message.chat.id))
    if search_check:
        text = 'Поиск уже запущен, ты можешь остановить его, если нужен новый (/cancel)'
        await message.answer(text)
        return
    text = '''
    Ожидаю ссылку на расписание, пример:
    https://pass.rzd.ru/tickets/public/ru?layer_name=e3-route...
    '''
    await Form.typing_url.set()
    await message.answer(text)

async def check_for_existing_search(chat_id):
    session = db_session()
    if session.query(ActiveSearch).filter_by(chat_id=chat_id).first():
        session.close()
        return True

@dispatcher.message_handler(state=Form.typing_url)
async def get_url(message: types.Message, state: FSMContext):
    url = message.text
    if 'https://pass.rzd.ru/tickets' not in url:
        await message.answer('Что-то не так с твоей ссылкой. обычно она начинается с https://pass.rzd.ru/tickets...\nПопробуй еще раз 😉')
        return
    chat_id = message.chat.id
    column = 'url'
    await update_db(chat_id, column, url)
    text = '''
    Хорошо, теперь отправь мне номера поездов, на которых ты хочешь поехать. Их нужно разделить запятой и пробелом, например:\n00032, 002А, Е*100
    '''
    await Form.next()
    await message.answer(text)

@dispatcher.message_handler(state=Form.typing_numbers)
async def get_numbers(message: types.Message, state: FSMContext):
    train_numbers = message.text
    chat_id = message.chat.id
    column = 'train_numbers'
    await update_db(chat_id, column, train_numbers)
    text = 'Отлично, теперь отправь мне ограничение на цену билетов. Целым числом: без копеек, запятых и пробелов, например:\n5250\nЕсли цена не важна, отправь 1'
    await Form.next()
    await message.answer(text)

@dispatcher.message_handler(state=Form.choosing_limit)
async def get_limit(message: types.Message, state: FSMContext):
    try:
        price_limit = int(message.text)
    except ValueError:
        await message.answer('Неверное число. Цена должна быть в виде ОДНОГО целого числа, без лишних знаков препинания, пробелов и т.д. Например:\n1070\nОтправь 1, если цена неважна.')
        return
    chat_id = message.chat.id
    column = 'price_limit'
    await update_db(chat_id, column, price_limit)
    text = 'Пойду искать места, если захочешь отменить поиск нажми /cancel'
    await Form.next()
    await message.answer(text)

async def update_db(chat_id, column, value):
    session = db_session()
    user_search = session.query(ActiveSearch).filter_by(chat_id=chat_id).first()
    if not user_search:
        user_search = ActiveSearch(chat_id=chat_id, query_time=datetime.datetime.now())
    updated_search = update_search(user_search, column, value)
    if column == 'price_limit':
        log_search = SearchLog(
            chat_id = updated_search.chat_id,
            url = updated_search.url,
            train_numbers = updated_search.train_numbers,
            price_limit = updated_search.price_limit,
            query_time = updated_search.query_time,
        )
        session.add(log_search)
    session.add(updated_search)
    session.commit()
    session.close()

def update_search(user_search, column, value):
    if column == 'url':
        user_search.url = value
    if column == 'train_numbers':
        user_search.train_numbers = value
    if column == 'price_limit':
        user_search.price_limit = value
    return user_search

@dispatcher.message_handler(state='*')
async def answer_searching(message: types.Message, state: FSMContext):
    text = 'Я бот. Общаюсь на языке команд:\n/help - помощь\n/start_search - начать поиск\n/cancel - отменить поиск😔'
    await message.answer(text)


if __name__ == '__main__':
    logging.config.dictConfig(config.LOGGER_CONFIG)
    bot_logger = logging.getLogger('trains_bot_logger')
    bot_logger.setLevel('WARNING')
    hunter_logger = logging.getLogger('place_hunter_logger')
    hunter_logger.setLevel('WARNING')
    main()
