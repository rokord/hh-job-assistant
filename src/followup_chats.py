from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"
RESULTS_PATH = PROJECT_ROOT / "data" / "followup_chats_results.jsonl"

HH_CHAT_URL = "https://hh.ru/chat"

FOLLOWUP_TEXT = "Добрый день, ваша вакансия мне интересна, напоминаю о своём отклике!"

MIN_HOURS_AFTER_LAST_MESSAGE = 24
MAX_DAYS_AFTER_LAST_MESSAGE = 14

DELAY_BETWEEN_CHATS_SECONDS = 1.0
CHAT_LOAD_WAIT_SECONDS = 1.2

SCROLL_UP_STEP = 620
MAX_PROCESSED_CHATS_SAFETY = 5000
MAX_NO_NEW_BATCHES = 8


@dataclass
class Stats:
    checked: int = 0
    sent: int = 0
    stopped_too_fresh: int = 0
    skipped_refusal: int = 0
    skipped_no_input: int = 0
    skipped_disabled_chat: int = 0
    skipped_too_old: int = 0
    skipped_already_sent: int = 0
    skipped_no_time: int = 0
    skipped_duplicate: int = 0
    skipped_not_visible: int = 0
    empty_batches: int = 0
    errors: int = 0


@dataclass
class ChatItem:
    chat_id: str
    title: str
    text: str
    href: str
    is_refusal: bool
    y: float


@dataclass
class ChatState:
    conversation_text: str
    last_time_text: Optional[str]
    last_date_label: Optional[str]
    last_author: Optional[str]
    has_input: bool
    is_disabled: bool
    is_refusal: bool


def ensure_dirs() -> None:
    (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)


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


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def write_result(payload: dict) -> None:
    ensure_dirs()

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }

    with RESULTS_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def wait_for_user_ready() -> None:
    print("")
    print("Открыл HH Chat.")
    print("")
    print("Что нужно сделать вручную:")
    print("1. Авторизоваться на HH, если нужно.")
    print("2. Открыть https://hh.ru/chat.")
    print("3. Слева вручную проскроллить список чатов вниз до нужной глубины.")
    print("4. Лучше доскроллить до чатов старше 14-15 дней.")
    print("5. Потом нажать Enter в терминале.")
    print("")
    input("Когда список чатов слева проскроллен вниз — нажми Enter...")


def get_or_open_chat_page(context: BrowserContext) -> Page:
    for page in context.pages:
        if "hh.ru/chat" in page.url:
            page.bring_to_front()
            return page

    page = context.new_page()
    page.goto(HH_CHAT_URL, wait_until="domcontentloaded")
    return page


def get_chat_list_scroll_top(page: Page) -> Optional[int]:
    js = """
    () => {
        function isVisible(el) {
            if (!el) return false;

            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);

            return (
                rect.width > 0 &&
                rect.height > 0 &&
                style.visibility !== "hidden" &&
                style.display !== "none"
            );
        }

        const candidates = Array.from(document.querySelectorAll("*"))
            .filter((el) => {
                if (!isVisible(el)) return false;

                const rect = el.getBoundingClientRect();
                const text = (el.innerText || el.textContent || "");
                const linksCount = el.querySelectorAll('a[href*="/chat/"]').length;

                const isLeftColumn =
                    rect.x < 470 &&
                    rect.width >= 220 &&
                    rect.width <= 470 &&
                    rect.height >= 300;

                const isScrollable = el.scrollHeight > el.clientHeight + 60;
                const looksLikeChatList =
                    linksCount > 0 ||
                    text.includes("Только непрочитанные") ||
                    text.includes("Отклик на вакансию") ||
                    text.includes("Отказ");

                return isLeftColumn && isScrollable && looksLikeChatList;
            })
            .sort((a, b) => {
                const aLinks = a.querySelectorAll('a[href*="/chat/"]').length;
                const bLinks = b.querySelectorAll('a[href*="/chat/"]').length;

                if (bLinks !== aLinks) return bLinks - aLinks;

                const aScroll = a.scrollHeight - a.clientHeight;
                const bScroll = b.scrollHeight - b.clientHeight;

                return bScroll - aScroll;
            });

        const container = candidates.length ? candidates[0] : null;

        if (!container) return null;

        return Math.round(container.scrollTop);
    }
    """

    try:
        value = page.evaluate(js)

        if value is None:
            return None

        return int(value)
    except Exception:
        return None


