import os
import re
import socket
import config
import pickle
import asyncio
import logging
import logging.config
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from dotenv import load_dotenv
load_dotenv()

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup


def authorize_spreadsheets_api():
    # Code snippet from: https://developers.google.com/sheets/api/quickstart/python
    # If modifying these scopes, delete the file token.pickle.
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', scopes)
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    return sheet
sheet = authorize_spreadsheets_api()

PROCESSING_SPREADSHEET_ID = os.environ['PROCESSING_SPREADSHEET_ID']
LOGGING_SPREADSHEET_ID = os.environ['LOGGING_SPREADSHEET_ID']
PROCESSING_SPSH_DATA_RANGE = 'A2:C'

# bot settings
bot = Bot(token=os.environ['TG_BOT_TOKEN'])
storage = MemoryStorage() # TODO This type of storage is not recommended for usage in bots, because you will lost all states after restarting.
dispatcher = Dispatcher(bot, storage=storage)

class Form(StatesGroup):
    typing_url_and_numbers = State()
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
            search_list = await download_spreadsheet_data(PROCESSING_SPREADSHEET_ID, PROCESSING_SPSH_DATA_RANGE)
            if not search_list:
                await asyncio.sleep(10)
                continue
            await search_places(search_list)
        except Exception:
            hunter_logger.exception('')
        await asyncio.sleep(5)

async def download_spreadsheet_data(spreadsheet_id, data_range):
    learned_htpp_exceptions = [
        'The service is currently unavailable',
        'Internal error encountered',
    ]
    try:
        result = sheet.values().get(spreadsheetId=spreadsheet_id,
                                    range=data_range).execute()
    except HttpError as exc:
        for exception in learned_htpp_exceptions:
            if exception in exc._get_reason().strip():
                return
        raise exc
    except socket.timeout:
        return
    data = result.get('values', [])
    return data

async def search_places(search_list):
    for search_string_number, search in enumerate(search_list):
        if not search:
            continue
        answer = await check_search(search)
        if answer:
            await bot.send_message(chat_id=search[2], text=answer)
            await remove_search_from_spreadsheet_hunter(search_string_number)
        await asyncio.sleep(5)

async def check_search(search):
    url, train_numbers, chat_id = search
    train_numbers = train_numbers.split(', ')
    response = await make_rzd_request(url)
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
    answer = await check_for_places(train_numbers, trains_with_places)
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
    if check_for_bad_url(data):
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

def check_for_bad_url(data):
    if data.find('за пределами периода') != -1:
        return True

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
        return 'Неверные номера поездов, не нашел ни одного на эту дату. Прочитай /help и начни новый поиск'

async def check_for_places(train_numbers, trains_with_places):
    time_pattern = r'route_time\">\d{1,2}:\d{2}'
    for train in trains_with_places:
        for train_number in train_numbers:
            if train_number not in train:
                continue
            time = re.search(time_pattern, train)[0][-5:]
            return f'Нашлись места в поезде {train_number}\nОтправление в {time}'

async def check_for_all_gone(train_numbers, trains_that_gone):
    number_of_tn = len(train_numbers)
    gone_trains = []
    for train in trains_that_gone:
        for train_number in train_numbers:
            if train_number not in train: 
                continue
            gone_trains.append(train_number)
    if len(gone_trains) == number_of_tn:
        return 'Места не появились, все поезда ушли 😔'

