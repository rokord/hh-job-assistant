from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.browser import BrowserSession
from app.config import get_project_root, load_config, resolve_project_path
from app.logging_setup import setup_logging


logger = logging.getLogger(__name__)

HH_HOME_URL = "https://hh.ru"
OUTPUT_FILE = "data/vacancies_preview.json"


def say(message: str = "") -> None:
    print(message, flush=True)


def pause(message: str) -> None:
    say()
    input(message)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).strip()


def is_login_page(url: str) -> bool:
    parsed = urlparse(url)
    return "/account/login" in parsed.path


def get_collection_config(config: dict) -> dict:
    collection = config.get("collection", {})

    return {
        "max_pages": int(collection.get("max_pages", 3)),
        "scrolls_per_page": int(collection.get("scrolls_per_page", 5)),
        "delay_between_pages_seconds": int(collection.get("delay_between_pages_seconds", 5)),
    }


def get_vacancy_cards(page: Page) -> Locator:
    selectors = [
        '[data-qa="vacancy-serp__vacancy"]',
        'article:has(a[href*="/vacancy/"])',
        '[class*="vacancy-card"]:has(a[href*="/vacancy/"])',
        '[data-qa*="vacancy"]:has(a[href*="/vacancy/"]):has-text("Отклик")',
    ]

    for selector in selectors:
        cards = page.locator(selector)
        count = cards.count()

        if 1 <= count <= 80:
            say(f"Карточки найдены по селектору: {selector}")
            return cards

    return page.locator('[data-qa="__nothing_found__"]')


def first_text(parent: Locator, selectors: list[str], timeout: int = 1200) -> str:
    for selector in selectors:
        locator = parent.locator(selector).first

        try:
            if locator.count() > 0:
                text = normalize_text(locator.inner_text(timeout=timeout))
                if text:
                    return text
        except PlaywrightError:
            continue

    return ""


def get_card_title(card: Locator) -> str:
    return first_text(
        card,
        [
            '[data-qa="serp-item__title"]',
            'a[href*="/vacancy/"]',
        ],
    )


def get_card_url(card: Locator) -> str:
    selectors = [
        '[data-qa="serp-item__title"]',
        'a[href*="/vacancy/"]',
    ]

    for selector in selectors:
        locator = card.locator(selector).first

        try:
            if locator.count() > 0:
                href = locator.get_attribute("href", timeout=1200)

                if not href:
                    continue

                if href.startswith("/"):
                    href = f"https://hh.ru{href}"

                return href.split("?")[0]
        except PlaywrightError:
            continue

    return ""


def get_card_company(card: Locator) -> str:
    return first_text(
        card,
        [
            '[data-qa="vacancy-serp__vacancy-employer"]',
            '[data-qa="vacancy-serp__vacancy-employer-text"]',
            '[data-qa*="employer"]',
        ],
    )