def set_chat_list_scroll_top(page: Page, scroll_top: int) -> bool:
    js = """
    (targetScrollTop) => {
        function isVisible(el) {
            if (!el) return false;

            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);

            return (
                rect.width > 0 &&
                rect.height > 0 &&
                style.visibility !== "hidden" &&
                style.display !== "none"
            );
        }

        const candidates = Array.from(document.querySelectorAll("*"))
            .filter((el) => {
                if (!isVisible(el)) return false;

                const rect = el.getBoundingClientRect();
                const text = (el.innerText || el.textContent || "");
                const linksCount = el.querySelectorAll('a[href*="/chat/"]').length;

                const isLeftColumn =
                    rect.x < 470 &&
                    rect.width >= 220 &&
                    rect.width <= 470 &&
                    rect.height >= 300;

                const isScrollable = el.scrollHeight > el.clientHeight + 60;
                const looksLikeChatList =
                    linksCount > 0 ||
                    text.includes("Только непрочитанные") ||
                    text.includes("Отклик на вакансию") ||
                    text.includes("Отказ");

                return isLeftColumn && isScrollable && looksLikeChatList;
            })
            .sort((a, b) => {
                const aLinks = a.querySelectorAll('a[href*="/chat/"]').length;
                const bLinks = b.querySelectorAll('a[href*="/chat/"]').length;

                if (bLinks !== aLinks) return bLinks - aLinks;

                const aScroll = a.scrollHeight - a.clientHeight;
                const bScroll = b.scrollHeight - b.clientHeight;

                return bScroll - aScroll;
            });

        const container = candidates.length ? candidates[0] : null;

        if (!container) return false;

        container.scrollTop = targetScrollTop;
        return true;
    }
    """

    try:
        return bool(page.evaluate(js, scroll_top))
    except Exception:
        return False


def scroll_chat_list_up(page: Page) -> bool:
    js = """
    (scrollStep) => {
        function isVisible(el) {
            if (!el) return false;

            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);

            return (
                rect.width > 0 &&
                rect.height > 0 &&
                style.visibility !== "hidden" &&
                style.display !== "none"
            );
        }

        const candidates = Array.from(document.querySelectorAll("*"))
            .filter((el) => {
                if (!isVisible(el)) return false;

                const rect = el.getBoundingClientRect();
                const text = (el.innerText || el.textContent || "");
                const linksCount = el.querySelectorAll('a[href*="/chat/"]').length;

                const isLeftColumn =
                    rect.x < 470 &&
                    rect.width >= 220 &&
                    rect.width <= 470 &&
                    rect.height >= 300;

                const isScrollable = el.scrollHeight > el.clientHeight + 60;
                const looksLikeChatList =
                    linksCount > 0 ||
                    text.includes("Только непрочитанные") ||
                    text.includes("Отклик на вакансию") ||
                    text.includes("Отказ");

                return isLeftColumn && isScrollable && looksLikeChatList;
            })
            .sort((a, b) => {
                const aLinks = a.querySelectorAll('a[href*="/chat/"]').length;
                const bLinks = b.querySelectorAll('a[href*="/chat/"]').length;

                if (bLinks !== aLinks) return bLinks - aLinks;

                const aScroll = a.scrollHeight - a.clientHeight;
                const bScroll = b.scrollHeight - b.clientHeight;

                return bScroll - aScroll;
            });

        const container = candidates.length ? candidates[0] : null;

        if (!container) return false;

        const before = container.scrollTop;
        container.scrollTop = Math.max(0, container.scrollTop - scrollStep);

        return container.scrollTop !== before;
    }
    """

    try:
        changed = bool(page.evaluate(js, SCROLL_UP_STEP))

        if changed:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        page.mouse.move(260, 500)
        page.mouse.wheel(0, -SCROLL_UP_STEP)
        time.sleep(1.0)
        return True
    except Exception:
        return False


