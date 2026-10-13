# HH Job Assistant

Локальный помощник для работы с подходящими вакансиями на hh.ru.

Проект автоматизирует рутинные действия при откликах: сбор вакансий, полуавтоматические отклики, автоматические отклики по страницам, blacklist компаний, вставку сопроводительного письма и остановку при достижении лимита откликов hh.ru.

Проект работает локально на компьютере через Playwright и отдельный браузерный профиль. Авторизация на hh.ru сохраняется в локальной папке `data/browser-profile`.

---

## Возможности

### Сбор вакансий

Скрипт `src/main.py` открывает hh.ru, ждёт пока пользователь вручную откроет страницу подходящих вакансий, затем собирает вакансии со страниц поиска и сохраняет результат в JSON.

Результат сохраняется в:

```text
data/vacancies_preview.json
```

### Полуавтоматические отклики

Скрипт `src/apply_from_list.py` проходит вакансии по одной и спрашивает действие в терминале.

Доступные команды:

```text
y — откликнуться
n — пропустить
b — добавить компанию в blacklist
q — выйти
```

Этот режим удобен, если хочется контролировать каждый отклик вручную.

### Автоматические отклики по страницам

Скрипт `src/auto_apply_pages.py` автоматически проходит вакансии на странице hh.ru, нажимает кнопку отклика, вставляет сопроводительное письмо при необходимости, обрабатывает попапы и переходит на следующие страницы.

Скрипт умеет:

- искать карточки вакансий на странице;
- пропускать компании из blacklist;
- пропускать вакансии, на которые уже был отклик;
- нажимать кнопку `Откликнуться`;
- вставлять сопроводительное письмо;
- закрывать попапы с роботом-рекрутером;
- возвращаться со страниц опросников и внешних форм обратно к списку;
- переходить на следующую страницу;
- учитывать лимит hh.ru на отклики за 24 часа;
- останавливать работу, если hh.ru показывает уведомление о лимите;
- писать результаты в JSONL-файл.

Результат сохраняется в:

```text
data/auto_apply_results.jsonl
```

---

## Важная логика по лимиту hh.ru

hh.ru ограничивает количество откликов за период 24 часа.

Сайт может не блокировать кнопку `Откликнуться`, даже если лимит уже исчерпан. Вместо этого hh.ru показывает уведомление на странице. Поэтому скрипт после клика проверяет текст страницы и toast-уведомления.

Если найден текст вроде:

```text
В течение 24 часов можно совершить не более 200 откликов
Вы исчерпали лимит откликов
Попробуйте отправить отклик позднее
```

скрипт:

1. записывает событие `daily_limit_reached`;
2. печатает сообщение в терминал;
3. останавливает автоматические отклики;
4. не продолжает бессмысленно нажимать кнопки.

Дополнительно в конфиге есть параметр:

```json
"auto_apply": {
  "max_response_attempts_per_run": 200
}
```

Он ограничивает количество попыток отклика за один запуск скрипта.

---

## Структура проекта

```text
hh-job-assistant-clean/
  config/
    settings.json
    settings.example.json
    cover_letter.txt
    cover_letter.example.txt
    blacklist_companies.txt
    blacklist_companies.example.txt

  data/
    browser-profile/
    vacancies_preview.json
    auto_apply_results.jsonl
    apply_log.jsonl

  logs/
    app.log

  src/
    main.py
    apply_from_list.py
    auto_apply_pages.py

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
  CHANGELOG.md
  .gitignore
```

---

## Что не нужно коммитить в Git

В репозиторий не должны попадать:

```text
.venv/
data/browser-profile/
logs/
data/*.json
data/*.jsonl
config/settings.json
config/cover_letter.txt
config/blacklist_companies.txt
```

Почему:

- `data/browser-profile/` содержит локальный профиль браузера и авторизацию hh.ru;
- `config/cover_letter.txt` содержит личный текст письма;
- `config/blacklist_companies.txt` содержит личные настройки;
- `data/*.jsonl` содержит историю откликов;
- `logs/` содержит локальные логи работы.

Для Git нужны только example-файлы:

```text
config/settings.example.json
config/cover_letter.example.txt
config/blacklist_companies.example.txt
```

---

## Установка

