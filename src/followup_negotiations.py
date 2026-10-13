from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"
RESULTS_PATH = PROJECT_ROOT / "data" / "followup_negotiations_results.jsonl"

HH_NEGOTIATIONS_URL = "https://hh.ru/applicant/negotiations?filter=all&state=READ&state=UNREAD"

FOLLOWUP_TEXT = "Добрый день, ваша вакансия мне интересна, напоминаю о своём отклике!"

MIN_HOURS_AFTER_LAST_MESSAGE = 24
MAX_DAYS_AFTER_LAST_MESSAGE = 14

MAX_CARDS_PER_RUN = 300
SCROLL_STEP = 1200
SCROLL_PAUSE_SECONDS = 1.2
CHAT_OPEN_WAIT_SECONDS = 2.5
AFTER_SEND_PAUSE_SECONDS = 1.5


@dataclass
class Stats:
    checked: int = 0
    sent: int = 0
    skipped_too_fresh: int = 0
    skipped_too_old: int = 0
    skipped_already_sent: int = 0
    skipped_no_chat_button: int = 0
    skipped_chat_not_opened: int = 0
    skipped_no_time: int = 0
    skipped_recommendation_block: int = 0
    errors: int = 0


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}

    with SETTINGS_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_nested(data: dict, path: str, default=None):
    current = data

    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]

    return current


def resolve_project_path(value: str) -> Path:
    path = Path(value)

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def ensure_dirs() -> None:
    (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)


def write_result(payload: dict) -> None:
    ensure_dirs()

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }

    with RESULTS_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def first_visible(page: Page, selectors: list[str], timeout: int = 700) -> Optional[Locator]:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()

            for index in range(count):
                item = locator.nth(index)

                try:
                    if item.is_visible(timeout=timeout):
                        return item
                except Exception:
                    continue

        except Exception:
            continue

    return None


def wait_for_user_ready() -> None:
    print("")
    print("Открыл страницу HH.")
    print("")
    print("Что нужно сделать вручную:")
    print("1. Авторизоваться на HH, если нужно.")
    print("2. Открыть страницу откликов.")
    print("3. Нажать вкладку: Ожидание.")
    print("4. Убедиться, что видны карточки с кнопкой 'Перейти в чат'.")
    print("")
    input("Когда вкладка 'Ожидание' открыта — нажми Enter в терминале...")


def get_or_open_hh_page(context: BrowserContext) -> Page:
    for page in context.pages:
        if "hh.ru/applicant/negotiations" in page.url:
            page.bring_to_front()
            return page

    page = context.new_page()
    page.goto(HH_NEGOTIATIONS_URL, wait_until="domcontentloaded")
    return page


def get_visible_recommendations_top(page: Page) -> Optional[float]:
    phrases = [
        "Вам подойдут эти вакансии",
        "Подходящие вакансии",
        "Рекомендуемые вакансии",
    ]

    for phrase in phrases:
        try:
            locator = page.locator(f"text={phrase}").first

            if not locator.is_visible(timeout=500):
                continue

            box = locator.bounding_box()

            if box:
                return float(box["y"])

        except Exception:
            continue

    return None


def is_button_inside_recommendations_block(page: Page, button: Locator) -> bool:
    recommendations_top = get_visible_recommendations_top(page)

    if recommendations_top is None:
        return False

    try:
        button_box = button.bounding_box()

        if not button_box:
            return False

        return float(button_box["y"]) > recommendations_top

    except Exception:
        return False


def get_chat_buttons_on_screen(page: Page) -> list[Locator]:
    """
    Возвращает только видимые кнопки/ссылки 'Перейти в чат' на основной странице.
    Не берём кнопки из блока рекомендаций 'Вам подойдут эти вакансии'.
    """

    selectors = [
        "button:has-text('Перейти в чат')",
        "a:has-text('Перейти в чат')",
    ]

    buttons: list[Locator] = []

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()

            for index in range(count):
                button = locator.nth(index)

                try:
                    if not button.is_visible(timeout=300):
                        continue

                    box = button.bounding_box()

                    if not box:
                        continue

                    # Отсекаем кнопки вне видимой части страницы.
                    if box["y"] < 0 or box["y"] > 900:
                        continue

                    # Отсекаем блок "Вам подойдут эти вакансии".
                    if is_button_inside_recommendations_block(page, button):
                        continue

                    buttons.append(button)

                except Exception:
                    continue

        except Exception:
            continue

    return buttons