def collect_visible_chat_items(page: Page) -> list[ChatItem]:
    """
    Собирает только видимые сейчас чаты в левой колонке.
    Порядок: сверху вниз, то есть новые -> старые.
    Обработка будет в обратном порядке: снизу вверх, старые -> новые.
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
                style.visibility !== "hidden" &&
                style.display !== "none"
            );
        }

        const anchors = Array.from(document.querySelectorAll('a[href*="/chat/"]'));
        const items = [];

        for (const anchor of anchors) {
            if (!isVisible(anchor)) continue;

            const href = anchor.href || "";
            const match = href.match(/\\/chat\\/(\\d+)/);

            if (!match) continue;

            const rect = anchor.getBoundingClientRect();

            if (rect.x > 470) continue;
            if (rect.y < 80 || rect.y > window.innerHeight - 20) continue;

            const text = (anchor.innerText || anchor.textContent || "").trim();

            if (!text) continue;

            const lines = text
                .split("\\n")
                .map((line) => line.trim())
                .filter(Boolean);

            const title = lines.length ? lines[0] : text.slice(0, 120);

            items.push({
                chat_id: match[1],
                title,
                text,
                href,
                y: rect.y,
                is_refusal: /(^|\\s)отказ(\\s|$)/i.test(text),
            });
        }

        items.sort((a, b) => a.y - b.y);

        return items;
    }
    """

    try:
        raw_items = page.evaluate(js)
    except Exception:
        return []

    result: list[ChatItem] = []
    seen: set[str] = set()

    for item in raw_items:
        chat_id = str(item.get("chat_id", "")).strip()

        if not chat_id or chat_id in seen:
            continue

        seen.add(chat_id)

        result.append(
            ChatItem(
                chat_id=chat_id,
                title=normalize_text(str(item.get("title", "")))[:160],
                text=normalize_text(str(item.get("text", ""))),
                href=str(item.get("href", "")),
                is_refusal=bool(item.get("is_refusal", False)),
                y=float(item.get("y", 0)),
            )
        )

    return result


def click_visible_chat_by_id(page: Page, chat_id: str) -> bool:
    """
    ВАЖНО:
    Не используем page.goto("https://hh.ru/chat/<id>"), потому что HH сбрасывает позицию списка.
    Кликаем только по видимому элементу слева.
    """

    js = """
    (chatId) => {
        function isVisible(el) {
            if (!el) return false;

            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);

            return (
                rect.width > 0 &&
                rect.height > 0 &&
                style.visibility !== "hidden" &&
                style.display !== "none"
            );
        }

        const anchors = Array.from(document.querySelectorAll('a[href*="/chat/"]'));

        const candidates = anchors
            .filter((anchor) => {
                if (!isVisible(anchor)) return false;

                const href = anchor.href || "";
                const rect = anchor.getBoundingClientRect();

                if (!href.includes(`/chat/${chatId}`)) return false;
                if (rect.x > 470) return false;
                if (rect.y < 80 || rect.y > window.innerHeight - 20) return false;

                return true;
            })
            .sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();

                return ar.y - br.y;
            });

        if (!candidates.length) {
            return false;
        }

        const target = candidates[0];

        target.scrollIntoView({ block: "center", inline: "nearest" });
        target.click();

        return true;
    }
    """

    try:
        return bool(page.evaluate(js, chat_id))
    except Exception:
        return False


def first_visible(page: Page, selectors: list[str], timeout: int = 500) -> Optional[Locator]:
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


def get_visible_message_input(page: Page) -> Optional[Locator]:
    selectors = [
        "textarea[placeholder*='Сообщение']",
        "input[placeholder*='Сообщение']",
        "[placeholder*='Сообщение']",
        "textarea",
        "input[type='text']",
    ]

    locator = first_visible(page, selectors, timeout=700)

    if locator is None:
        return None

    try:
        if not locator.is_enabled(timeout=700):
            return None
    except Exception:
        return None

    return locator