def get_card_salary(card: Locator) -> str:
    salary = first_text(
        card,
        [
            '[data-qa="vacancy-serp__vacancy-compensation"]',
            '[data-qa*="compensation"]',
        ],
    )

    if is_valid_salary_text(salary):
        return salary

    text = get_card_text(card)
    match = re.search(
        r"((?:от|до)?\s*\d[\d\s]*(?:–|-|до)?\s*\d*[\d\s]*\s*(?:₽|руб)[^,]*)",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return ""

    candidate = normalize_text(match.group(1))

    if is_valid_salary_text(candidate):
        return candidate

    return ""


def is_valid_salary_text(value: str) -> bool:
    if not value:
        return False

    lowered = value.lower()

    bad_parts = [
        "выплаты",
        "курс",
        "курсы",
        "промокод",
        "скидка",
    ]

    if any(part in lowered for part in bad_parts):
        return False

    has_money = "₽" in value or "руб" in lowered
    has_digit = bool(re.search(r"\d", value))

    return has_money and has_digit


def get_card_location(card: Locator) -> str:
    location = first_text(
        card,
        [
            '[data-qa="vacancy-serp__vacancy-address"]',
            '[data-qa*="address"]',
        ],
    )

    if location:
        return location

    text = get_card_text(card)

    known_locations = [
        "Москва",
        "Санкт-Петербург",
        "Новосибирск",
        "Нижний Новгород",
        "Владивосток",
        "Кострома",
        "Самара",
        "Чебоксары",
        "Прага",
    ]

    for location_name in known_locations:
        if location_name.lower() in text.lower():
            return location_name

    return ""


def get_card_experience(card: Locator) -> str:
    experience = first_text(
        card,
        [
            '[data-qa*="experience"]',
        ],
    )

    if experience:
        return experience

    text = get_card_text(card)
    match = re.search(r"(Опыт\s+[^О]+?лет|Опыт\s+более\s+\d+\s+лет|Без опыта)", text)

    if match:
        return normalize_text(match.group(1))

    return ""


def get_card_text(card: Locator) -> str:
    try:
        return normalize_text(card.inner_text(timeout=1500))
    except PlaywrightError:
        return ""


def card_has_apply_button(card: Locator) -> bool:
    selectors = [
        'button:has-text("Откликнуться")',
        'a:has-text("Откликнуться")',
        'button:has-text("Отклик без резюме")',
        'a:has-text("Отклик без резюме")',
        '[data-qa*="response"]',
    ]

    for selector in selectors:
        locator = card.locator(selector).first

        try:
            if locator.count() > 0 and locator.is_visible(timeout=700):
                return True
        except PlaywrightError:
            continue

    return False


def is_remote(card: Locator) -> bool:
    text = get_card_text(card).lower()

    return (
        "можно удалённо" in text
        or "можно удаленно" in text
        or "удалённая работа" in text
        or "удаленная работа" in text
    )


def is_bad_title(title: str) -> bool:
    lowered = title.lower()

    bad_exact = {
        "Вакансии на карте",
        "Компании для вас",
        "Курсы для роста и развития в вашей профессии",
    }

    bad_parts = [
        "быстрые фильтры",
        "сотрудник на склад",
        "сборщик заказов",
        "курсы для роста",
        "посмотреть",
        "перейти",
    ]

    if not title:
        return True

    if title in bad_exact:
        return True

    if len(title) < 4:
        return True

    if len(title) > 180:
        return True

    return any(part in lowered for part in bad_parts)


def scroll_page(page: Page, scroll_count: int) -> None:
    say()
    say(f"Скроллим страницу: {scroll_count} раз")

    for index in range(scroll_count):
        if page.is_closed():
            raise RuntimeError("Браузерная страница была закрыта.")

        say(f"  Скролл {index + 1}/{scroll_count}")
        page.mouse.wheel(0, 1400)
        page.wait_for_timeout(1200)


def extract_vacancies_from_page(page: Page, page_number: int) -> list[dict]:
    say()
    say(f"Извлекаем вакансии со страницы {page_number}...")

    cards = get_vacancy_cards(page)
    total = cards.count()

    say(f"Карточек найдено: {total}")

    vacancies_by_url: dict[str, dict] = {}

    for index in range(total):
        card = cards.nth(index)

        try:
            title = get_card_title(card)
            url = get_card_url(card)

            if is_bad_title(title):
                continue

            if not url or "/vacancy/" not in url:
                continue

            if url in vacancies_by_url:
                continue

            vacancies_by_url[url] = {
                "title": title,
                "company": get_card_company(card),
                "salary": get_card_salary(card),
                "location": get_card_location(card),
                "experience": get_card_experience(card),
                "remote": is_remote(card),
                "url": url,
                "can_apply": card_has_apply_button(card),
                "source_page": page_number,
                "collected_at": datetime.now().isoformat(timespec="seconds"),
            }

        except PlaywrightError as error:
            logger.warning("Could not parse card %s: %s", index + 1, error)
            continue

    vacancies = list(vacancies_by_url.values())
    say(f"Уникальных вакансий на странице {page_number}: {len(vacancies)}")

    return vacancies


def find_next_page_url(page: Page, next_page_number: int) -> str | None:
    say()
    say(f"Ищем следующую страницу: {next_page_number}")

    selectors = [
        '[data-qa="pager-next"]',
        f'a:has-text("{next_page_number}")',
        'a:has-text("дальше")',
        'a:has-text("следующая")',
    ]

    for selector in selectors:
        locator = page.locator(selector).last

        try:
            if locator.count() == 0:
                continue

            href = locator.get_attribute("href", timeout=1500)

            if not href:
                continue

            if href.startswith("/"):
                href = f"https://hh.ru{href}"

            say(f"Следующая страница найдена: {href}")
            return href

        except PlaywrightError:
            continue

    say("Следующая страница не найдена.")
    return None


def collect_pages(
    page: Page,
    max_pages: int,
    scrolls_per_page: int,
    delay_between_pages_seconds: int,
) -> list[dict]:
    all_vacancies_by_url: dict[str, dict] = {}

    for page_number in range(1, max_pages + 1):
        say()
        say("=" * 50)
        say(f"СТРАНИЦА {page_number}/{max_pages}")
        say("=" * 50)
        say(f"URL перед сбором: {page.url}")

        if is_login_page(page.url):
            say()
            say("СТОП: сейчас открыта страница логина.")
            say("Нужно авторизоваться и открыть страницу подходящих вакансий.")
            return list(all_vacancies_by_url.values())

        scroll_page(page, scroll_count=scrolls_per_page)

        vacancies = extract_vacancies_from_page(page, page_number)

        added = 0

        for vacancy in vacancies:
            url = vacancy["url"]

            if url in all_vacancies_by_url:
                continue

            all_vacancies_by_url[url] = vacancy
            added += 1

        say(f"Добавлено новых вакансий: {added}")
        say(f"Всего уникальных вакансий: {len(all_vacancies_by_url)}")

        if page_number >= max_pages:
            say("Достигнут лимит страниц.")
            break

        next_url = find_next_page_url(page, page_number + 1)

        if not next_url:
            say("Пагинация завершена.")
            break

        say(f"Пауза перед следующей страницей: {delay_between_pages_seconds} сек.")
        page.wait_for_timeout(delay_between_pages_seconds * 1000)

        say("Открываем следующую страницу...")
        page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

    return list(all_vacancies_by_url.values())


def save_vacancies(project_root: Path, vacancies: list[dict]) -> Path:
    output_path = project_root / OUTPUT_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(vacancies),
        "vacancies": vacancies,
    }

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    return output_path