async def remove_search_from_spreadsheet_hunter(string_number):
    string_number = string_number + 2
    body = {
        'ranges': [f'A{string_number}:C{string_number}']
    }
    sheet.values().batchClear(spreadsheetId=PROCESSING_SPREADSHEET_ID, 
        body=body).execute()
    return

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
    3. Скопируй ссылку загруженной страницы.
    4. Выбери номера поездов, на которых хочешь поехать и пришли мне в одном сообщении ссылку на страницу и список поездов (их всех нужно разделить запиятыми и пробелами). Учти номера поездов содержат цифры, РУССКИЕ буквы и значки, например «123*А, 456Е».
    Важно! Поиск можно прекратить в любой момент, введя команду /cancel
    Пример твоего сообщения:
    https://pass.rzd.ru/tickets/public/ru?layer_name=e3-route..., 00032, 002А, Е*100
    '''
    await message.answer(text)

@dispatcher.message_handler(state='*', commands=['cancel'])
async def cancel_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    search_check = await check_for_existing_search(str(message.chat.id))
    if not search_check:
        await message.answer('Поиск еще не запущен, начни новый /start_search')
    else:
        await remove_search_from_spreadsheet_bot(str(message.chat.id))
        await message.answer('Поиск отменен. Начни новый поиск командой /start_search')

    if current_state is not None:
        await state.set_state(None)

async def remove_search_from_spreadsheet_bot(chat_id):
    searches = await download_spreadsheet_data(PROCESSING_SPREADSHEET_ID, PROCESSING_SPSH_DATA_RANGE)
    value_input_option = 'RAW'
    for string_number, search in enumerate(searches):
        if chat_id in search:
            string_number = string_number + 2
            body = {
                'ranges': [f'A{string_number}:C{string_number}']
            }
            sheet.values().batchClear(spreadsheetId=PROCESSING_SPREADSHEET_ID, 
                body=body).execute()
            return

@dispatcher.message_handler(state='*', commands=['start_search'])
async def start_search(message: types.Message):
    search_check = await check_for_existing_search(str(message.chat.id))
    if search_check:
        text = 'Поиск уже запущен, ты можешь остановить его, если нужен новый (/cancel)'
        await message.answer(text)
        return
    
    text = '''
    Ожидаю ссылку на расписание и список поездов (напоминаю, их нужно рзделить запятой и пробелом)
    Пример:
    https://pass.rzd.ru/tickets/public/ru?layer_name=e3-route..., 00032, 002А, Е*100
    '''
    await Form.typing_url_and_numbers.set()
    await message.answer(text)

async def check_for_existing_search(chat_id):
    searches = await download_spreadsheet_data(PROCESSING_SPREADSHEET_ID, PROCESSING_SPSH_DATA_RANGE)
    for search in searches:
        if chat_id in search:
            return True

@dispatcher.message_handler(state=Form.typing_url_and_numbers)
async def get_url_and_numbers(message: types.Message, state: FSMContext):
    user_request = message.text.split(', ')
    if len(user_request) < 2:
        await message.answer('Что-то не так с твоим запросом, мне нужен список из ссылки на поиск и номеров поездов. Попробуй еще раз, не забудь разделить их запятой и пробелом 😉')
        return
    url = user_request[0]
    if 'pass.rzd.ru/tickets/' not in url:
        await message.answer('Что-то не так с твоей ссылкой. обычно она начинается с https://pass.rzd.ru/tickets...\nПопробуй еще раз 😉')
        return
    train_numbers = ', '.join(user_request[1:])
    chat_id = message.chat.id
    await update_spreadsheets(url, train_numbers, chat_id)
    text = 'Пойду искать места, если захочешь отменить поиск нажми /cancel'
    await Form.next()
    await message.answer(text)

async def update_spreadsheets(url, train_numbers, chat_id):
    logging_empty_string = await get_logging_empty_string_number()
    processing_empty_string = await get_processing_empty_string_number()
    logging_spsh_range = f'A{logging_empty_string}'
    processing_spsh_range = f'A{processing_empty_string}'
    value_input_option = 'RAW'
    body = {
        'values': [[url, train_numbers, str(chat_id)]]
    }
    sheet.values().update(spreadsheetId=LOGGING_SPREADSHEET_ID, 
        valueInputOption=value_input_option, range=logging_spsh_range, 
        body=body).execute()
    sheet.values().update(spreadsheetId=PROCESSING_SPREADSHEET_ID, 
        valueInputOption=value_input_option, range=processing_spsh_range, 
        body=body).execute()

    await update_logging_number(logging_empty_string)

async def get_logging_empty_string_number():
    data_range = 'D2:D2'
    data = await download_spreadsheet_data(LOGGING_SPREADSHEET_ID, data_range)
    empty_string_number = int(data[0][0])
    return empty_string_number

async def get_processing_empty_string_number():
    data_range = 'A2:C'
    data = await download_spreadsheet_data(PROCESSING_SPREADSHEET_ID, data_range)
    empty_string_number = len(data) + 2
    if not data:
        return empty_string_number
    for string_number, string in enumerate(data):
        if not string:
            empty_string_number = string_number + 2
            return empty_string_number

async def update_logging_number(logging_empty_string):
    body = {
        'values': [[str(logging_empty_string + 1)]]
    }
    sheet.values().update(spreadsheetId=LOGGING_SPREADSHEET_ID, 
        valueInputOption='RAW', range='D2', 
        body=body).execute()

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