def get_card_from_chat_button(button: Locator) -> Optional[Locator]:
    """
    Поднимаемся от кнопки 'Перейти в чат' к ближайшему контейнеру карточки.
    Не берём слишком большие контейнеры страницы.
    """

    xpath_candidates = [
        "xpath=ancestor::div[contains(., 'Был онлайн') and contains(., 'Перейти в чат')][1]",
        "xpath=ancestor::div[contains(., 'Разбирает') and contains(., 'Перейти в чат')][1]",
        "xpath=ancestor::div[contains(., 'сегодня') and contains(., 'Перейти в чат')][1]",
        "xpath=ancestor::div[contains(., 'вчера') and contains(., 'Перейти в чат')][1]",
        "xpath=ancestor::div[contains(., 'Не просмотрен') and contains(., 'Перейти в чат')][1]",
        "xpath=ancestor::div[contains(., 'Перейти в чат')][1]",
    ]

    for xpath in xpath_candidates:
        try:
            card = button.locator(xpath)

            if card.count() == 0:
                continue

            candidate = card.first

            if not candidate.is_visible(timeout=500):
                continue

            text = normalize_text(candidate.inner_text(timeout=1000))

            if "Перейти в чат" not in text:
                continue

            if "Вам подойдут эти вакансии" in text:
                continue

            # Если контейнер захватил полстраницы, он нам не подходит.
            if len(text) > 1500:
                continue

            return candidate

        except Exception:
            continue

    return None


def extract_card_key(card: Locator) -> str:
    try:
        text = normalize_text(card.inner_text(timeout=1000))
    except Exception:
        return f"unknown-{time.time()}"

    text = text.replace("Не просмотрен", "")
    text = text.replace("Перейти в чат", "")
    text = text.replace("Усилить отклик", "")
    text = text.replace("Оставить отзыв", "")
    text = text.replace("Удалить", "")
    text = normalize_text(text)

    if not text:
        return f"unknown-{time.time()}"

    return text[:300]


def extract_card_title(card: Locator) -> str:
    key = extract_card_key(card)

    # Обычно первые строки — название вакансии, компания, дата.
    cleaned = key
    cleaned = re.sub(r"\bсегодня\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bвчера\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"Был онлайн.*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"Разбирает.*", "", cleaned, flags=re.IGNORECASE)
    cleaned = normalize_text(cleaned)

    return cleaned[:180] if cleaned else key[:180]


def is_chat_modal_visible(page: Page) -> bool:
    """
    Проверяем, что открылось именно окно чата.
    На текущем HH в модалке есть заголовок 'Чаты' и поле 'Сообщение'.
    """

    selectors = [
        "text=Чаты",
        "text=Вакансия",
        "textarea[placeholder*='Сообщение']",
        "input[placeholder*='Сообщение']",
        "[placeholder*='Сообщение']",
    ]

    visible = first_visible(page, selectors, timeout=1200)
    return visible is not None


def wait_for_chat_modal(page: Page) -> bool:
    deadline = time.time() + CHAT_OPEN_WAIT_SECONDS + 4

    while time.time() < deadline:
        if is_chat_modal_visible(page):
            return True

        time.sleep(0.4)

    return False


def get_visible_message_input(page: Page) -> Optional[Locator]:
    selectors = [
        "textarea[placeholder*='Сообщение']",
        "input[placeholder*='Сообщение']",
        "[placeholder*='Сообщение']",
        "textarea",
        "input[type='text']",
    ]

    return first_visible(page, selectors, timeout=1000)


