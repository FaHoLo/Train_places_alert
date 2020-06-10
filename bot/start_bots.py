import asyncio
from dotenv import load_dotenv

from hunter import start_searching
from bot import dispatcher, executor


def main():
    load_dotenv()
    place_hunt = asyncio.get_event_loop()
    place_hunt.create_task(start_searching())
    executor.start_polling(dispatcher, loop=place_hunt)
    place_hunt.close()


if __name__ == '__main__':
    main()