def get_chat_state(page: Page) -> ChatState:
    """
    Читает правую область выбранного чата.
    Автор последнего сообщения пишем в лог, но НЕ используем как фильтр.
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
                style.visibility !== "hidden" &&
                style.display !== "none"
            );
        }

        function textOf(el) {
            return ((el && (el.innerText || el.textContent)) || "").trim();
        }

        const all = Array.from(document.querySelectorAll("div, main, section")).filter(isVisible);

        const panels = all
            .map((el) => {
                const rect = el.getBoundingClientRect();
                const text = textOf(el);

                let score = 0;

                if (rect.x > 300) score += 50;
                if (rect.width > 500) score += 20;
                if (rect.height > 400) score += 20;
                if (text.includes("Вакансия")) score += 20;
                if (text.includes("Сообщение")) score += 20;
                if (text.includes("Отклик на вакансию")) score += 10;
                if (text.includes("Чаты")) score -= 60;

                return {
                    el,
                    rect,
                    text,
                    score,
                    area: rect.width * rect.height,
                };
            })
            .filter((item) => {
                return (
                    item.rect.x > 300 &&
                    item.rect.width > 500 &&
                    item.rect.height > 350 &&
                    item.text.length > 20
                );
            })
            .sort((a, b) => {
                if (b.score !== a.score) return b.score - a.score;
                return b.area - a.area;
            });

        const panel = panels.length ? panels[0].el : document.body;
        const panelRect = panel.getBoundingClientRect();
        const panelText = textOf(panel);

        const inputCandidates = Array.from(
            document.querySelectorAll(
                'textarea[placeholder*="Сообщение"], input[placeholder*="Сообщение"], [placeholder*="Сообщение"]'
            )
        ).filter((el) => {
            if (!isVisible(el)) return false;

            const rect = el.getBoundingClientRect();

            return rect.x > 300 && rect.y > 300;
        });

        const hasInput = inputCandidates.length > 0;

        const disabledPatterns = [
            "Работодатель отключил переписку",
            "Переписка будет доступна после приглашения работодателя",
            "Переписка недоступна",
            "чат недоступен"
        ];

        const isDisabled = disabledPatterns.some((phrase) => {
            return panelText.toLowerCase().includes(phrase.toLowerCase());
        });

        const isRefusal = /(^|\\s)отказ(\\s|$)/i.test(panelText);

        const timePattern = /\\b([01]?\\d|2[0-3]):([0-5]\\d)\\b/;

        const timeNodes = Array.from(panel.querySelectorAll("*"))
            .filter(isVisible)
            .map((el) => {
                const rect = el.getBoundingClientRect();
                const text = textOf(el);

                return {
                    el,
                    rect,
                    text,
                };
            })
            .filter((item) => {
                if (!timePattern.test(item.text)) return false;

                if (item.rect.x < panelRect.x - 5 || item.rect.x > panelRect.x + panelRect.width + 5) {
                    return false;
                }

                if (item.rect.y < panelRect.y - 5 || item.rect.y > panelRect.y + panelRect.height + 5) {
                    return false;
                }

                if (item.text.length > 350) return false;
                if (item.rect.width > 500 || item.rect.height > 220) return false;

                return true;
            })
            .map((item) => {
                const match = item.text.match(timePattern);

                return {
                    text: item.text,
                    time: match ? match[0] : null,
                    x: item.rect.x,
                    y: item.rect.y,
                    width: item.rect.width,
                    height: item.rect.height,
                    centerX: item.rect.x + item.rect.width / 2,
                    centerY: item.rect.y + item.rect.height / 2,
                };
            })
            .filter((item) => item.time !== null)
            .sort((a, b) => {
                if (Math.abs(a.centerY - b.centerY) > 8) {
                    return a.centerY - b.centerY;
                }

                return a.centerX - b.centerX;
            });

        const lastTimeNode = timeNodes.length ? timeNodes[timeNodes.length - 1] : null;

        let lastAuthor = "unknown";
        let lastTimeText = null;
        let lastDateLabel = null;

        if (lastTimeNode) {
            lastTimeText = lastTimeNode.time;

            const panelCenterX = panelRect.x + panelRect.width / 2;

            if (lastTimeNode.centerX > panelCenterX) {
                lastAuthor = "me";
            } else {
                lastAuthor = "employer";
            }

            const datePattern = /^(Сегодня|Вчера|\\d{1,2}\\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)|\\d{1,2}\\.\\d{1,2}(\\.\\d{4})?)$/i;

            const dateNodes = Array.from(panel.querySelectorAll("*"))
                .filter(isVisible)
                .map((el) => {
                    const rect = el.getBoundingClientRect();
                    const text = textOf(el);

                    return {
                        text,
                        centerY: rect.y + rect.height / 2,
                        centerX: rect.x + rect.width / 2,
                        width: rect.width,
                        height: rect.height,
                    };
                })
                .filter((item) => {
                    if (!datePattern.test(item.text)) return false;
                    if (item.centerY >= lastTimeNode.centerY) return false;
                    if (item.width > 250 || item.height > 80) return false;

                    return true;
                })
                .sort((a, b) => a.centerY - b.centerY);

            if (dateNodes.length) {
                lastDateLabel = dateNodes[dateNodes.length - 1].text;
            }
        }

        return {
            conversation_text: panelText,
            last_time_text: lastTimeText,
            last_date_label: lastDateLabel,
            last_author: lastAuthor,
            has_input: hasInput,
            is_disabled: isDisabled,
            is_refusal: isRefusal,
        };
    }
    """

    try:
        data = page.evaluate(js)
    except Exception:
        data = {}

    return ChatState(
        conversation_text=normalize_text(str(data.get("conversation_text", ""))),
        last_time_text=data.get("last_time_text"),
        last_date_label=data.get("last_date_label"),
        last_author=data.get("last_author"),
        has_input=bool(data.get("has_input", False)),
        is_disabled=bool(data.get("is_disabled", False)),
        is_refusal=bool(data.get("is_refusal", False)),
    )


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