def get_conversation_text(page: Page) -> str:
    """
    Важно: в модалке слева есть список чатов, а справа выбранный чат.
    Нельзя читать body целиком, иначе туда попадут времена из списка чатов слева.
    Поэтому берём контейнер вокруг поля ввода сообщения в правой части.
    """

    js = """
    () => {
        function isVisible(el) {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return (
                rect.width > 0 &&
                rect.height > 0 &&
                style.visibility !== 'hidden' &&
                style.display !== 'none'
            );
        }

        const candidates = Array.from(
            document.querySelectorAll(
                'textarea[placeholder*="Сообщение"], input[placeholder*="Сообщение"], [placeholder*="Сообщение"]'
            )
        ).filter(isVisible);

        if (!candidates.length) {
            return "";
        }

        const input = candidates[candidates.length - 1];

        let best = null;
        let node = input;

        while (node && node !== document.body) {
            const rect = node.getBoundingClientRect();
            const text = (node.innerText || node.textContent || "").trim();

            const looksLikeConversation =
                rect.width >= 350 &&
                rect.height >= 300 &&
                text.includes("Вакансия") &&
                text.includes("Сообщение");

            const isRightSide =
                rect.x > window.innerWidth * 0.35;

            if (looksLikeConversation && isRightSide) {
                best = node;
            }

            node = node.parentElement;
        }

        if (best) {
            return (best.innerText || best.textContent || "").trim();
        }

        let fallback = input.parentElement;

        for (let i = 0; i < 8 && fallback; i++) {
            const rect = fallback.getBoundingClientRect();

            if (rect.width >= 350 && rect.height >= 250) {
                return (fallback.innerText || fallback.textContent || "").trim();
            }

            fallback = fallback.parentElement;
        }

        return "";
    }
    """

    try:
        text = page.evaluate(js)
        return normalize_text(text)
    except Exception:
        return ""


def already_sent_followup(conversation_text: str) -> bool:
    return FOLLOWUP_TEXT.lower() in conversation_text.lower()


def parse_russian_month(month_text: str) -> Optional[int]:
    months = {
        "января": 1,
        "февраля": 2,
        "марта": 3,
        "апреля": 4,
        "мая": 5,
        "июня": 6,
        "июля": 7,
        "августа": 8,
        "сентября": 9,
        "октября": 10,
        "ноября": 11,
        "декабря": 12,
    }

    return months.get(month_text.lower())


def find_nearest_date_label_before_time(text: str, time_start_index: int, now: datetime) -> date:
    """
    Ищем ближайшую дату перед найденным временем:
    - Сегодня
    - Вчера
    - 14 июля
    - 03.07.2026 / 03.07
    Если ничего нет — предполагаем сегодня/вчера по времени.
    """

    prefix = text[:time_start_index].lower()

    today_pos = prefix.rfind("сегодня")
    yesterday_pos = prefix.rfind("вчера")

    if yesterday_pos > today_pos:
        return (now - timedelta(days=1)).date()

    if today_pos > yesterday_pos:
        return now.date()

    # Формат: 14 июля
    month_pattern = re.compile(
        r"\b([0-3]?\d)\s+"
        r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b",
        re.IGNORECASE,
    )

    month_matches = list(month_pattern.finditer(prefix))

    if month_matches:
        match = month_matches[-1]
        day = int(match.group(1))
        month = parse_russian_month(match.group(2))

        if month:
            candidate = date(now.year, month, day)

            # Если вдруг дата в будущем из-за перехода года — считаем прошлый год.
            if candidate > now.date():
                candidate = date(now.year - 1, month, day)

            return candidate

    # Формат: 14.07.2026 или 14.07
    numeric_pattern = re.compile(r"\b([0-3]?\d)\.([01]?\d)(?:\.(20\d{2}))?\b")
    numeric_matches = list(numeric_pattern.finditer(prefix))

    if numeric_matches:
        match = numeric_matches[-1]
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else now.year

        candidate = date(year, month, day)

        if candidate > now.date():
            candidate = date(year - 1, month, day)

        return candidate

    return guess_date_without_label(now, text, time_start_index)


def guess_date_without_label(now: datetime, text: str, time_start_index: int) -> date:
    time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text[time_start_index:])

    if not time_match:
        return now.date()

    hour = int(time_match.group(1))
    minute = int(time_match.group(2))

    candidate = datetime.combine(now.date(), datetime.min.time()).replace(hour=hour, minute=minute)

    if candidate > now + timedelta(minutes=5):
        candidate = candidate - timedelta(days=1)

    return candidate.date()


