import asyncio
import logging
import logging.config
from dotenv import load_dotenv

from config import LOGGER_CONFIG
from hunter import start_searching
from bot import dispatcher, executor


def main():
    load_dotenv()
    logging.config.dictConfig(LOGGER_CONFIG)
    place_hunt = asyncio.get_event_loop()
    place_hunt.create_task(start_searching())
    executor.start_polling(dispatcher, loop=place_hunt)
    place_hunt.close()


if __name__ == '__main__':
    main()
