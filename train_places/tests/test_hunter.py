"""Tests for place hunter.

Note: there is 8 warnings because of the aiogram and aiohttp libraries, 
so use --disable-warnings flag when running tests to hide them.
"""

import asyncio
import os
import pathlib

from train_places.hunter.hunter import *
from train_places.phrases import phrases


loop = asyncio.get_event_loop()
loop.set_debug(True)

path_dir = pathlib.Path(__file__).parent.absolute()

with open(os.path.join(path_dir, 'rzd_responses', 'normal_response.html'), 'r') as f:
    normal_response = f.read()

with open(os.path.join(path_dir, 'rzd_responses', 'yesterday_trains.html'), 'r') as f:
    yesterday_trains_response = f.read()

with open(os.path.join(path_dir, 'rzd_responses', 'wrong_date.html'), 'r') as f:
    wrong_date_response = f.read()


def test_found_places():
    train_with_places = '780А'
    train_numbers = f'122*С,{train_with_places}'
    price_limit = 1
    phrase = phrases.place_found.format(train_number=train_with_places, time='21:00')

    answer = loop.run_until_complete(check_trains_data(normal_response, train_numbers, price_limit))
    assert answer == phrase


def test_found_places_with_price():
    train_with_places = '780А'
    train_numbers = f'122*С,{train_with_places}'
    price_limit = '5000'
    phrase = phrases.place_found_with_price.format(train_number=train_with_places, time='21:00',
                                                   spaced_price='4 579')

    answer = loop.run_until_complete(check_trains_data(normal_response, train_numbers, price_limit))
    assert answer == phrase


def test_no_places():
    train_numbers = '122*С'
    price_limit = 1
    answer = loop.run_until_complete(check_trains_data(normal_response, train_numbers, price_limit))
    
    assert answer is None


def test_no_places_under_price_limit():
    train_with_places = '780А'
    train_numbers = f'122*С,{train_with_places}'
    price_limit = '4000'
    phrase = phrases.place_found_with_price.format(train_number=train_with_places, time='21:00',
                                                   spaced_price='4 579')

    answer = loop.run_until_complete(check_trains_data(normal_response, train_numbers, price_limit))
    assert answer is None


def test_all_trains_gone():
    train_numbers = '768А,757Н'
    price_limit = 1
    
    answer = loop.run_until_complete(check_trains_data(normal_response, train_numbers, price_limit))
    assert answer == phrases.all_trains_gone


def test_bad_train_number():
    train_numbers = '12345'
    price_limit = 1
    
    answer = loop.run_until_complete(check_trains_data(normal_response, train_numbers, price_limit))
    assert answer == phrases.bad_train_number


def test_bad_train_numbers():
    train_numbers = '12345,678910'
    price_limit = 1
    
    answer = loop.run_until_complete(check_trains_data(normal_response, train_numbers, price_limit))
    assert answer == phrases.bad_train_numbers


def test_url_with_yesterday_date():
    answer = check_for_bad_url(yesterday_trains_response)
    assert answer == phrases.all_trains_gone


def test_bad_date_or_route():
    answer = check_for_bad_url(wrong_date_response)
    assert answer == phrases.bad_date_or_route