def parse_date_label(label: Optional[str], now: datetime) -> date:
    if not label:
        return now.date()

    label = normalize_text(label).lower()

    if label == "сегодня":
        return now.date()

    if label == "вчера":
        return (now - timedelta(days=1)).date()

    month_match = re.match(
        r"^([0-3]?\d)\s+"
        r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)$",
        label,
        re.IGNORECASE,
    )

    if month_match:
        day = int(month_match.group(1))
        month = parse_russian_month(month_match.group(2))

        if month:
            candidate = date(now.year, month, day)

            if candidate > now.date():
                candidate = date(now.year - 1, month, day)

            return candidate

    numeric_match = re.match(r"^([0-3]?\d)\.([01]?\d)(?:\.(20\d{2}))?$", label)

    if numeric_match:
        day = int(numeric_match.group(1))
        month = int(numeric_match.group(2))
        year = int(numeric_match.group(3)) if numeric_match.group(3) else now.year

        candidate = date(year, month, day)

        if candidate > now.date():
            candidate = date(year - 1, month, day)

        return candidate

    return now.date()


def parse_last_message_datetime(state: ChatState, now: datetime) -> Optional[datetime]:
    if not state.last_time_text:
        return None

    match = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", state.last_time_text)

    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))

    message_date = parse_date_label(state.last_date_label, now)

    candidate = datetime.combine(message_date, datetime.min.time()).replace(hour=hour, minute=minute)

    if not state.last_date_label and candidate > now + timedelta(minutes=5):
        candidate = candidate - timedelta(days=1)

    return candidate


def get_age_status(last_message_at: datetime, now: datetime) -> str:
    age = now - last_message_at

    if age < timedelta(hours=MIN_HOURS_AFTER_LAST_MESSAGE):
        return "too_fresh"

    if age > timedelta(days=MAX_DAYS_AFTER_LAST_MESSAGE):
        return "too_old"

    return "eligible"


def already_sent_followup(state: ChatState) -> bool:
    return FOLLOWUP_TEXT.lower() in state.conversation_text.lower()


def find_send_button_near_input(page: Page) -> Optional[Locator]:
    selectors = [
        "button[aria-label*='Отправить']",
        "button[aria-label*='Send']",
        "button:has-text('Отправить')",
        "button[type='submit']",
    ]

    visible = first_visible(page, selectors, timeout=500)

    if visible is not None:
        return visible

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

                is_right_of_input = box["x"] >= input_box["x"] + input_box["width"] - 90
                is_same_row = abs(button_center_y - input_center_y) < 70
                is_reasonable_size = 20 <= box["width"] <= 90 and 20 <= box["height"] <= 90

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

    time.sleep(0.4)

    send_button = find_send_button_near_input(page)

    if send_button is not None:
        try:
            send_button.click(timeout=3000)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        page.keyboard.press("Enter")
        time.sleep(1.0)
        return True
    except Exception:
        return False


