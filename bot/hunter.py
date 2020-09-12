"""Place hunter bot module.

Bot needs environment varibles:
    GOOGLE_CHROME_BIN: Chrome browser path.
    CHROMEDRIVER_PATH: Chrome driver path (easy guide: https://youtu.be/Ven-pqwk3ec?t=184).
    TG_PROXY: Bot proxy (default = None).
    TG_BOT_TOKEN: Bot token.
    TG_LOG_BOT_TOKEN Telegram log bot token.
    TG_LOG_CHAT_ID: Telegram log chat id.
    DB_HOST: Redis database host.
    DB_PORT: Redis database port.
    DB_PASS: Redis database password.
"""

import asyncio
import datetime
from itertools import product
import os
from textwrap import dedent
from typing import Optional, Tuple, Union, List

from bs4 import BeautifulSoup, Tag
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
    """Run place hunter."""
    place_hunt = asyncio.get_event_loop()
    place_hunt.create_task(start_searching())
    place_hunt.close()


async def start_searching():
    """Run place hunter main task."""
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


async def collect_searches() -> dict:
    """Collect searches from db.

    Returns:
        searches: Collected searches.
    """
    search_keys = await collect_search_keys()
    searches = {}
    for search_key in search_keys:
        searches[search_key.decode('UTF-8')] = {
            key.decode('UTF-8'): value.decode('UTF-8')
            for key, value in redis_db.hgetall(search_key).items()
        }
    return searches


async def collect_search_keys():
    """Collect search keys by search patterns from db.

    Returns:
        keys: Search keys.
    """
    keys = []
    search_patterns = ['tg-*', 'vk-*']
    for search_pattern in search_patterns:
        try:
            # All scan methods returns cursor position and then list of keys: (0, [key1, key2])
            search_keys = redis_db.scan(0, match=search_pattern, count=10000)[1]
        except TimeoutError:
            await asyncio.sleep(2)
            return keys
        if search_keys:
            keys.extend(search_keys)
        await asyncio.sleep(2)
    return keys


async def search_places(searches: dict) -> None:
    """Search places in searches and notify user about its appearance.

    Args:
        searches: Active searches of all users.
    """
    for search_id, search_info in searches.items():
        if search_info.get('price_limit') is None:
            continue
        answer = await check_search(search_info)
        if answer:
            await bot.send_message(chat_id=search_id[3:], text=answer)
            await utils.remove_search_from_db(search_id)
        await asyncio.sleep(5)


async def check_search(search: dict) -> Optional[str]:
    """Check single user search for places and return answer.

    Args:
        search: User search info.

    Returns:
        answer: Answer to send to user.
    """
    train_numbers = [
        train_number.strip()
        for train_number in search['train_numbers'].strip().split(', ')
    ]
    response = await make_rzd_request(search['url'])
    if not response:
        return None
    if 'за пределами периода' in response or 'Необходимо ввести маршрут' in response:
        return 'Битая ссылка. Скорее всего неверная дата или не выбран маршрут. Прочитай /help и начни новый поиск.'
    await asyncio.sleep(2)
    trains_with_places, trains_that_gone, trains_without_places = await collect_trains(response)

    if not trains_with_places and not trains_that_gone and not trains_with_places:
        return None

    # TODO Its anti-pattern here, change it
    answer = await check_for_wrong_train_numbers(
        train_numbers, trains_with_places, trains_that_gone, trains_without_places)  # type: ignore
    if answer:
        return answer
    answer = await check_for_places(
        train_numbers, trains_with_places, int(search['price_limit']))  # type: ignore
    if answer:
        return answer
    answer = await check_for_all_gone(train_numbers, trains_that_gone)  # type: ignore
    return answer


async def make_rzd_request(url) -> Optional[str]:
    """Get response from rzd with Selenium.

    Args:
        url: Search url.

    Returns:
        response: Page data.
    """
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
            # It takes about 1-2 secs from starting of webdriver to this error autodetection
            # and handling
            print(text)
        else:
            await utils.handle_exception(LOG_BOT, LOGGER_NAME, text=delta_msg)
        return None
    except Exception:
        await utils.handle_exception(LOG_BOT, LOGGER_NAME)
        return None

    try:
        driver.get(url)
        # We must close driver if any exception raised, to save RAM
    except TimeoutException:
        driver.close()
        return None
    except Exception as ex:
        await utils.handle_exception(LOG_BOT, LOGGER_NAME)
        driver.close()

        try:
            # ex.msg check is here coz sometimes driver dont write it in traceback
            await LOG_BOT.send_message(os.environ.get('TG_LOG_CHAT_ID'), ex.msg)  # type: ignore
        except Exception:
            pass
        return None

    await asyncio.sleep(2)
    while True:
        data = driver.page_source
        if data.count('Подбираем поезда') < 2:
            break
        await asyncio.sleep(1)
    driver.close()
    return data


