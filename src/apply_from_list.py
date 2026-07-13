from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.apply_log import append_apply_log
from app.blacklist import add_company_to_blacklist, is_company_blacklisted, load_company_blacklist
from app.browser import BrowserSession
from app.config import get_nested, get_project_root, load_config, resolve_project_path
from app.cover_letter import load_cover_letter
from app.logging_setup import setup_logging
from app.textio import ask, normalize_text, say


logger = logging.getLogger(__name__)
HH_HOME_URL = "https://hh.ru"


@dataclass
class ApplyStats:
    sent: int = 0
    already_applied: int = 0
    skipped: int = 0
    blacklisted: int = 0
    manual: int = 0
    errors: int = 0


def is_login_page(url: str) -> bool:
    return "/account/login" in urlparse(url).path


def print_stats(stats: ApplyStats) -> None:
    say()
    say("Текущая статистика")
    say("------------------")
    say(f"Отправлено: {stats.sent}")
    say(f"Уже были отклики: {stats.already_applied}")
    say(f"Пропущено вручную: {stats.skipped}")
    say(f"Компании из blacklist: {stats.blacklisted}")
    say(f"Требует ручного действия: {stats.manual}")
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
            say(f"Карточки найдены по селектору: {selector}")
            return cards
    return page.locator('[data-qa="__nothing_found__"]')


def get_card_title(card: Locator) -> str:
    for selector in ['[data-qa="serp-item__title"]', 'a[href*="/vacancy/"]']:
        locator = card.locator(selector).first
        try:
            if locator.count() > 0:
                text = normalize_text(locator.inner_text(timeout=1500))
                if text:
                    return text
        except PlaywrightError:
            continue
    return "Название не найдено"


def get_card_company(card: Locator) -> str:
    for selector in ['[data-qa="vacancy-serp__vacancy-employer"]', '[data-qa="vacancy-serp__vacancy-employer-text"]', '[data-qa*="employer"]']:
        locator = card.locator(selector).first
        try:
            if locator.count() > 0:
                text = normalize_text(locator.inner_text(timeout=1500))
                if text:
                    return text
        except PlaywrightError:
            continue
    return "Компания не найдена"


def get_card_url(card: Locator) -> str:
    locator = card.locator('a[href*="/vacancy/"]').first
    try:
        if locator.count() > 0:
            href = locator.get_attribute("href") or ""
            if href.startswith("/"):
                return f"https://hh.ru{href}".split("?")[0]
            return href.split("?")[0]
    except PlaywrightError:
        pass
    return ""


def card_text(card: Locator) -> str:
    try:
        return normalize_text(card.inner_text(timeout=2000))
    except PlaywrightError:
        return ""


def is_already_applied(card: Locator) -> bool:
    text = card_text(card).lower()
    return any(phrase in text for phrase in ["вы откликнулись", "отклик отправлен", "ваш отклик отправлен", "резюме отправлено"])


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


def visible(locator: Locator, timeout: int = 700) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible(timeout=timeout)
    except PlaywrightError:
        return False


def fill_cover_letter_if_possible(page: Page, cover_letter: str) -> bool:
    if not cover_letter:
        return False

    candidates = [
        'textarea',
        '[contenteditable="true"]',
        '[role="textbox"]',
    ]
    for selector in candidates:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible(timeout=1000):
                current_text = normalize_text(locator.inner_text(timeout=500)) if selector != "textarea" else normalize_text(locator.input_value(timeout=500))
                if current_text:
                    return False
                say("Найдено поле сопроводительного письма. Вставляю текст из config/cover_letter.txt")
                if selector == "textarea":
                    locator.fill(cover_letter, timeout=3000)
                else:
                    locator.click(timeout=2000)
                    locator.fill(cover_letter, timeout=3000)
                return True
        except PlaywrightError:
            continue
    return False