### 1. Перейти в папку проекта

```powershell
cd C:\Users\notamedia\Documents\hh-job-assistant-clean
```

### 2. Создать виртуальное окружение

```powershell
python -m venv .venv
```

### 3. Активировать виртуальное окружение

```powershell
.\.venv\Scripts\Activate.ps1
```

Если PowerShell запрещает запуск скриптов, выполнить один раз:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Потом снова:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. Обновить pip

```powershell
python -m pip install --upgrade pip
```

### 5. Установить зависимости

```powershell
pip install -r requirements.txt
```

### 6. Установить браузер Playwright

```powershell
python -m playwright install chromium
```

---

## Первичная настройка

### 1. Создать локальный конфиг

```powershell
Copy-Item config\settings.example.json config\settings.json
```

### 2. Создать файл сопроводительного письма

```powershell
Copy-Item config\cover_letter.example.txt config\cover_letter.txt
```

После этого открыть файл:

```powershell
code config\cover_letter.txt
```

И вписать свой текст сопроводительного письма.

### 3. Создать файл blacklist

```powershell
Copy-Item config\blacklist_companies.example.txt config\blacklist_companies.txt
```

Открыть:

```powershell
code config\blacklist_companies.txt
```

Формат:

```text
Компания 1
Компания 2
Компания 3
```

Одна компания на строку.

---

## Конфигурация

Основной локальный конфиг:

```text
config/settings.json
```

Пример:

```json
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
```

### Параметры

#### `browser.headless`

```json
"headless": false
```

Если `false`, браузер виден на экране.

Для этого проекта лучше оставлять:

```json
"headless": false
```

#### `browser.user_data_dir`

```json
"user_data_dir": "data/browser-profile"
```

Папка профиля браузера. В ней сохраняется авторизация hh.ru.

#### `collection.max_pages`

```json
"max_pages": 3
```

Максимальное количество страниц вакансий, которые обрабатывает скрипт.

#### `collection.scrolls_per_page`

```json
"scrolls_per_page": 5
```

Количество прокруток страницы при сборе вакансий.

#### `apply.auto_insert_cover_letter`

```json
"auto_insert_cover_letter": true
```

Автоматически вставлять сопроводительное письмо.

#### `apply.auto_submit_after_cover_letter`

```json
"auto_submit_after_cover_letter": false
```

Автоматически отправлять отклик после вставки письма.

Для осторожного режима лучше держать `false`.

#### `apply.ask_before_each_apply`

```json
"ask_before_each_apply": true
```

Используется в полуавтоматическом режиме.

#### `auto_apply.max_response_attempts_per_run`

```json
"max_response_attempts_per_run": 200
```

Максимальное количество попыток отклика за один запуск автоматического скрипта.

---

## Авторизация на hh.ru

При первом запуске скрипт откроет браузер.

Нужно:

1. Войти на hh.ru вручную.
2. Дождаться, что профиль открылся успешно.
3. Открыть страницу подходящих вакансий.
4. Вернуться в PowerShell.
5. Нажать Enter.

Авторизация сохранится в:

```text
data/browser-profile/
```

При следующих запусках вход обычно не потребуется.

---

## Скрипт `src/main.py`

### Назначение

Собирает вакансии со страницы подходящих вакансий и сохраняет их в JSON.

### Запуск

```powershell
python src\main.py
```

### Как пользоваться

1. Запустить скрипт.
2. Дождаться открытия браузера.
3. Открыть страницу подходящих вакансий на hh.ru.
4. Убедиться, что URL начинается с:

```text
https://hh.ru/search/vacancy
```

5. Вернуться в PowerShell.
6. Нажать Enter.
7. Дождаться завершения.

### Результат

Файл:

```text
data/vacancies_preview.json
```

Пример данных:

```json
[
  {
    "title": "Project Manager",
    "company": "Example Company",
    "url": "https://hh.ru/vacancy/123456",
    "text": "..."
  }
]
```

### Когда использовать

Этот режим нужен, если хочется сначала посмотреть список вакансий, а не откликаться сразу.

---

## Скрипт `src/apply_from_list.py`

### Назначение

Полуавтоматический режим откликов.

Скрипт показывает вакансии по одной и спрашивает, что делать.

### Запуск