def print_preview(vacancies: list[dict], limit: int = 20) -> None:
    say()
    say(f"Найдено вакансий: {len(vacancies)}")
    say("--------------------")

    for index, vacancy in enumerate(vacancies[:limit], start=1):
        say()
        say(f"{index}. {vacancy['title']}")
        say(f"   Компания: {vacancy['company'] or '-'}")
        say(f"   Зарплата: {vacancy['salary'] or '-'}")
        say(f"   Локация: {vacancy['location'] or '-'}")
        say(f"   Опыт: {vacancy['experience'] or '-'}")
        say(f"   Удалёнка: {vacancy['remote']}")
        say(f"   Можно откликнуться: {vacancy['can_apply']}")
        say(f"   Страница: {vacancy['source_page']}")
        say(f"   URL: {vacancy['url']}")


def main() -> None:
    project_root = get_project_root()
    setup_logging(project_root)

    say("HH Job Assistant")
    say("----------------")
    say("Режим: сбор вакансий в JSON")
    say(f"Папка проекта: {project_root}")

    try:
        config = load_config()
        collection = get_collection_config(config)

        user_data_dir = resolve_project_path(config["browser"]["user_data_dir"])
        headless = bool(config["browser"]["headless"])

        say()
        say(f"Профиль браузера: {user_data_dir}")
        say(f"Фоновый режим: {headless}")
        say(f"Максимум страниц: {collection['max_pages']}")
        say(f"Скроллов на страницу: {collection['scrolls_per_page']}")
        say(f"Пауза между страницами: {collection['delay_between_pages_seconds']} сек.")

        with BrowserSession(user_data_dir=user_data_dir, headless=headless) as (_, page):
            say()
            say("Открываем hh.ru...")
            page.goto(HH_HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)

            say()
            say(f"Текущий URL: {page.url}")
            say(f"Заголовок: {page.title()}")

            if is_login_page(page.url):
                say()
                say("СТАТУС: НЕ АВТОРИЗОВАН.")
                say("Авторизуйся в открытом браузере.")
                say("Потом открой страницу подходящих вакансий.")

            say()
            say("Открой в браузере страницу подходящих вакансий.")
            say("Когда видишь список вакансий, вернись в PowerShell.")

            pause("Нажми Enter, чтобы начать сбор...")

            say()
            say("Команда получена.")
            say(f"Стартовый URL: {page.url}")

            if is_login_page(page.url):
                say()
                say("Сбор не начат: ты всё ещё на странице логина.")
                say("Авторизуйся, открой подходящие вакансии и запусти скрипт заново.")
                pause("Нажми Enter, чтобы закрыть браузер...")
                return

            vacancies = collect_pages(
                page=page,
                max_pages=collection["max_pages"],
                scrolls_per_page=collection["scrolls_per_page"],
                delay_between_pages_seconds=collection["delay_between_pages_seconds"],
            )

            output_path = save_vacancies(project_root, vacancies)
            print_preview(vacancies)

            say()
            say(f"Файл сохранён: {output_path}")

            pause("Нажми Enter, чтобы закрыть браузер...")

        say("Готово.")

    except KeyboardInterrupt:
        say()
        say("Остановлено пользователем через Ctrl+C.")
        sys.exit(130)

    except RuntimeError as error:
        logger.exception("Runtime error")
        say()
        say("ОШИБКА")
        say(str(error))
        sys.exit(1)

    except (PlaywrightTimeoutError, PlaywrightError) as error:
        logger.exception("Browser error")
        say()
        say("ОШИБКА БРАУЗЕРА")
        say(str(error))
        sys.exit(1)

    except Exception as error:
        logger.exception("Application failed")
        say()
        say("ОШИБКА")
        say(type(error).__name__)
        say(str(error))
        sys.exit(1)


if __name__ == "__main__":
    main()