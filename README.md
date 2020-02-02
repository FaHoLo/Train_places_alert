# Ищем места в забитом поезде

Проект предназначен для проверки поездов на появление свободных мест. Используются данные сайта [РЖД](https://pass.rzd.ru). Frontend - телеграм бот.

### Как установить

1. Python3 должен быть уже установлен.  
2. Используйте `pip` (или `pip3`, есть конфликт с Python2) для установки зависимостей:
```
pip install -r requirements.txt
```
3. Рекомендуется использовать [virtualenv/venv](https://docs.python.org/3/library/venv.html) для изоляции проекта.

4. Создать две google таблицы и занести их `id` (найти его можно в url страницы) в файл `.env` под именем `PROCESSING_SPREADSHEET_ID` и `LOGGING_SPREADSHEET_ID`.

5. Создать проект и получить к нему `credentials.json` [здесь](https://developers.google.com/sheets/api/quickstart/python) (Step 1). Полученный файл положить в папку с программой.

6. Положить `geckodriver.exe` в папку расположения браузера `Firefox`. Поправить расположение папки в функции `make_rzd_request`. В функции есть подсказки для пользователей `GoogleChrome`.

7. Для работы с Telegram потребуется:
    * Включить `VPN`, если мессенджер заблокирован в вашей стране 
    * Получить `bot token` и положить его в `.env` под именем `TG_BOT_TOKEN`, об этом [здесь](https://smmplanner.com/blog/otlozhennyj-posting-v-telegram/)

8. Запустить файл `start_bot.py`.

### Важно знать

1. При первом запуске откроется окно браузера для предоставления прав доступа программе к `spreadsheets`, разрешить доступ нужно вручную.
2. В репозитории есть файлы с:
    * Примерами HTML ответов от сайта РЖД
    * Ботом, работающим без асинхронности 
    * Ботом-примером с диалоговыми командами из документации `python-telegram-bot`