async def collect_trains(data: str) -> Tuple[List[Tag], List[str], List[str]]:
    """Collect all trains type from search page data.

    Train types: train with places, train that gone, train without places

    Args:
        data: Search page data.

    Returns:
        trains_with_places: Trains that have vacant places.
                            Tag type of trains for subsequent selections.
        trains_that_gone: Trains that gone.
        trains_without_places: Trains without vacant places.
    """
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


async def check_for_wrong_train_numbers(
            train_numbers: List[str], trains_with_places: List[Tag],
            trains_that_gone: List[str], trains_without_places: List[str],
        **kwargs) -> Tuple[bool, str]:
    """Check train numbers for collected trains entry.

    Check is passed if at least one train number found on page.

    Args:
        train_numbers: Train numbers from user search.
        trains_with_places: Trains that have vacant places.
                            Tag type of trains for subsequent selections.
        trains_that_gone: Trains that gone.
        trains_without_places: Trains without vacant places.

    Returns:
        answer: Answer about bad train numbers.
    """
    status = False
    answer = ''
    all_trains_data = str(trains_with_places) + ''.join(trains_that_gone) \
        + ''.join(trains_without_places)
    for train_number in train_numbers:
        if train_number in all_trains_data:
            status = True
            break
    if status:
        if len(train_numbers) == 1:
            answer = 'Неверный номер поезда, не нашел его в списках на эту дату.'
        else:
            answer = 'Неверные номера поездов, не нашел ни одного в списках на эту дату.'
        answer += ' Прочитай /help и начни новый поиск.'
    return status, answer


async def check_for_places(train_numbers: List[str], trains_with_places: List[Tag],
                           price_limit: int) -> Optional[str]:
    r"""Check trains for vacant places with price limit.

    If price limit == 1 check is not performed.

    For development use in case of broked time selector:
    import re
    time_pattern = r'route_time\">\d{1,2}:\d{2}'
    time = re.search(time_pattern, train_data)[0][-5:]

    Args:
        train_numbers: Train numbers from user search.
        trains_with_places: Trains that have vacant places.
                            Tag type of trains for selections.
        price_limit: Price limit for place price.

    Returns:
        Answer: Answer about finding suitable places.
    """
    for train_data, train_number in product(trains_with_places, train_numbers):
        if train_number not in str(train_data):
            continue
        time = train_data.select_one('span.train-info__route_time').text.strip()
        if price_limit == 1:
            return f'Нашлись места в поезде {train_number}\nОтправление в {time}'
        price = await check_for_satisfying_price(train_data, price_limit)
        if price:
            spaced_price = await put_spaces_into_price(price)
            return dedent(f'''\
            Нашлись места в поезде {train_number}
            Цена билета: {spaced_price} ₽
            Отправление в {time}
            ''')
    return None


async def check_for_satisfying_price(train_data: Tag, price_limit: int) -> Optional[int]:
    """Check train place for satisfying prices.

    Args:
        train_data: Tag of train data.
        price_limit: Price limit of search.

    Returns:
        price: Satisfying place price.
    """
    global separator
    for span_price in train_data.select('span.route-cartype-price-rub'):
        try:
            price = int(span_price.text.strip().encode('UTF-8').replace(separator, b''))
        except ValueError:
            separator = DIGIT_GROUPING_SEPARATORS[1]
            price = int(span_price.text.strip().encode('UTF-8').replace(separator, b''))
        if price <= price_limit:
            return price
    return None


async def put_spaces_into_price(price: int) -> str:
    """Put spaces into price.

    Args:
        price: Place price.

    Returns:
        new_price: Price with spaces.
    """
    new_price = str(price)
    price_parts = []
    while len(new_price) > 3:
        price_parts.append(new_price[-3:])
        new_price = new_price[:-3]
    price_parts.append(new_price)
    price_parts.reverse()
    new_price = ' '.join(price_parts)
    return new_price


async def check_for_all_gone(train_numbers: List[str],
                             trains_that_gone: List[str]) -> Optional[str]:
    """Check train numbers for entry in gone trains.

    Args:
        train_numbers: Train numbers from user search.
        trains_that_gone: Trains that gone.

    Returns:
        answer: Answer about the departure of all searched trains.
    """
    gone_trains = []
    for train, train_number in product(trains_that_gone, train_numbers):
        if train_number not in train:
            continue
        gone_trains.append(train_number)
    if len(gone_trains) == len(train_numbers):
        return 'Места не появились, все поезда ушли 😔'
    return None


if __name__ == "__main__":
    main()