def parse_last_message_datetime(conversation_text: str, now: datetime) -> Optional[datetime]:
    """
    Берём последнее время в правом чате.
    Пример:
    Вчера ... 22:05
    Сегодня ... 03:38
    """

    text = normalize_text(conversation_text)

    if not text:
        return None

    time_matches = list(re.finditer(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text))

    if not time_matches:
        return None

    last_time_match = time_matches[-1]

    hour = int(last_time_match.group(1))
    minute = int(last_time_match.group(2))

    msg_date = find_nearest_date_label_before_time(text, last_time_match.start(), now)

    return datetime.combine(msg_date, datetime.min.time()).replace(hour=hour, minute=minute)


def should_send_followup(last_message_at: datetime, now: datetime) -> tuple[bool, str]:
    age = now - last_message_at

    if age < timedelta(hours=MIN_HOURS_AFTER_LAST_MESSAGE):
        return False, "too_fresh"

    if age > timedelta(days=MAX_DAYS_AFTER_LAST_MESSAGE):
        return False, "too_old"

    return True, "ok"


def find_send_button_near_input(page: Page) -> Optional[Locator]:
    selectors = [
        "button[aria-label*='Отправить']",
        "button[aria-label*='Send']",
        "button:has-text('Отправить')",
        "button[type='submit']",
    ]

    visible = first_visible(page, selectors, timeout=700)

    if visible is not None:
        return visible

    # На HH кнопка отправки часто круглая справа от поля сообщения.
    try:
        message_input = get_visible_message_input(page)

        if message_input is None:
            return None

        input_box = message_input.bounding_box()

        if not input_box:
            return None

        buttons = page.locator("button")
        count = buttons.count()

        candidates: list[Locator] = []

        input_center_y = input_box["y"] + input_box["height"] / 2

        for index in range(count):
            button = buttons.nth(index)

            try:
                if not button.is_visible(timeout=300):
                    continue

                box = button.bounding_box()

                if not box:
                    continue

                button_center_y = box["y"] + box["height"] / 2

                is_right_of_input = box["x"] >= input_box["x"] + input_box["width"] - 80
                is_same_row = abs(button_center_y - input_center_y) < 70
                is_reasonable_size = 20 <= box["width"] <= 80 and 20 <= box["height"] <= 80

                if is_right_of_input and is_same_row and is_reasonable_size:
                    candidates.append(button)

            except Exception:
                continue

        if candidates:
            return candidates[-1]

    except Exception:
        pass

    return None


def send_followup_message(page: Page) -> bool:
    message_input = get_visible_message_input(page)

    if message_input is None:
        return False

    try:
        message_input.click(timeout=3000)
        message_input.fill(FOLLOWUP_TEXT)
    except Exception:
        try:
            message_input.click(timeout=3000)
            page.keyboard.insert_text(FOLLOWUP_TEXT)
        except Exception:
            return False

    time.sleep(0.5)

    send_button = find_send_button_near_input(page)

    if send_button is not None:
        try:
            send_button.click(timeout=3000)
            time.sleep(AFTER_SEND_PAUSE_SECONDS)
            return True
        except Exception:
            pass

    try:
        page.keyboard.press("Enter")
        time.sleep(AFTER_SEND_PAUSE_SECONDS)
        return True
    except Exception:
        return False


def close_chat_modal(page: Page) -> None:
    """
    Закрываем чат после обработки.
    Escape на HH не всегда закрывает окно, поэтому ищем крестик справа сверху.
    """

    try:
        page.keyboard.press("Escape")
        time.sleep(0.6)

        if not is_chat_modal_visible(page):
            return

    except Exception:
        pass

    try:
        buttons = page.locator("button")
        count = buttons.count()

        candidates: list[Locator] = []

        for index in range(count):
            button = buttons.nth(index)

            try:
                if not button.is_visible(timeout=300):
                    continue

                box = button.bounding_box()

                if not box:
                    continue

                # Крестик закрытия чата обычно в правом верхнем углу модального окна.
                is_top_right = box["x"] > 900 and box["y"] < 250
                is_small = box["width"] <= 80 and box["height"] <= 80

                if is_top_right and is_small:
                    text = normalize_text(button.inner_text(timeout=300))

                    # Кнопка может быть без текста, с иконкой.
                    if text in ("", "×", "Закрыть") or len(text) <= 15:
                        candidates.append(button)

            except Exception:
                continue

        if candidates:
            candidates[-1].click(timeout=2000)
            time.sleep(0.8)
            return

    except Exception:
        pass

    try:
        page.keyboard.press("Escape")
        time.sleep(0.8)
    except Exception:
        pass