def find_final_submit_button(page: Page) -> Locator | None:
    selectors = [
        'button:has-text("Откликнуться")',
        'button:has-text("Отправить")',
        'button:has-text("Продолжить")',
        'button:has-text("Сохранить и отправить")',
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        if visible(locator, timeout=700):
            return locator
    return None


def detect_after_click_state(page: Page, card: Locator, cover_letter: str, auto_insert_letter: bool, auto_submit_after_letter: bool) -> str:
    page.wait_for_timeout(2000)

    if is_already_applied(card):
        return "отклик отправлен"

    manual_checks = [
        ("есть вопросы работодателя или тестовое", 'text=/Ответьте на вопросы|Вопросы работодателя|Тестовое задание|тестовое|задание/i'),
        ("нужно выбрать резюме", 'text=/Выберите резюме|Выбор резюме|каким резюме|выберите резюме/i'),
        ("капча или проверка", 'text=/captcha|капча|Подтвердите|проверка/i'),
    ]
    for state_name, selector in manual_checks:
        if visible(page.locator(selector), timeout=900):
            return state_name

    inserted = False
    if auto_insert_letter:
        inserted = fill_cover_letter_if_possible(page, cover_letter)
        if inserted:
            if auto_submit_after_letter:
                submit = find_final_submit_button(page)
                if submit is not None:
                    say("Автоотправка включена. Нажимаю финальную кнопку отправки.")
                    submit.click(timeout=5000)
                    page.wait_for_timeout(2000)
                    if is_already_applied(card):
                        return "отклик отправлен"
            return "сопроводительное письмо вставлено, нужно проверить/отправить вручную"

    final_submit = find_final_submit_button(page)
    if final_submit is not None:
        return "нужно финальное подтверждение"

    body_text = ""
    try:
        body_text = normalize_text(page.locator("body").inner_text(timeout=1000)).lower()
    except PlaywrightError:
        pass
    if any(x in body_text for x in ["ваш отклик отправлен", "отклик отправлен", "вы откликнулись"]):
        return "отклик отправлен"

    return "состояние не распознано"


def process_visible_cards(
    page: Page,
    project_root: Path,
    blacklist_path: Path,
    cover_letter: str,
    auto_insert_letter: bool,
    auto_submit_after_letter: bool,
) -> None:
    stats = ApplyStats()
    blacklist = load_company_blacklist(blacklist_path)
    apply_log_path = project_root / "data" / "apply_log.jsonl"

    cards = get_vacancy_cards(page)
    total = cards.count()
    say()
    say(f"Найдено карточек на текущей странице: {total}")
    if total == 0:
        say("Карточки не найдены.")
        return

    for index in range(total):
        card = cards.nth(index)
        try:
            if page.is_closed():
                say("Страница браузера закрыта.")
                return

            card.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(700)

            title = get_card_title(card)
            company = get_card_company(card)
            vacancy_url = get_card_url(card)

            say()
            say("=" * 70)
            say(f"Вакансия {index + 1}/{total}")
            say(f"Название: {title}")
            say(f"Компания: {company}")
            say(f"URL: {vacancy_url or '-'}")

            if is_company_blacklisted(company, blacklist):
                say("Компания в blacklist. Пропускаю.")
                stats.blacklisted += 1
                print_stats(stats)
                continue

            if is_already_applied(card):
                say("Статус: уже откликались. Пропускаю.")
                stats.already_applied += 1
                append_apply_log(apply_log_path, {"status": "already_applied", "title": title, "company": company, "url": vacancy_url})
                print_stats(stats)
                continue

            apply_button = find_apply_button(card)
            if apply_button is None:
                say("Кнопка отклика не найдена. Пропускаю.")
                stats.skipped += 1
                print_stats(stats)
                continue

            say("Кнопка отклика найдена.")
            answer = ask("Действие: [y] отклик / [n] пропустить / [b] компания в blacklist / [q] выйти: ")
            if answer == "q":
                say("Остановлено пользователем.")
                print_stats(stats)
                return
            if answer == "b":
                add_company_to_blacklist(blacklist_path, company)
                blacklist.add(company.casefold())
                say(f"Компания добавлена в blacklist: {company}")
                stats.blacklisted += 1
                print_stats(stats)
                continue
            if answer != "y":
                say("Пропускаю.")
                stats.skipped += 1
                print_stats(stats)
                continue

            say("Нажимаю кнопку отклика...")
            apply_button.click(timeout=7000)
            state = detect_after_click_state(page, card, cover_letter, auto_insert_letter, auto_submit_after_letter)
            say(f"После клика: {state}")

            if state == "отклик отправлен":
                stats.sent += 1
                append_apply_log(apply_log_path, {"status": "sent", "title": title, "company": company, "url": vacancy_url})
                say("Результат: отклик успешно отправлен.")
            else:
                stats.manual += 1
                append_apply_log(apply_log_path, {"status": "manual", "state": state, "title": title, "company": company, "url": vacancy_url})
                say("Результат: требуется ручная проверка в браузере.")
                say("Если появились вопросы, тест, выбор резюме или поле письма — обработай вручную.")

            print_stats(stats)
            next_action = ask("Продолжить к следующей вакансии? [Enter/y] да / [q] выйти: ")
            if next_action == "q":
                say("Остановлено пользователем.")
                print_stats(stats)
                return

        except PlaywrightError as error:
            logger.warning("Ошибка при обработке карточки %s: %s", index + 1, error)
            stats.errors += 1
            say()
            say(f"Ошибка на карточке {index + 1}. Пропускаю.")
            say(str(error))
            print_stats(stats)

    say()
    say("Обработка текущей страницы завершена.")
    print_stats(stats)


def main() -> None:
    project_root = get_project_root()
    setup_logging(project_root)

    say("HH Job Assistant")
    say("----------------")
    say("Режим: отклики из списка вакансий")
    say("Вакансии НЕ открываются отдельно.")

    try:
        config = load_config()
        user_data_dir = resolve_project_path(config["browser"]["user_data_dir"])
        headless = bool(config["browser"]["headless"])
        auto_insert_letter = bool(get_nested(config, "apply.auto_insert_cover_letter", True))
        auto_submit_after_letter = bool(get_nested(config, "apply.auto_submit_after_cover_letter", False))
        cover_letter = load_cover_letter(project_root / "config" / "cover_letter.txt")
        blacklist_path = project_root / "config" / "blacklist_companies.txt"

        say()
        say(f"Профиль браузера: {user_data_dir}")
        say(f"Фоновый режим: {headless}")
        say(f"Автовставка письма: {auto_insert_letter}")
        say(f"Автоотправка после письма: {auto_submit_after_letter}")

        with BrowserSession(user_data_dir=user_data_dir, headless=headless) as (_, page):
            say()
            say("Открываю hh.ru...")
            page.goto(HH_HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            say(f"Текущий URL: {page.url}")
            say(f"Заголовок: {page.title()}")
            if is_login_page(page.url):
                say("СТАТУС: НЕ АВТОРИЗОВАН. Авторизуйся вручную в открытом браузере.")

            say()
            say("Открой страницу подходящих вакансий.")
            say("Когда видишь список вакансий и кнопки 'Откликнуться', вернись сюда.")
            input("Нажми Enter, чтобы начать обработку карточек...")

            say()
            say("Команда получена. Начинаю обработку текущей страницы.")
            say(f"Стартовый URL: {page.url}")
            process_visible_cards(
                page=page,
                project_root=project_root,
                blacklist_path=blacklist_path,
                cover_letter=cover_letter,
                auto_insert_letter=auto_insert_letter,
                auto_submit_after_letter=auto_submit_after_letter,
            )
            input("\nНажми Enter, чтобы закрыть браузер...")
        say("Готово.")

    except KeyboardInterrupt:
        say("\nОстановлено пользователем через Ctrl+C.")
        sys.exit(130)
    except (PlaywrightTimeoutError, PlaywrightError) as error:
        logger.exception("Ошибка браузера")
        say(f"\nОШИБКА БРАУЗЕРА\n{error}")
        sys.exit(1)
    except Exception as error:
        logger.exception("Ошибка приложения")
        say(f"\nОШИБКА\n{type(error).__name__}\n{error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