def process_current_chat(page: Page, item: ChatItem, stats: Stats) -> bool:
    """
    Возвращает True, если нужно остановить весь обход.
    Останавливаемся на первом чате, где последнее сообщение свежее 24 часов.
    """

    now = datetime.now()
    state = get_chat_state(page)

    print("")
    print(f"[{stats.checked}] Чат: {item.title}")

    last_message_at = parse_last_message_datetime(state, now)

    if last_message_at is None:
        print("[SKIP] Не смог определить дату/время последнего сообщения.")
        stats.skipped_no_time += 1
        write_result(
            {
                "status": "skipped_no_time",
                "chat_id": item.chat_id,
                "title": item.title,
                "last_time_text": state.last_time_text,
                "last_date_label": state.last_date_label,
                "conversation_text_preview": state.conversation_text[:700],
            }
        )
        return False

    age_status = get_age_status(last_message_at, now)

    print(f"Последнее сообщение: {last_message_at.isoformat(timespec='minutes')}")
    print(f"Возраст: {age_status}")
    print(f"Автор последнего сообщения: {state.last_author}")

    if age_status == "too_fresh":
        print("[STOP] Дошёл до чата свежее 24 часов. Дальше выше все чаты ещё новее.")
        stats.stopped_too_fresh += 1
        write_result(
            {
                "status": "stopped_too_fresh",
                "chat_id": item.chat_id,
                "title": item.title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
                "last_author": state.last_author,
            }
        )
        return True

    if age_status == "too_old":
        print("[SKIP] Старше 14 дней.")
        stats.skipped_too_old += 1
        write_result(
            {
                "status": "skipped_too_old",
                "chat_id": item.chat_id,
                "title": item.title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
                "last_author": state.last_author,
            }
        )
        return False

    if item.is_refusal or state.is_refusal:
        print("[SKIP] Отказ.")
        stats.skipped_refusal += 1
        write_result(
            {
                "status": "skipped_refusal",
                "chat_id": item.chat_id,
                "title": item.title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
                "last_author": state.last_author,
            }
        )
        return False

    if state.is_disabled:
        print("[SKIP] Переписка отключена работодателем.")
        stats.skipped_disabled_chat += 1
        write_result(
            {
                "status": "skipped_disabled_chat",
                "chat_id": item.chat_id,
                "title": item.title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
                "last_author": state.last_author,
            }
        )
        return False

    if not state.has_input:
        print("[SKIP] Нет поля для сообщения.")
        stats.skipped_no_input += 1
        write_result(
            {
                "status": "skipped_no_input",
                "chat_id": item.chat_id,
                "title": item.title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
                "last_author": state.last_author,
            }
        )
        return False

    if already_sent_followup(state):
        print("[SKIP] Такое напоминание уже отправляли.")
        stats.skipped_already_sent += 1
        write_result(
            {
                "status": "skipped_already_sent",
                "chat_id": item.chat_id,
                "title": item.title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
                "last_author": state.last_author,
            }
        )
        return False

    print("[SEND] Отправляю напоминание.")

    sent = send_followup_message(page)

    if sent:
        stats.sent += 1
        write_result(
            {
                "status": "sent",
                "chat_id": item.chat_id,
                "title": item.title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
                "last_author": state.last_author,
                "message": FOLLOWUP_TEXT,
            }
        )
    else:
        print("[ERROR] Не удалось отправить сообщение.")
        stats.errors += 1
        write_result(
            {
                "status": "send_error",
                "chat_id": item.chat_id,
                "title": item.title,
                "last_message_at": last_message_at.isoformat(timespec="minutes"),
                "last_author": state.last_author,
            }
        )

    return False


def process_visible_batch_bottom_to_top(
    page: Page,
    visible_items: list[ChatItem],
    processed_chat_ids: set[str],
    stats: Stats,
) -> bool:
    """
    Обрабатывает текущий видимый кусок списка снизу вверх.
    Не открывает чат через URL. Только кликает по видимой карточке слева.
    """

    items_to_process = list(reversed(visible_items))

    for item in items_to_process:
        if item.chat_id in processed_chat_ids:
            stats.skipped_duplicate += 1
            continue

        processed_chat_ids.add(item.chat_id)
        stats.checked += 1

        if stats.checked > MAX_PROCESSED_CHATS_SAFETY:
            print("[STOP] Достигнут аварийный лимит обработки. Останавливаюсь.")
            return True

        before_scroll_top = get_chat_list_scroll_top(page)

        opened = click_visible_chat_by_id(page, item.chat_id)

        if not opened:
            print("")
            print(f"[{stats.checked}] [SKIP] Чат уже не виден в списке: {item.title}")
            stats.skipped_not_visible += 1
            write_result(
                {
                    "status": "skipped_not_visible",
                    "chat_id": item.chat_id,
                    "title": item.title,
                }
            )
            continue

        time.sleep(CHAT_LOAD_WAIT_SECONDS)

        try:
            should_stop = process_current_chat(page, item, stats)

            if should_stop:
                return True

        except Exception as error:
            print(f"[ERROR] Ошибка обработки чата: {error}")
            stats.errors += 1
            write_result(
                {
                    "status": "chat_processing_error",
                    "chat_id": item.chat_id,
                    "title": item.title,
                    "error": str(error),
                }
            )

        if before_scroll_top is not None:
            set_chat_list_scroll_top(page, before_scroll_top)

        time.sleep(DELAY_BETWEEN_CHATS_SECONDS)

    return False