def process_open_chat(page: Page, title: str, stats: Stats) -> None:
    now = datetime.now()

    conversation_text = get_conversation_text(page)

    if not conversation_text:
        print("[SKIP] Не смог прочитать текст чата.")
        stats.skipped_no_time += 1
        write_result(
            {
                "status": "skipped_no_conversation_text",
                "title": title,
            }
        )
        return

    if already_sent_followup(conversation_text):
        print(f"[SKIP] Уже отправляли напоминание: {title}")
        stats.skipped_already_sent += 1
        write_result(
            {
                "status": "skipped_already_sent",
                "title": title,
            }
        )
        return

    last_message_at = parse_last_message_datetime(conversation_text, now)

    if last_message_at is None:
        print(f"[SKIP] Не смог определить время последнего сообщения: {title}")
        stats.skipped_no_time += 1
        write_result(
            {
                "status": "skipped_no_time",
                "title": title,
                "conversation_text_preview": conversation_text[:700],
            }
        )
        return

    allowed, reason = should_send_followup(last_message_at, now)

    if not allowed:
        if reason == "too_fresh":
            print(f"[SKIP] Меньше 24 часов: {title} / last={last_message_at}")
            stats.skipped_too_fresh += 1
        elif reason == "too_old":
            print(f"[SKIP] Старше 14 дней: {title} / last={last_message_at}")
            stats.skipped_too_old += 1
        else:
            print(f"[SKIP] {reason}: {title} / last={last_message_at}")

        write_result(
            {
                "status": f"skipped_{reason}",
                "title": title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
            }
        )
        return

    print(f"[SEND] Отправляю напоминание: {title} / last={last_message_at}")

    sent = send_followup_message(page)

    if sent:
        stats.sent += 1
        write_result(
            {
                "status": "sent",
                "title": title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
                "message": FOLLOWUP_TEXT,
            }
        )
    else:
        print(f"[ERROR] Не удалось отправить сообщение: {title}")
        stats.errors += 1
        write_result(
            {
                "status": "send_error",
                "title": title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
            }
        )


def process_current_visible_cards(page: Page, processed_keys: set[str], stats: Stats) -> int:
    buttons = get_chat_buttons_on_screen(page)

    if not buttons:
        print("На текущем экране не найдено кнопок 'Перейти в чат'.")
        return 0

    print(f"Кнопок 'Перейти в чат' на экране: {len(buttons)}")

    processed_on_this_screen = 0

    for button in buttons:
        if stats.checked >= MAX_CARDS_PER_RUN:
            break

        try:
            if is_button_inside_recommendations_block(page, button):
                stats.skipped_recommendation_block += 1
                continue

            card = get_card_from_chat_button(button)

            if card is None:
                stats.skipped_no_chat_button += 1
                continue

            card_key = extract_card_key(card)

            if card_key in processed_keys:
                continue

            processed_keys.add(card_key)

            title = extract_card_title(card)

            stats.checked += 1
            processed_on_this_screen += 1

            print("")
            print(f"[{stats.checked}] Проверяю: {title}")

            try:
                button.scroll_into_view_if_needed(timeout=3000)
                time.sleep(0.3)
                button.click(timeout=5000)
            except Exception as error:
                print(f"[ERROR] Не смог открыть чат: {error}")
                stats.errors += 1
                write_result(
                    {
                        "status": "open_chat_error",
                        "title": title,
                        "error": str(error),
                    }
                )
                continue

            if not wait_for_chat_modal(page):
                print("[SKIP] Модальное окно чата не открылось.")
                stats.skipped_chat_not_opened += 1
                write_result(
                    {
                        "status": "skipped_chat_not_opened",
                        "title": title,
                    }
                )
                continue

            try:
                process_open_chat(page, title, stats)
            except Exception as error:
                print(f"[ERROR] Ошибка обработки чата: {error}")
                stats.errors += 1
                write_result(
                    {
                        "status": "chat_processing_error",
                        "title": title,
                        "error": str(error),
                    }
                )
            finally:
                close_chat_modal(page)
                time.sleep(0.8)

        except Exception as error:
            print(f"[ERROR] Ошибка обработки карточки: {error}")
            stats.errors += 1
            write_result(
                {
                    "status": "card_processing_error",
                    "error": str(error),
                }
            )

    return processed_on_this_screen


