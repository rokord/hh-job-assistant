from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.browser import BrowserSession
from app.config import get_project_root, load_config, resolve_project_path
from app.logging_setup import setup_logging


logger = logging.getLogger(__name__)

HH_HOME_URL = "https://hh.ru"

COVER_LETTER_FILE = "config/cover_letter.txt"
BLACKLIST_FILE = "config/blacklist_companies.txt"
RESULTS_FILE = "data/auto_apply_results.jsonl"


@dataclass
class AutoApplyStats:
    response_attempts: int = 0
    sent: int = 0
    external_form_opened: int = 0
    blacklist: int = 0
    already_applied: int = 0
    manual_required: int = 0
    chatbot_closed: int = 0
    daily_limit_reached: int = 0
    navigated_back: int = 0
    skipped: int = 0
    errors: int = 0


def say(message: str = "") -> None:
    print(message, flush=True)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).strip()


def normalize_company(value: str | None) -> str:
    value = normalize_text(value).lower()
    value = value.replace("«", "").replace("»", "").replace('"', "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def is_login_page(url: str) -> bool:
    parsed = urlparse(url)
    return "/account/login" in parsed.path


def is_valid_vacancy_list_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.netloc not in {"hh.ru", "www.hh.ru"}:
        return False

    return parsed.path == "/search/vacancy"


def get_nested(config: dict, path: str, default):
    current = config

    for part in path.split("."):
        if not isinstance(current, dict):
            return default

        if part not in current:
            return default

        current = current[part]

    return current


def get_active_vacancy_page(context: BrowserContext, fallback: Page) -> Page:
    for page in reversed(context.pages):
        try:
            if page.is_closed():
                continue

            if is_valid_vacancy_list_url(page.url):
                return page
        except PlaywrightError:
            continue

    return fallback


def wait_for_vacancy_page(context: BrowserContext, fallback: Page) -> Page:
    page = fallback

    while True:
        page.wait_for_timeout(1500)
        page = get_active_vacancy_page(context, page)

        say()
        say("Открытые вкладки:")
        for index, opened_page in enumerate(context.pages, start=1):
            try:
                say(f"  {index}. {opened_page.url}")
            except PlaywrightError:
                pass

        say()
        say(f"Текущая рабочая вкладка: {page.url}")

        if is_login_page(page.url):
            say("Сейчас открыта страница логина.")
            say("Авторизуйся и открой страницу подходящих вакансий.")
            input("Когда страница подходящих вакансий открыта, нажми Enter...")
            continue

        if is_valid_vacancy_list_url(page.url):
            return page

        say()
        say("Не вижу открытую вкладку со списком подходящих вакансий.")
        say("Открой страницу, где URL начинается с:")
        say("https://hh.ru/search/vacancy")
        input("Когда откроешь нужную страницу, нажми Enter...")


def read_text_file(path: Path, default: str = "") -> str:
    if not path.exists():
        return default

    return path.read_text(encoding="utf-8").strip()


def read_blacklist(path: Path) -> set[str]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return set()

    items: set[str] = set()

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        items.add(normalize_company(line))

    return items


def is_company_blacklisted(company: str, blacklist: set[str]) -> bool:
    normalized = normalize_company(company)

    if not normalized:
        return False

    if normalized in blacklist:
        return True

    for blocked in blacklist:
        if len(blocked) >= 4 and blocked in normalized:
            return True

    return False


def append_result(project_root: Path, payload: dict) -> None:
    output_path = project_root / RESULTS_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload["timestamp"] = datetime.now().isoformat(timespec="seconds")

    with output_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def print_stats(stats: AutoApplyStats) -> None:
    say()
    say("Статистика")
    say("----------")
    say(f"Попыток отклика: {stats.response_attempts}")
    say(f"Отправлено точно: {stats.sent}")
    say(f"Опросники/внешние формы: {stats.external_form_opened}")
    say(f"Blacklist: {stats.blacklist}")
    say(f"Уже откликались: {stats.already_applied}")
    say(f"Требует ручного действия: {stats.manual_required}")
    say(f"Закрыто роботов/чатов: {stats.chatbot_closed}")
    say(f"Дневной лимит HH: {stats.daily_limit_reached}")
    say(f"Возвратов к списку: {stats.navigated_back}")
    say(f"Пропущено: {stats.skipped}")
    say(f"Ошибок: {stats.errors}")


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


def get_card_company(card: Locator) -> str:
    return first_text(
        card,
        [
            '[data-qa="vacancy-serp__vacancy-employer"]',
            '[data-qa="vacancy-serp__vacancy-employer-text"]',
            '[data-qa*="employer"]',
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


def get_card_text(card: Locator) -> str:
    try:
        return normalize_text(card.inner_text(timeout=1500))
    except PlaywrightError:
        return ""


def is_already_applied(card: Locator) -> bool:
    text = get_card_text(card).lower()

    phrases = [
        "вы откликнулись",
        "отклик отправлен",
        "ваш отклик отправлен",
        "резюме отправлено",
    ]

    return any(phrase in text for phrase in phrases)


def find_apply_button(card: Locator) -> Locator | None:
    selectors = [
        'button:has-text("Откликнуться")',
        'a:has-text("Откликнуться")',
        'button:has-text("Отклик без резюме")',
        'a:has-text("Отклик без резюме")',
        '[data-qa*="vacancy-serp__vacancy_response"]',
        '[data-qa*="vacancy-response"]',
        '[data-qa*="response"]',
    ]

    for selector in selectors:
        locator = card.locator(selector).first

        try:
            if locator.count() > 0 and locator.is_visible(timeout=1000):
                text = normalize_text(locator.inner_text(timeout=1000)).lower()

                if "вы откликнулись" in text:
                    continue

                return locator
        except PlaywrightError:
            continue

    return None


def get_page_text(page: Page) -> str:
    try:
        return normalize_text(page.locator("body").inner_text(timeout=1500)).lower()
    except PlaywrightError:
        return ""


def detect_daily_limit(page: Page) -> bool:
    body_text = get_page_text(page)

    limit_phrases = [
        "не более 200 откликов",
        "исчерпали лимит откликов",
        "попробуйте отправить отклик позднее",
        "попробуйте отправить отклик позже",
        "лимит откликов",
    ]

    return any(phrase in body_text for phrase in limit_phrases)


def wait_and_detect_daily_limit(page: Page, timeout_ms: int = 4000) -> bool:
    step_ms = 500
    elapsed = 0

    while elapsed <= timeout_ms:
        if detect_daily_limit(page):
            return True

        page.wait_for_timeout(step_ms)
        elapsed += step_ms

    return False


def fill_cover_letter_if_needed(page: Page, cover_letter: str) -> bool:
    if not cover_letter:
        return False

    selectors = [
        'textarea',
        '[contenteditable="true"]',
        '[role="textbox"]',
        '[data-qa*="cover-letter"] textarea',
        '[data-qa*="vacancy-response-popup-form-letter-input"]',
    ]

    for selector in selectors:
        locator = page.locator(selector).first

        try:
            if locator.count() > 0 and locator.is_visible(timeout=1000):
                if selector == "textarea":
                    current = normalize_text(locator.input_value(timeout=500))
                else:
                    current = normalize_text(locator.inner_text(timeout=500))

                if current:
                    return False

                locator.fill(cover_letter, timeout=3000)
                say("Сопроводительное письмо вставлено.")
                return True
        except PlaywrightError:
            continue

    return False


def click_country_confirmation_if_needed(page: Page) -> bool:
    body_text = get_page_text(page)

    country_phrases = [
        "другой стране",
        "другая страна",
        "в другой стране",
        "за пределами",
        "релокац",
        "понимаю",
    ]

    if not any(phrase in body_text for phrase in country_phrases):
        return False

    button_selectors = [
        'button:has-text("Понимаю")',
        'button:has-text("Подтвердить")',
        'button:has-text("Продолжить")',
        'button:has-text("Да")',
        'button:has-text("Откликнуться")',
    ]

    for selector in button_selectors:
        button = page.locator(selector).last

        try:
            if button.count() > 0 and button.is_visible(timeout=1000):
                button.click(timeout=3000)
                say("Подтверждение по стране нажато.")
                page.wait_for_timeout(1500)
                return True
        except PlaywrightError:
            continue

    return False


def click_submit_response_if_possible(page: Page) -> bool:
    selectors = [
        'button:has-text("Отправить")',
        'button:has-text("Откликнуться")',
        'button:has-text("Продолжить")',
        'button:has-text("Подтвердить")',
        'button:has-text("Сохранить и отправить")',
    ]

    for selector in selectors:
        button = page.locator(selector).last

        try:
            if button.count() > 0 and button.is_visible(timeout=1200):
                button_text = normalize_text(button.inner_text(timeout=1000)).lower()

                allowed_words = [
                    "откликнуться",
                    "отправить",
                    "продолжить",
                    "подтвердить",
                    "сохранить и отправить",
                ]

                if any(word in button_text for word in allowed_words):
                    button.click(timeout=4000)
                    page.wait_for_timeout(2000)
                    say(f"Нажата кнопка подтверждения: {button_text}")
                    return True
        except PlaywrightError:
            continue

    return False


def close_chatbot_popup_if_needed(page: Page) -> bool:
    body_text = get_page_text(page)

    robot_phrases = [
        "робот-рекрутер",
        "робот рекрутер",
        "чат",
        "ответьте в чате",
        "бот",
        "recruiter",
    ]

    if not any(phrase in body_text for phrase in robot_phrases):
        return False

    close_selectors = [
        'button[aria-label="Закрыть"]',
        'button[aria-label="Close"]',
        '[data-qa*="close"]',
        'button:has-text("Закрыть")',
        'button:has-text("Позже")',
        'button:has-text("Не сейчас")',
        'button:has-text("Отмена")',
    ]

    for selector in close_selectors:
        button = page.locator(selector).last

        try:
            if button.count() > 0 and button.is_visible(timeout=1000):
                button.click(timeout=3000)
                page.wait_for_timeout(1000)
                say("Попап с роботом/чатом закрыт.")
                return True
        except PlaywrightError:
            continue

    return False


def detect_manual_required(page: Page) -> str | None:
    checks = [
        ("тестовое задание или вопросы работодателя", 'text=/Ответьте на вопросы|Вопросы работодателя|Тестовое задание|тестовое|задание|опрос|анкета/i'),
        ("капча или проверка", 'text=/captcha|капча|Подтвердите|проверка/i'),
        ("нужно выбрать резюме", 'text=/Выберите резюме|Выбор резюме|каким резюме|выберите резюме/i'),
    ]

    for label, selector in checks:
        locator = page.locator(selector).first

        try:
            if locator.count() > 0 and locator.is_visible(timeout=1000):
                return label
        except PlaywrightError:
            continue

    return None


def detect_success(card: Locator | None = None) -> bool:
    if card is None:
        return False

    success_phrases = [
        "вы откликнулись",
        "ваш отклик отправлен работодателю",
        "отклик отправлен",
        "резюме отправлено",
    ]

    try:
        text = get_card_text(card).lower()
        return any(phrase in text for phrase in success_phrases)
    except PlaywrightError:
        return False


def return_to_list_if_needed(page: Page, list_url: str, stats: AutoApplyStats) -> None:
    if page.url == list_url:
        return

    say(f"Страница ушла сюда: {page.url}")
    say("Возвращаюсь напрямую к списку вакансий.")

    stats.navigated_back += 1

    page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)


def handle_after_apply_click(page: Page, card: Locator, cover_letter: str) -> str:
    page.wait_for_timeout(1000)

    if wait_and_detect_daily_limit(page, timeout_ms=4000):
        return "daily_limit_reached"

    for _ in range(3):
        clicked_country = click_country_confirmation_if_needed(page)

        if wait_and_detect_daily_limit(page, timeout_ms=1500):
            return "daily_limit_reached"

        filled_letter = fill_cover_letter_if_needed(page, cover_letter)

        if wait_and_detect_daily_limit(page, timeout_ms=1500):
            return "daily_limit_reached"

        if filled_letter:
            click_submit_response_if_possible(page)

            if wait_and_detect_daily_limit(page, timeout_ms=2500):
                return "daily_limit_reached"

        if clicked_country:
            page.wait_for_timeout(1200)

    if wait_and_detect_daily_limit(page, timeout_ms=1500):
        return "daily_limit_reached"

    if detect_success(card):
        return "sent"

    if close_chatbot_popup_if_needed(page):
        return "chatbot_closed"

    manual_reason = detect_manual_required(page)

    if manual_reason:
        return f"manual_required: {manual_reason}"

    if wait_and_detect_daily_limit(page, timeout_ms=1500):
        return "daily_limit_reached"

    return "unknown"


def find_next_page_url(page: Page, next_page_number: int) -> str | None:
    if not is_valid_vacancy_list_url(page.url):
        say(f"Нельзя искать пагинацию не на странице вакансий: {page.url}")
        return None

    selectors = [
        '[data-qa="pager-next"]',
        f'a[href*="/search/vacancy"]:has-text("{next_page_number}")',
        'a[href*="/search/vacancy"]:has-text("дальше")',
        'a[href*="/search/vacancy"]:has-text("следующая")',
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

            if not is_valid_vacancy_list_url(href):
                say(f"Игнорирую не-вакансийную ссылку пагинации: {href}")
                continue

            return href

        except PlaywrightError:
            continue

    return None


def process_current_page(
    page: Page,
    project_root: Path,
    blacklist: set[str],
    cover_letter: str,
    page_number: int,
    stats: AutoApplyStats,
    max_response_attempts_per_run: int,
) -> bool:
    list_url = page.url

    if not is_valid_vacancy_list_url(list_url):
        say(f"СТОП: текущая страница не похожа на список вакансий: {list_url}")
        return False

    cards = get_vacancy_cards(page)
    total = cards.count()

    say()
    say("=" * 70)
    say(f"СТРАНИЦА {page_number}")
    say(f"URL: {list_url}")
    say(f"Карточек найдено: {total}")
    say("=" * 70)

    for index in range(total):
        try:
            if stats.response_attempts >= max_response_attempts_per_run:
                say()
                say(f"Достигнут лимит попыток за запуск: {max_response_attempts_per_run}")
                say("Останавливаю автоотклики.")
                print_stats(stats)
                return False

            return_to_list_if_needed(page, list_url, stats)

            cards = get_vacancy_cards(page)
            current_total = cards.count()

            if index >= current_total:
                break

            card = cards.nth(index)

            card.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(700)

            title = get_card_title(card)
            company = get_card_company(card)
            vacancy_url = get_card_url(card)

            say()
            say(f"[{index + 1}/{total}] {title}")
            say(f"Компания: {company}")

            if is_company_blacklisted(company, blacklist):
                say("Результат: компания в blacklist. Пропускаю.")
                stats.blacklist += 1
                append_result(
                    project_root,
                    {
                        "status": "blacklist",
                        "page": page_number,
                        "title": title,
                        "company": company,
                        "url": vacancy_url,
                    },
                )
                continue

            if is_already_applied(card):
                say("Результат: уже откликались. Пропускаю.")
                stats.already_applied += 1
                append_result(
                    project_root,
                    {
                        "status": "already_applied",
                        "page": page_number,
                        "title": title,
                        "company": company,
                        "url": vacancy_url,
                    },
                )
                continue

            apply_button = find_apply_button(card)

            if apply_button is None:
                say("Результат: кнопка отклика не найдена.")
                stats.skipped += 1
                append_result(
                    project_root,
                    {
                        "status": "no_apply_button",
                        "page": page_number,
                        "title": title,
                        "company": company,
                        "url": vacancy_url,
                    },
                )
                continue

            say("Нажимаю отклик...")
            before_url = page.url

            apply_button.click(timeout=7000)
            stats.response_attempts += 1
            page.wait_for_timeout(1000)

            if wait_and_detect_daily_limit(page, timeout_ms=4000):
                stats.daily_limit_reached += 1
                append_result(
                    project_root,
                    {
                        "status": "daily_limit_reached",
                        "page": page_number,
                        "title": title,
                        "company": company,
                        "url": vacancy_url,
                        "response_attempts_in_this_run": stats.response_attempts,
                    },
                )
                say()
                say("Дневной лимит HH на отклики исчерпан.")
                say("Останавливаю автоотклики.")
                print_stats(stats)
                return False

            if page.url != before_url:
                opened_url = page.url

                say(f"Произошёл переход на другую страницу: {opened_url}")
                say("Логирую как форму/опросник и возвращаюсь напрямую к списку вакансий.")

                stats.external_form_opened += 1
                stats.navigated_back += 1

                append_result(
                    project_root,
                    {
                        "status": "external_form_opened",
                        "page": page_number,
                        "title": title,
                        "company": company,
                        "url": vacancy_url,
                        "opened_url": opened_url,
                        "response_attempts_in_this_run": stats.response_attempts,
                    },
                )

                page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                continue

            state = handle_after_apply_click(page, card, cover_letter)
            say(f"После клика: {state}")

            if state == "daily_limit_reached":
                stats.daily_limit_reached += 1
                append_result(
                    project_root,
                    {
                        "status": "daily_limit_reached",
                        "page": page_number,
                        "title": title,
                        "company": company,
                        "url": vacancy_url,
                        "response_attempts_in_this_run": stats.response_attempts,
                    },
                )
                say()
                say("Дневной лимит HH на отклики исчерпан.")
                say("Останавливаю автоотклики.")
                print_stats(stats)
                return False

            if state == "sent":
                stats.sent += 1
                append_result(
                    project_root,
                    {
                        "status": "sent",
                        "page": page_number,
                        "title": title,
                        "company": company,
                        "url": vacancy_url,
                        "response_attempts_in_this_run": stats.response_attempts,
                    },
                )
                return_to_list_if_needed(page, list_url, stats)
                continue

            if state == "chatbot_closed":
                stats.chatbot_closed += 1
                append_result(
                    project_root,
                    {
                        "status": "chatbot_closed",
                        "page": page_number,
                        "title": title,
                        "company": company,
                        "url": vacancy_url,
                        "response_attempts_in_this_run": stats.response_attempts,
                    },
                )
                return_to_list_if_needed(page, list_url, stats)
                continue

            if state.startswith("manual_required"):
                stats.manual_required += 1
                append_result(
                    project_root,
                    {
                        "status": "manual_required",
                        "detail": state,
                        "page": page_number,
                        "title": title,
                        "company": company,
                        "url": vacancy_url,
                        "response_attempts_in_this_run": stats.response_attempts,
                    },
                )
                return_to_list_if_needed(page, list_url, stats)
                continue

            if wait_and_detect_daily_limit(page, timeout_ms=1500):
                stats.daily_limit_reached += 1
                append_result(
                    project_root,
                    {
                        "status": "daily_limit_reached",
                        "page": page_number,
                        "title": title,
                        "company": company,
                        "url": vacancy_url,
                        "response_attempts_in_this_run": stats.response_attempts,
                    },
                )
                say()
                say("Дневной лимит HH на отклики исчерпан.")
                say("Останавливаю автоотклики.")
                print_stats(stats)
                return False

            stats.skipped += 1
            append_result(
                project_root,
                {
                    "status": "unknown_after_click",
                    "page": page_number,
                    "title": title,
                    "company": company,
                    "url": vacancy_url,
                    "response_attempts_in_this_run": stats.response_attempts,
                },
            )
            return_to_list_if_needed(page, list_url, stats)

        except PlaywrightError as error:
            stats.errors += 1
            logger.warning("Ошибка на вакансии %s: %s", index + 1, error)

            say(f"Ошибка на вакансии {index + 1}. Пропускаю.")
            say(str(error))

            append_result(
                project_root,
                {
                    "status": "error",
                    "page": page_number,
                    "index": index + 1,
                    "error": str(error),
                    "current_url": page.url,
                },
            )

            try:
                return_to_list_if_needed(page, list_url, stats)
            except PlaywrightError:
                pass

    print_stats(stats)
    return True


def process_pages(
    page: Page,
    project_root: Path,
    blacklist: set[str],
    cover_letter: str,
    max_pages: int,
    max_response_attempts_per_run: int,
) -> None:
    stats = AutoApplyStats()

    for page_number in range(1, max_pages + 1):
        if is_login_page(page.url):
            say("СТОП: открыта страница логина.")
            return

        if not is_valid_vacancy_list_url(page.url):
            say(f"СТОП: текущая страница не является списком вакансий: {page.url}")
            return

        should_continue = process_current_page(
            page=page,
            project_root=project_root,
            blacklist=blacklist,
            cover_letter=cover_letter,
            page_number=page_number,
            stats=stats,
            max_response_attempts_per_run=max_response_attempts_per_run,
        )

        if not should_continue:
            break

        if page_number >= max_pages:
            say("Достигнут лимит страниц.")
            break

        if not is_valid_vacancy_list_url(page.url):
            say(f"После обработки страница не является списком вакансий: {page.url}")
            say("Останавливаю, чтобы не работать по неправильной странице.")
            break

        next_url = find_next_page_url(page, page_number + 1)

        if not next_url:
            say("Следующая страница не найдена. Завершаю.")
            break

        say()
        say(f"Переход на следующую страницу: {next_url}")
        page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

    say()
    say("Автоотклики завершены.")
    print_stats(stats)


def main() -> None:
    project_root = get_project_root()
    setup_logging(project_root)

    say("HH Job Assistant")
    say("----------------")
    say("Режим: автоматические отклики по страницам")
    say("Blacklist учитывается.")
    say("Сопроводительное письмо вставляется автоматически.")
    say("Лимит HH ловится по уведомлению после клика.")
    say("Попытки отклика считаются отдельно от точно отправленных откликов.")

    try:
        config = load_config()

        collection = config.get("collection", {})
        max_pages = int(collection.get("max_pages", 3))

        max_response_attempts_per_run = int(
            get_nested(config, "auto_apply.max_response_attempts_per_run", 200)
        )

        user_data_dir = resolve_project_path(config["browser"]["user_data_dir"])
        headless = bool(config["browser"]["headless"])

        cover_letter = read_text_file(project_root / COVER_LETTER_FILE)
        blacklist = read_blacklist(project_root / BLACKLIST_FILE)

        say()
        say(f"Профиль браузера: {user_data_dir}")
        say(f"Фоновый режим: {headless}")
        say(f"Максимум страниц: {max_pages}")
        say(f"Максимум попыток отклика за запуск: {max_response_attempts_per_run}")
        say(f"Компаний в blacklist: {len(blacklist)}")
        say(f"Файл результатов: {project_root / RESULTS_FILE}")

        if not cover_letter:
            say()
            say("ВНИМАНИЕ: config/cover_letter.txt пустой.")
            say("Если появится обязательное письмо, вставлять будет нечего.")

        with BrowserSession(user_data_dir=user_data_dir, headless=headless) as (context, page):
            say()
            say("Открываю hh.ru...")
            page.goto(HH_HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)

            say()
            say(f"Текущий URL: {page.url}")
            say(f"Заголовок: {page.title()}")

            if is_login_page(page.url):
                say()
                say("СТАТУС: НЕ АВТОРИЗОВАН.")
                say("Авторизуйся в открытом браузере.")

            say()
            say("Открой страницу подходящих вакансий.")
            say("Важно: URL должен начинаться с https://hh.ru/search/vacancy")
            say("Можно открыть её в этой же вкладке или в новой вкладке.")
            say("Когда видишь список вакансий, вернись в PowerShell.")

            input("Нажми Enter, чтобы начать автоматические отклики...")

            page = wait_for_vacancy_page(context, page)

            say()
            say("Стартую.")
            say(f"Стартовый URL: {page.url}")

            process_pages(
                page=page,
                project_root=project_root,
                blacklist=blacklist,
                cover_letter=cover_letter,
                max_pages=max_pages,
                max_response_attempts_per_run=max_response_attempts_per_run,
            )

            input("Нажми Enter, чтобы закрыть браузер...")

        say("Готово.")

    except KeyboardInterrupt:
        say()
        say("Остановлено пользователем через Ctrl+C.")
        sys.exit(130)

    except (PlaywrightTimeoutError, PlaywrightError) as error:
        logger.exception("Ошибка браузера")
        say()
        say("ОШИБКА БРАУЗЕРА")
        say(str(error))
        sys.exit(1)

    except Exception as error:
        logger.exception("Ошибка приложения")
        say()
        say("ОШИБКА")
        say(type(error).__name__)
        say(str(error))
        sys.exit(1)


if __name__ == "__main__":
    main()