```powershell
python src\apply_from_list.py
```

### Управление

```text
y — откликнуться на вакансию
n — пропустить вакансию
b — добавить компанию в blacklist и пропустить
q — выйти из скрипта
```

### Что умеет

- показывает название вакансии;
- показывает компанию;
- показывает ссылку;
- проверяет blacklist;
- вставляет сопроводительное письмо;
- пишет лог откликов;
- позволяет добавлять компанию в blacklist прямо во время работы.

### Когда использовать

Этот режим нужен, если хочется лично принимать решение по каждой вакансии.

---

## Скрипт `src/auto_apply_pages.py`

### Назначение

Автоматический режим откликов по страницам подходящих вакансий.

### Запуск

```powershell
python src\auto_apply_pages.py
```

### Как пользоваться

1. Запустить скрипт.
2. Откроется hh.ru.
3. Открыть страницу подходящих вакансий.
4. Проверить, что URL начинается с:

```text
https://hh.ru/search/vacancy
```

5. Вернуться в PowerShell.
6. Нажать Enter.
7. Скрипт начнёт автоматическую обработку.

### Что делает скрипт

Для каждой карточки вакансии:

1. Читает название вакансии.
2. Читает название компании.
3. Проверяет blacklist.
4. Проверяет, был ли уже отклик.
5. Ищет кнопку `Откликнуться`.
6. Нажимает кнопку.
7. Увеличивает счётчик попыток отклика.
8. Проверяет уведомление о лимите hh.ru.
9. Если появилось сопроводительное письмо — вставляет текст.
10. Если появился робот-рекрутер — закрывает попап.
11. Если произошёл переход на опросник или внешнюю форму — логирует и возвращается назад.
12. Если отклик отправлен — записывает `sent`.
13. Переходит к следующей вакансии.
14. После страницы переходит на следующую страницу.

### Статусы в результате

Файл:

```text
data/auto_apply_results.jsonl
```

Возможные статусы:

```text
sent
blacklist
already_applied
no_apply_button
external_form_opened
chatbot_closed
manual_required
daily_limit_reached
unknown_after_click
error
```

### Значение статусов

#### `sent`

Отклик точно отправлен.

#### `blacklist`

Компания находится в blacklist, вакансия пропущена.

#### `already_applied`

На эту вакансию уже был отклик.

#### `no_apply_button`

Скрипт не нашёл кнопку отклика.

#### `external_form_opened`

После клика hh.ru открыл отдельную страницу, форму, опросник или тест.

Важно: такая попытка может тратить лимит откликов hh.ru, даже если скрипт не прошёл опросник.

#### `chatbot_closed`

Отклик привёл к роботу-рекрутеру или чату. Скрипт закрыл попап и пошёл дальше.

#### `manual_required`

Вакансия требует ручного действия: тест, вопросы работодателя, выбор резюме или проверку.

#### `daily_limit_reached`

hh.ru показал уведомление о лимите откликов за 24 часа. Скрипт остановился.

#### `unknown_after_click`

После клика не удалось точно определить результат.

#### `error`

Произошла ошибка Playwright или ошибка обработки карточки.

---

## Blacklist компаний

Файл:

```text
config/blacklist_companies.txt
```

Формат:

```text
Компания 1
Компания 2
Компания 3
```

Пример:

```text
ООО Рога и Копыта
Кадровое агентство Пример
ИП Иванов Иван Иванович
```

Если компания находится в blacklist, скрипт пропускает все её вакансии.

Проверка работает по названию компании. Если название на hh.ru отличается, компания может не совпасть. Тогда нужно добавить ещё один вариант написания.

---

## Сопроводительное письмо

Файл:

```text
config/cover_letter.txt
```

Пример:

```text
Здравствуйте!

Меня заинтересовала ваша вакансия. У меня есть опыт управления IT/digital-проектами, взаимодействия с командами разработки, заказчиками и подрядчиками.

Буду рад обсудить, чем могу быть полезен вашей команде.
```

Если hh.ru показывает поле сопроводительного письма, скрипт вставляет туда текст из этого файла.

---

## Логи

Основной лог приложения:

```text
logs/app.log
```

Результаты автоматических откликов:

```text
data/auto_apply_results.jsonl
```