def is_near_bottom_of_first_page(page: Page) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                    const scrollTop = window.scrollY || document.documentElement.scrollTop;
                    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
                    const documentHeight = document.documentElement.scrollHeight || document.body.scrollHeight;

                    return scrollTop + viewportHeight >= documentHeight - 250;
                }
                """
            )
        )
    except Exception:
        return False


def is_recommendations_visible_in_viewport(page: Page) -> bool:
    top = get_visible_recommendations_top(page)

    if top is None:
        return False

    return 0 <= top <= 950


def process_first_page_only(page: Page, stats: Stats) -> None:
    processed_keys: set[str] = set()
    rounds_without_new_cards = 0

    print("")
    print("Начинаю обход только текущей страницы.")
    print("Пагинацию пока не трогаю.")
    print("Блок 'Вам подойдут эти вакансии' пропускаю.")
    print("")

    while stats.checked < MAX_CARDS_PER_RUN:
        if is_recommendations_visible_in_viewport(page):
            print("")
            print("Дошёл до блока 'Вам подойдут эти вакансии'. Дальше не обрабатываю.")
            break

        processed_count = process_current_visible_cards(page, processed_keys, stats)

        if processed_count == 0:
            rounds_without_new_cards += 1
        else:
            rounds_without_new_cards = 0

        if rounds_without_new_cards >= 4:
            print("")
            print("Новых карточек больше не нахожу. Останавливаюсь на текущей странице.")
            break

        if is_near_bottom_of_first_page(page):
            print("")
            print("Дошёл до низа текущей страницы. Пагинацию не трогаю.")
            break

        print("")
        print("Скроллю ниже...")
        page.mouse.wheel(0, SCROLL_STEP)
        time.sleep(SCROLL_PAUSE_SECONDS)


def print_stats(stats: Stats) -> None:
    print("")
    print("Готово.")
    print("")
    print(f"Проверено карточек: {stats.checked}")
    print(f"Отправлено напоминаний: {stats.sent}")
    print(f"Пропущено, меньше 24 часов: {stats.skipped_too_fresh}")
    print(f"Пропущено, старше 14 дней: {stats.skipped_too_old}")
    print(f"Пропущено, уже отправляли: {stats.skipped_already_sent}")
    print(f"Пропущено, нет кнопки чата: {stats.skipped_no_chat_button}")
    print(f"Пропущено, чат не открылся: {stats.skipped_chat_not_opened}")
    print(f"Пропущено, не найдено время: {stats.skipped_no_time}")
    print(f"Пропущено, блок рекомендаций: {stats.skipped_recommendation_block}")
    print(f"Ошибок: {stats.errors}")
    print("")
    print(f"Лог: {RESULTS_PATH}")


def main() -> None:
    ensure_dirs()

    settings = load_settings()

    user_data_dir = get_nested(settings, "browser.user_data_dir", "data/browser-profile")
    headless = bool(get_nested(settings, "browser.headless", False))

    browser_profile_path = resolve_project_path(user_data_dir)

    stats = Stats()

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile_path),
            headless=headless,
            viewport={"width": 1400, "height": 900},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )

        page = get_or_open_hh_page(context)
        page.bring_to_front()

        try:
            page.goto(HH_NEGOTIATIONS_URL, wait_until="domcontentloaded")
        except Exception:
            pass

        wait_for_user_ready()

        process_first_page_only(page, stats)

        print_stats(stats)

        input("Нажми Enter, чтобы закрыть браузер...")
        context.close()


if __name__ == "__main__":
    main()