# Ищем места в забитом поезде

Проект предназначен для проверки поездов на появление свободных мест. Используются данные сайта [РЖД](https://pass.rzd.ru). 

Frontend - телеграм бот.

### Как установить

1. Python3 должен быть уже установлен.  
2. Используйте `pip` (или `pip3`, есть конфликт с Python2) для установки зависимостей:
```
pip install -r requirements.txt
```
3. Рекомендуется использовать [virtualenv/venv](https://docs.python.org/3/library/venv.html) для изоляции проекта.

4. Завести бесплатную базу данных на [redislabs.com](https://redislabs.com/), получить адрес, порт и пароль от базы и положить их в `.env` под именами `DB_HOST`, `DB_PORT` и `DB_PASSWORD` соответственно.

5. Установить браузер `GoogleChrome`, скачать подходящий `ChromeDriver` [остюда](https://chromedriver.chromium.org/) и указать путь к файлу драйвера в `.env` под именем `CHROMEDRIVER_PATH`.

6. Для работы с Telegram потребуется:
    * Включить `VPN`, если мессенджер заблокирован в вашей стране; 
    * Получить `bot token` и положить его в `.env` под именем `TG_BOT_TOKEN`, об этом [здесь](https://smmplanner.com/blog/otlozhennyj-posting-v-telegram/);
    * Получить `bot token` для бота-логера, требуемого для отслеживания ошибок в работе ботов. Полученный token в `.env` под именем `TG_LOG_BOT_TOKEN`.
    * Получить свой `id` у `@userinfobot` и положить в `.env` под именем `TG_CHAT_ID`

7. Запустить файл `aio_search_bot.py`.

### Утилита для сбора логов

В репозитории присутствует файл-утилита `collect_logs.py`. Она предназначена для очистки памяти базы данных от собранных логов о поисковых запросах (записи обезличины). Утилита соберет все логи и запишет в json-файл на рабочей машине (допишет, если в файле уже присутствуют записи). Перед её запуском потребуется в файле `.env` указать имя записи логов в базе данных и путь к json-файлу под именами `LOGS_KEY` и `LOGS_PATH` соответственно.