Результаты полуавтоматических откликов:

```text
data/apply_log.jsonl
```

Собранные вакансии:

```text
data/vacancies_preview.json
```

---

## Формат `auto_apply_results.jsonl`

Каждая строка — отдельный JSON-объект.

Пример:

```json
{"status":"sent","page":1,"title":"Project Manager","company":"Example","url":"https://hh.ru/vacancy/123","response_attempts_in_this_run":15,"timestamp":"2026-07-13T21:30:00"}
```

Это удобно для анализа, потому что файл можно читать построчно.

---

## Рекомендуемый сценарий работы

### Осторожный сценарий

1. Запустить сбор вакансий:

```powershell
python src\main.py
```

2. Посмотреть `data/vacancies_preview.json`.

3. Запустить полуавтоматический режим:

```powershell
python src\apply_from_list.py
```

4. Добавить лишние компании в blacklist.

5. Запустить автоматический режим:

```powershell
python src\auto_apply_pages.py
```

### Быстрый сценарий

1. Обновить blacklist.
2. Обновить сопроводительное письмо.
3. Запустить:

```powershell
python src\auto_apply_pages.py
```

---

## Проверка перед запуском автооткликов

Перед запуском стоит проверить:

```text
1. Авторизация на hh.ru работает.
2. Открыта страница подходящих вакансий.
3. URL начинается с https://hh.ru/search/vacancy
4. config/cover_letter.txt заполнен.
5. config/blacklist_companies.txt обновлён.
6. config/settings.json содержит нужный max_pages.
7. auto_apply.max_response_attempts_per_run выставлен корректно.
```

---

## Частые проблемы

### Скрипт пишет, что это не страница вакансий

Нужно открыть страницу, где URL начинается с:

```text
https://hh.ru/search/vacancy
```

Например:

```text
https://hh.ru/search/vacancy?resume=...
```

### Скрипт открыл hh.ru, но не видит страницу вакансий

Иногда нужная страница открыта в другой вкладке. Скрипт умеет искать вкладку со страницей вакансий, но лучше оставить открытой только нужную вкладку hh.ru.

### Сопроводительное письмо не вставилось

Проверь файл:

```text
config/cover_letter.txt
```

Он не должен быть пустым.

### Компания не пропускается blacklist

Проверь точное название компании на hh.ru и добавь такой же вариант в:

```text
config/blacklist_companies.txt
```

### Скрипт остановился на лимите hh.ru

Это нормально. hh.ru ограничивает отклики за период 24 часа. Нужно подождать, пока часть лимита освободится.

### Скрипт перешёл на опросник

Это нормальный кейс. Скрипт логирует `external_form_opened` и возвращается к списку вакансий.

---

## Git

### Первый запуск Git

```powershell
git init
```

### Проверить статус

```powershell
git status
```

Перед коммитом убедиться, что в Git не попали:

```text
.venv/
data/browser-profile/
config/settings.json
config/cover_letter.txt
config/blacklist_companies.txt
data/*.json
data/*.jsonl
logs/
```

### Добавить файлы

```powershell
git add .
```

### Сделать коммит

```powershell
git commit -m "Initial stable version"
```

### Подключить GitHub

```powershell
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/hh-job-assistant.git
git push -u origin main
```

Если `origin` уже существует:

```powershell
git remote remove origin
git remote add origin https://github.com/YOUR_USERNAME/hh-job-assistant.git
git push -u origin main
```

---

## Безопасность

Проект предназначен для локального использования.

Не публиковать:

```text
data/browser-profile/
config/settings.json
config/cover_letter.txt
config/blacklist_companies.txt
data/auto_apply_results.jsonl
data/apply_log.jsonl
logs/
```

Особенно важно не публиковать `data/browser-profile/`, потому что там может быть сохранён вход на hh.ru.

---

## Текущий стабильный статус

```text
main.py              — сбор вакансий работает
apply_from_list.py   — полуавтоматические отклики работают
auto_apply_pages.py  — автоматические отклики работают
blacklist            — работает
cover_letter         — вставляется
лимит hh.ru          — учитывается
переходы на опросник — обрабатываются
пагинация            — работает
```

---

## Лицензия

Проект предназначен для личного локального использования.