def process_chats_from_current_scroll_position(page: Page, stats: Stats) -> None:
    processed_chat_ids: set[str] = set()
    no_new_batches = 0

    print("")
    print("Начинаю обход от текущей позиции списка.")
    print("Ты уже вручную проскроллил список вниз.")
    print("Я иду снизу вверх по видимым чатам, затем скроллю список выше и продолжаю.")
    print("Чаты открываю кликом слева, без перехода по URL, чтобы список не улетал наверх.")
    print("Остановлюсь на первом чате, где последнее сообщение свежее 24 часов.")
    print("Автор последнего сообщения больше не важен.")
    print("")

    while True:
        current_scroll_top = get_chat_list_scroll_top(page)
        visible_items = collect_visible_chat_items(page)

        new_visible_items = [
            item for item in visible_items if item.chat_id not in processed_chat_ids
        ]

        print("")
        print(f"Текущий scrollTop списка: {current_scroll_top}")
        print(f"Видимых чатов в текущем куске: {len(visible_items)}")
        print(f"Новых чатов в текущем куске: {len(new_visible_items)}")

        if new_visible_items:
            no_new_batches = 0

            should_stop = process_visible_batch_bottom_to_top(
                page=page,
                visible_items=visible_items,
                processed_chat_ids=processed_chat_ids,
                stats=stats,
            )

            if should_stop:
                break
        else:
            no_new_batches += 1
            stats.empty_batches += 1

            if no_new_batches >= MAX_NO_NEW_BATCHES:
                print("[STOP] Несколько раз подряд не нашёл новых чатов. Останавливаюсь.")
                break

        if current_scroll_top is not None:
            set_chat_list_scroll_top(page, current_scroll_top)

        print("")
        print("Скроллю левый список чатов выше...")

        changed = scroll_chat_list_up(page)

        if not changed:
            print("[STOP] Левый список больше не скроллится вверх.")
            break

        time.sleep(0.8)


def print_stats(stats: Stats) -> None:
    print("")
    print("Готово.")
    print("")
    print(f"Проверено чатов: {stats.checked}")
    print(f"Отправлено напоминаний: {stats.sent}")
    print(f"Остановка на чате свежее 24 часов: {stats.stopped_too_fresh}")
    print(f"Пропущено, отказ: {stats.skipped_refusal}")
    print(f"Пропущено, нет поля ввода: {stats.skipped_no_input}")
    print(f"Пропущено, переписка отключена: {stats.skipped_disabled_chat}")
    print(f"Пропущено, старше 14 дней: {stats.skipped_too_old}")
    print(f"Пропущено, уже отправляли: {stats.skipped_already_sent}")
    print(f"Пропущено, не найдено время: {stats.skipped_no_time}")
    print(f"Пропущено, чат уже не виден: {stats.skipped_not_visible}")
    print(f"Повторные chat_id пропущены: {stats.skipped_duplicate}")
    print(f"Пустых кусков списка: {stats.empty_batches}")
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

        page = get_or_open_chat_page(context)
        page.bring_to_front()

        try:
            page.goto(HH_CHAT_URL, wait_until="domcontentloaded")
        except Exception:
            pass

        wait_for_user_ready()

        process_chats_from_current_scroll_position(page, stats)

        print_stats(stats)

        input("Нажми Enter, чтобы закрыть браузер...")
        context.close()


if __name__ == "__main__":
    main()