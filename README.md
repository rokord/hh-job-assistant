# HH Job Assistant

Локальное Windows-приложение для работы с hh.ru.

Проект помогает автоматизировать рутинные действия при поиске работы:

- собирать вакансии со страницы поиска hh.ru;
- отправлять полуавтоматические отклики;
- отправлять автоматические отклики по страницам;
- вставлять сопроводительное письмо;
- учитывать blacklist компаний;
- отслеживать лимит hh.ru 200 откликов / 24 часа;
- отправлять follow-up сообщения работодателям в чатах;
- собирать статистику в локальном dashboard.

Приложение работает локально на компьютере через Python и Playwright. Браузер запускается в видимом режиме, авторизация hh.ru сохраняется в локальном профиле.

---

## Содержание

- [Возможности](#возможности)
- [Требования](#требования)
- [Установка с нуля](#установка-с-нуля)
- [Настройка проекта](#настройка-проекта)
- [Первый запуск и авторизация HH](#первый-запуск-и-авторизация-hh)
- [Запуск через меню](#запуск-через-меню)
- [Основные режимы](#основные-режимы)
- [Dashboard](#dashboard)
- [Структура проекта](#структура-проекта)
- [Локальные файлы и безопасность](#локальные-файлы-и-безопасность)
- [Обновление проекта](#обновление-проекта)
- [Проверка работоспособности](#проверка-работоспособности)
- [Частые проблемы](#частые-проблемы)
- [GitHub: что заливать и что не заливать](#github-что-заливать-и-что-не-заливать)

---

## Возможности

### Сбор вакансий

Скрипт открывает hh.ru, пользователь вручную открывает страницу поиска, после чего приложение собирает карточки вакансий и сохраняет результат в JSON.

Результат сохраняется в:

```text
data/vacancies_preview.json

Полуавтоматические отклики
Скрипт проходит по видимым карточкам вакансий на странице поиска и спрашивает действие по каждой вакансии:
y - откликнуться
n - пропустить
b - добавить компанию в blacklist
q - выйти

Лог сохраняется в:
data/apply_log.jsonl

Автоматические отклики по страницам
Скрипт автоматически проходит по карточкам вакансий на странице поиска hh.ru.
Он умеет:
- находить кнопку отклика;
- пропускать компании из blacklist;
- пропускать вакансии, на которые уже был отклик;
- вставлять сопроводительное письмо;
- отправлять отклик;
- ловить лимит hh.ru;
- переходить на следующую страницу;
- логировать результат каждого действия.
Лог сохраняется в:
data/auto_apply_results.jsonl

Follow-up сообщения в чатах HH
Скрипт работает со страницей:
https://hh.ru/chat

Пользователь вручную скроллит список чатов до нужной глубины, после чего скрипт идёт по видимым чатам снизу вверх и отправляет напоминание работодателю.
Текст напоминания:
Добрый день, ваша вакансия мне интересна, напоминаю о своём отклике!

Скрипт не отправляет сообщение, если:
- вакансия в архиве;
- был отказ;
- переписка отключена;
- нет поля ввода;
- последнее сообщение свежее 24 часов;
- чат старше 14 дней;
- такое напоминание уже отправлялось;
- работодатель прислал вопросы, форму, ссылку, анкету или тестовое задание.
Логи сохраняются в:
data/followup_chats_results.jsonl
data/followup_manual_review.jsonl

Dashboard
Локальный dashboard показывает статистику по логам проекта:
- количество запусков скриптов;
- количество отправленных откликов;
- количество сопроводительных писем;
- состояние лимита hh.ru 200 / 24 часа;
- количество проверенных чатов;
- количество отправленных напоминаний;
- архивы и отказы;
- manual review;
- последние события;
- хвост логов.
Адрес dashboard:
http://127.0.0.1:8765

Требования
Проект рассчитан на Windows.
Нужно установить:
- Python 3.11 или новее;
- Git;
- Visual Studio Code, желательно;
- Playwright Chromium.
Обычный Google Chrome устанавливать не обязательно. Приложение использует Chromium, который скачивается через Playwright.
Установка с нуля
1. Установить Python
Скачать Python:
https://www.python.org/downloads/

При установке обязательно поставить галочку:
Add Python to PATH

Проверить установку:
python --version

Должно показать примерно:
Python 3.12.x

2. Установить Git
Скачать Git:
https://git-scm.com/download/win

Проверить установку:
git --version

3. Установить VS Code
Скачать VS Code:
https://code.visualstudio.com/

VS Code нужен для удобного редактирования настроек, сопроводительного письма и blacklist.
4. Скачать проект
Открыть PowerShell и перейти в папку Documents:
cd $HOME\Documents

Склонировать репозиторий:
git clone https://github.com/rokord/hh-job-assistant.git

Перейти в папку проекта:
cd hh-job-assistant

5. Создать виртуальное окружение
В папке проекта выполнить:
python -m venv .venv

Активировать окружение:
.\.venv\Scripts\Activate.ps1

Если PowerShell запрещает запуск .ps1, выполнить один раз:
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

Потом снова активировать окружение:
.\.venv\Scripts\Activate.ps1

Если всё хорошо, слева в PowerShell появится:
(.venv)

6. Установить зависимости
В активированном окружении:
pip install -r requirements.txt

7. Установить браузер Playwright
Обычный Google Chrome устанавливать не нужно.
Приложение использует Chromium от Playwright. Он ставится командой:
python -m playwright install chromium

Настройка проекта
После установки нужно создать локальные конфигурационные файлы.
В папке проекта выполнить:
copy config\settings.example.json config\settings.json
copy config\cover_letter.example.txt config\cover_letter.txt
copy config\blacklist_companies.example.txt config\blacklist_companies.txt

Если какие-то файлы уже существуют, PowerShell может спросить подтверждение перезаписи. В таком случае лучше сначала проверить содержимое файла вручную.
Настройка settings.json
Открыть файл:
code config\settings.json

Базовый вариант настроек:
{
  "app": {
    "name": "HH Job Assistant",
    "environment": "local"
  },
  "browser": {
    "headless": false,
    "user_data_dir": "data/browser-profile"
  },
  "collection": {
    "max_pages": 3,
    "scrolls_per_page": 5,
    "delay_between_pages_seconds": 5
  },
  "apply": {
    "delay_after_click_seconds": 2,
    "auto_insert_cover_letter": true,
    "auto_submit_after_cover_letter": false,
    "skip_already_applied": true,
    "ask_before_each_apply": true
  },
  "auto_apply": {
    "max_response_attempts_per_run": 200
  }
}

Важные параметры:
"headless": false

Браузер должен быть видимым, чтобы пользователь мог авторизоваться на hh.ru и контролировать работу скриптов.
"auto_insert_cover_letter": true

Скрипт будет пытаться вставлять сопроводительное письмо.
"auto_submit_after_cover_letter": false

В полуавтоматическом режиме после вставки письма скрипт не будет сам нажимать финальную кнопку, если это требует ручной проверки.
"max_response_attempts_per_run": 200

Максимальное количество попыток отклика за один запуск.
Настройка сопроводительного письма
Открыть файл:
code config\cover_letter.txt

Пример:
Добрый день!

Меня заинтересовала ваша вакансия. У меня есть опыт управления digital- и IT-проектами, работы с командами разработки, заказчиками, backlog, сроками и результатом.

Буду рад обсудить, чем могу быть полезен вашей команде.

Скрипт будет использовать этот текст при откликах, если на hh.ru появляется поле сопроводительного письма.
Настройка blacklist компаний
Открыть файл:
code config\blacklist_companies.txt

Каждая компания указывается с новой строки:
ООО Рога и Копыта
Нежелательная компания

Скрипты будут пропускать компании из этого списка.
Первый запуск и авторизация HH
Запустить простую запускалку:
.\run_simple.ps1

Если PowerShell не запускает .ps1, использовать bat-файл:
.\run_simple.bat

При первом запуске откроется Chromium.
Нужно вручную авторизоваться на:
https://hh.ru

Авторизация сохраняется в локальном профиле браузера:
data/browser-profile/

После первой авторизации повторно входить обычно не нужно.
Важно: масштаб страницы в браузере должен быть 100%.
Сброс масштаба:
Ctrl + 0

Если масштаб установлен, например, 75%, Playwright может медленно нажимать кнопки или считать, что элемент находится вне видимой области.
Запуск через меню
Основная запускалка:
.\run_simple.ps1

Альтернативный запуск:
.\run_simple.bat

Меню:
1 - Собрать вакансии в JSON
2 - Полуавтоматические отклики из списка
3 - Автоотклики по страницам
4 - Напоминания в чатах HH
5 - Открыть локальный dashboard
6 - Открыть настройки
7 - Открыть сопроводительное письмо
8 - Открыть blacklist компаний
0 - Выход

Основные режимы
1. Сбор вакансий
Команда:
python src\main.py

Что делает:
- открывает hh.ru;
- пользователь вручную открывает страницу поиска вакансий;
- скрипт собирает карточки вакансий;
- сохраняет результат в JSON.
Результат:
data/vacancies_preview.json

2. Полуавтоматические отклики
Команда:
python src\apply_from_list.py

Как работает:
1. Скрипт открывает hh.ru.
2. Пользователь открывает страницу поиска вакансий.
3. Скрипт проходит по карточкам.
4. По каждой карточке спрашивает действие.
Команды:
y - откликнуться
n - пропустить
b - добавить компанию в blacklist
q - выйти

Что логируется:
data/apply_log.jsonl

В лог попадают:
- отправленные отклики;
- вакансии, где уже был отклик;
- вакансии, требующие ручного действия;
- пропуски;
- ошибки.
Если после клика нужно выбрать резюме, пройти тест, ответить на вопросы или вручную проверить письмо, скрипт пишет статус:
manual

3. Автоматические отклики по страницам
Команда:
python src\auto_apply_pages.py

Как работает:
1. Скрипт открывает hh.ru.
2. Пользователь открывает страницу поиска вакансий.
3. Скрипт автоматически проходит по карточкам.
4. Пропускает blacklist.
5. Пропускает уже отправленные отклики.
6. Нажимает кнопку отклика.
7. Вставляет сопроводительное письмо.
8. Отправляет отклик, если это возможно.
9. Логирует результат.
10. Переходит к следующей вакансии и следующей странице.
Лог:
data/auto_apply_results.jsonl

Возможные статусы:
sent
blacklist
already_applied
no_apply_button
manual_required
external_form_opened
chatbot_closed
daily_limit_reached
unknown_after_click
click_failed
error

Особенности:
- лимит hh.ru 200 откликов / 24 часа определяется по уведомлению на странице;
- при достижении лимита скрипт останавливается;
- если вакансия открывает внешнюю форму или отдельную страницу, скрипт логирует это и возвращается к списку;
- если появляется тестовое, вопросы или анкета, вакансия уходит в ручную обработку.
4. Напоминания в чатах HH
Команда:
python src\followup_chats_review.py

Как работает:
1. Скрипт открывает:
https://hh.ru/chat

2. Пользователь вручную скроллит левый список чатов вниз до нужной глубины.
3. Пользователь возвращается в PowerShell и нажимает Enter.
4. Скрипт идёт по чатам снизу вверх.
5. Если чат подходит, отправляет напоминание.
Текст напоминания:
Добрый день, ваша вакансия мне интересна, напоминаю о своём отклике!

Скрипт отправляет сообщение только если:
- последнее сообщение старше 24 часов;
- чат не старше 14 дней;
- нет отказа;
- вакансия не в архиве;
- переписка не отключена;
- есть поле ввода;
- такое напоминание ещё не отправлялось;
- работодатель не прислал вопросы, форму, ссылку или тестовое.
Логи:
data/followup_chats_results.jsonl
data/followup_manual_review.jsonl

Manual review используется для чатов, где нужно ответить работодателю руками.
Dashboard
Команда:
python src\dashboard.py

Адрес:
http://127.0.0.1:8765

Dashboard показывает:
- количество запусков скриптов;
- отправленные отклики;
- сопроводительные письма;
- количество вакансий с тестами, вопросами или формами;
- лимит hh.ru 200 откликов / 24 часа;
- проверенные чаты;
- отправленные follow-up сообщения;
- архивы и отказы;
- manual review;
- последние follow-up события;
- статусы follow-up;
- последние auto apply события;
- хвост logs/app.log.
Dashboard строит статистику по локальным файлам:
data/script_runs.jsonl
data/auto_apply_results.jsonl
data/apply_log.jsonl
data/followup_chats_results.jsonl
data/followup_manual_review.jsonl
logs/app.log

Важно: dashboard показывает локальную статистику. Если отклики отправлялись вручную на hh.ru или с другого компьютера, они могут не попасть в локальные логи.
Структура проекта
Примерная структура проекта:
hh-job-assistant/
  config/
    settings.example.json
    settings.json
    cover_letter.example.txt
    cover_letter.txt
    blacklist_companies.example.txt
    blacklist_companies.txt

  data/
    browser-profile/
    vacancies_preview.json
    auto_apply_results.jsonl
    apply_log.jsonl
    followup_chats_results.jsonl
    followup_manual_review.jsonl
    script_runs.jsonl

  logs/
    app.log

  src/
    main.py
    apply_from_list.py
    auto_apply_pages.py
    followup_chats_review.py
    dashboard.py

    app/
      __init__.py
      apply_log.py
      blacklist.py
      browser.py
      config.py
      cover_letter.py
      logging_setup.py
      textio.py

  requirements.txt
  README.md
  run_simple.ps1
  run_simple.bat
  run.ps1
  run.bat
  .gitignore

Локальные файлы и безопасность
Эти файлы не нужно коммитить в GitHub:
.venv/
data/browser-profile/
data/*.json
data/*.jsonl
logs/*.log
config/settings.json
config/cover_letter.txt
config/blacklist_companies.txt

Почему:
data/browser-profile/ — профиль браузера и авторизация hh.ru
config/settings.json — локальные настройки
config/cover_letter.txt — личное сопроводительное письмо
config/blacklist_companies.txt — личный blacklist
data/*.jsonl — история откликов и сообщений
logs/*.log — логи работы приложения
.venv/ — локальное виртуальное окружение Python

В GitHub должны попадать только example-файлы:
config/settings.example.json
config/cover_letter.example.txt
config/blacklist_companies.example.txt

Обновление проекта
Если проект уже установлен, обновить его можно так:
cd $HOME\Documents\hh-job-assistant
git pull
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium

После обновления можно проверить синтаксис:
python -m py_compile src\main.py
python -m py_compile src\apply_from_list.py
python -m py_compile src\auto_apply_pages.py
python -m py_compile src\followup_chats_review.py
python -m py_compile src\dashboard.py

Если команды ничего не вывели — ошибок синтаксиса нет.
Проверка работоспособности
Из папки проекта:
cd $HOME\Documents\hh-job-assistant
.\.venv\Scripts\Activate.ps1

Проверить основные скрипты:
python -m py_compile src\main.py
python -m py_compile src\apply_from_list.py
python -m py_compile src\auto_apply_pages.py
python -m py_compile src\followup_chats_review.py
python -m py_compile src\dashboard.py

Запустить меню:
.\run_simple.ps1

Или:
.\run_simple.bat

Частые проблемы
PowerShell не запускает .ps1
Ошибка может быть связана с политикой выполнения скриптов.
Решение:
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

После этого:
.\run_simple.ps1

Альтернатива:
.\run_simple.bat

Команда python не найдена
Python не добавлен в PATH.
Решение:
1. Переустановить Python.
2. При установке поставить галочку:
Add Python to PATH

Проверить:
python --version

Не установлен браузер Playwright
Ошибка может возникнуть, если не установлен Chromium.
Решение:
python -m playwright install chromium

Нужно снова авторизоваться на hh.ru
При первом запуске это нормально.
Авторизация сохраняется в:
data/browser-profile/

Если удалить эту папку, авторизация сбросится.
Скрипт медленно нажимает кнопки
Проверить масштаб страницы в браузере.
Рекомендуемый масштаб:
100%

Сброс масштаба:
Ctrl + 0

При масштабе 75% или другом нестандартном масштабе Playwright может дольше нажимать кнопки, потому что элементы могут оказаться вне видимой области.
Dashboard показывает не все отклики
Dashboard считает только локальные логи.
Если отклики были отправлены вручную на hh.ru, с другого компьютера или старой версией скрипта без логирования, они могут не попасть в статистику.
HH пишет про лимит откликов
На hh.ru действует лимит откликов за 24 часа.
Скрипт пытается распознать сообщение о лимите и остановиться.
Dashboard показывает состояние лимита по локальным логам и событиям daily_limit_reached.
GitHub: что заливать и что не заливать
Заливать в GitHub
Основные файлы:
README.md
requirements.txt
run_simple.ps1
run_simple.bat
run.ps1
run.bat

Скрипты:
src/main.py
src/apply_from_list.py
src/auto_apply_pages.py
src/followup_chats_review.py
src/dashboard.py

Вспомогательные модули:
src/app/__init__.py
src/app/apply_log.py
src/app/blacklist.py
src/app/browser.py
src/app/config.py
src/app/cover_letter.py
src/app/logging_setup.py
src/app/textio.py

Example-конфиги:
config/settings.example.json
config/cover_letter.example.txt
config/blacklist_companies.example.txt

Не заливать в GitHub
.venv/
data/
logs/
data/browser-profile/
config/settings.json
config/cover_letter.txt
config/blacklist_companies.txt

Старые экспериментальные файлы
Если в проекте остались старые экспериментальные скрипты, их лучше не заливать:
src/followup_chats.py
src/followup_negotiations.py

Актуальный скрипт для follow-up:
src/followup_chats_review.py

Команды для коммита актуальной версии
Перед коммитом проверить статус:
git status

Добавить нужные файлы:
git add README.md
git add requirements.txt
git add run_simple.ps1
git add run_simple.bat
git add run.ps1
git add run.bat

git add src\main.py
git add src\apply_from_list.py
git add src\auto_apply_pages.py
git add src\followup_chats_review.py
git add src\dashboard.py

git add src\app\__init__.py
git add src\app\apply_log.py
git add src\app\blacklist.py
git add src\app\browser.py
git add src\app\config.py
git add src\app\cover_letter.py
git add src\app\logging_setup.py
git add src\app\textio.py

git add config\settings.example.json
git add config\cover_letter.example.txt
git add config\blacklist_companies.example.txt

Проверить, что попадёт в коммит:
git status

Сделать коммит:
git commit -m "Update HH Job Assistant"

Залить на GitHub:
git push

Финальная проверка:
git status

Хороший результат:
On branch main
Your branch is up to date with 'origin/main'.
nothing to commit, working tree clean
