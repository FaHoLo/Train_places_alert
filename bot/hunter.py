import asyncio
import datetime
from itertools import product
import os
# import re

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from redis.exceptions import TimeoutError
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException

from bot import bot
import utils


load_dotenv()

LOG_BOT = utils.get_logger_bot()
LOGGER_NAME = 'place_hunter_logger'

redis_db = utils.get_db_connection()

# Different patterns for different servers
DIGIT_GROUPING_SEPARATORS = (b',', b'\xc2\xa0')
separator = DIGIT_GROUPING_SEPARATORS[0]


def main():
    place_hunt = asyncio.get_event_loop()
    place_hunt.create_task(start_searching())
    place_hunt.close()


async def start_searching():
    while True:
        try:
            searches = await collect_searches()
            if not searches:
                await asyncio.sleep(10)
                continue
            await search_places(searches)
        except Exception:
            await utils.handle_exception(LOG_BOT, LOGGER_NAME)
        await asyncio.sleep(5)


async def collect_searches():
    search_keys = await collect_search_keys()
    searches = {}
    for search_key in search_keys:
        searches[search_key.decode('UTF-8')] = {
            key.decode('UTF-8'): value.decode('UTF-8')
            for key, value in redis_db.hgetall(search_key).items()
        }
    return searches


async def collect_search_keys():
    keys = []
    try:
        db_keys = redis_db.keys()
    except TimeoutError:
        await asyncio.sleep(5)
        return keys
    for key in db_keys:
        if key.startswith(b'tg-') or key.startswith(b'vk-'):
            keys.append(key)
    return keys


async def search_places(searches):
    for search_id, search_info in searches.items():
        if search_info.get('price_limit') is None:
            continue
        answer = await check_search(search_info)
        if answer:
            await bot.send_message(chat_id=search_id[3:], text=answer)
            await utils.remove_search_from_db(search_id)
        await asyncio.sleep(5)


async def check_search(search):
    train_numbers = [
        train_number.strip()
        for train_number in search['train_numbers'].strip().split(', ')
    ]
    response = await make_rzd_request(search['url'])
    if not response:
        return
    await asyncio.sleep(2)
    trains_with_places, trains_that_gone, trains_without_places = await collect_trains(response)
    if trains_with_places == 'Bad url':
        return 'Битая ссылка. Скорее всего, неверная дата. Прочитай /help и начни новый поиск'
    if not trains_with_places and not trains_that_gone and not trains_with_places:
        return
    answer = await check_for_wrong_train_numbers(train_numbers, trains_with_places,
                                                 trains_that_gone, trains_without_places)
    if answer:
        return answer
    answer = await check_for_places(train_numbers, trains_with_places, int(search['price_limit']))
    if answer:
        return answer
    answer = await check_for_all_gone(train_numbers, trains_that_gone)
    return answer


async def make_rzd_request(url):
    # ChromeBrowser (heroku offical supports it), easy guide: https://youtu.be/Ven-pqwk3ec?t=184)
    chrome_options = webdriver.ChromeOptions()
    chrome_options.binary_location = os.environ.get('GOOGLE_CHROME_BIN')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--gpu-disable')
    chrome_options.add_argument('log-level=2')
    try:
        driver_start_time = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
        driver = webdriver.Chrome(
            executable_path=os.environ.get('CHROMEDRIVER_PATH'),
            options=chrome_options)
        # driver = webdriver.Firefox(executable_path='C:\Program Files\Mozilla Firefox\geckodriver')
    except WebDriverException as ex:
        driver_broked_time = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
        delta = driver_broked_time - driver_start_time
        delta_msg = f'\nBots downtime is {delta.seconds} seconds'
        text = ex.msg + delta_msg
        if 'Chrome failed to start: exited abnormally' in ex.msg:
            # Selenium have some unsolvable sht like this:
            # raise exception_class(message, screen, stacktrace)
            # selenium.common.exceptions.WebDriverException: Message: unknown error: Chrome failed
            # to start: exited abnormally.
            # (unknown error: DevToolsActivePort file doesn't exist)
            # (The process started from chrome location /app/.apt/opt/google/chrome/chrome is
            # no longer running, so ChromeDriver is assuming that Chrome has crashed.)
            # You don't need it in logs so just print to know, it happend, but you can try
            # to solve it, if they are too often there
            # It takes about 1-2 secs from starting of webdriver to its error autodetection
            # and handling
            print(text)
        else:
            await utils.handle_exception(LOG_BOT, LOGGER_NAME, text=delta_msg)
        return
    except Exception as ex:
        await utils.handle_exception(LOG_BOT, LOGGER_NAME, text=ex.msg)
        return

    try:
        driver.get(url)
    except TimeoutException:
        driver.close()
        return
    except Exception as ex:
        await LOG_BOT.send_message(os.environ.get('TG_LOG_CHAT_ID'), ex.msg)
        await utils.handle_exception(LOG_BOT, LOGGER_NAME)
        driver.close()
        return
    await asyncio.sleep(2)
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

    train_with_places_divs = soup.select('div.route-item')
    train_that_gone_divs = soup.select('div.route-item__train-is-gone')
    train_without_places_divs = soup.select('div.route-item__train-without-places')

    trains_with_places = []
    for train_div in train_with_places_divs:
        if train_div in train_that_gone_divs:
            continue
        if train_div in train_without_places_divs:
            continue
        trains_with_places.append(train_div)

    trains_that_gone = [str(train_div) for train_div in train_that_gone_divs]

    trains_without_places = []
    for train_div in train_without_places_divs:
        if train_div in train_that_gone_divs:
            continue
        trains_without_places.append(str(train_div))
    return trains_with_places, trains_that_gone, trains_without_places


async def check_for_wrong_train_numbers(train_numbers, trains_with_places,
                                        trains_that_gone, trains_without_places):
    status = 'Not found'
    for train_number in train_numbers:
        for train in trains_with_places:
            if train_number in str(train):
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
    # time_pattern = r'route_time\">\d{1,2}:\d{2}'
    for train_data, train_number in product(trains_with_places, train_numbers):
        if train_number not in str(train_data):
            continue
        time = train_data.select_one('span.train-info__route_time').text.strip()
        # re.search(time_pattern, train_data)[0][-5:]
        if price_limit == 1:
            return f'Нашлись места в поезде {train_number}\nОтправление в {time}'
        price = await check_for_satisfying_price(train_data, price_limit)
        if price:
            price = await put_spaces_into_price(price)
            return f'Нашлись места в поезде {train_number}\nЦена билета: {price} ₽\nОтправление в {time}'


async def check_for_satisfying_price(train_data, price_limit):
    global separator
    for span_price in train_data.select('span.route-cartype-price-rub'):
        try:
            price = int(span_price.text.strip().encode('UTF-8').replace(separator, b''))
        except ValueError:
            separator = DIGIT_GROUPING_SEPARATORS[1]
            price = int(span_price.text.strip().encode('UTF-8').replace(separator, b''))
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


if __name__ == "__main__":
    main()
