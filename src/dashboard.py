from __future__ import annotations

import json
import webbrowser
from collections import Counter
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
CONFIG_DIR = PROJECT_ROOT / "config"

SETTINGS_PATH = CONFIG_DIR / "settings.json"

SCRIPT_RUNS_PATH = DATA_DIR / "script_runs.jsonl"
FOLLOWUP_RESULTS_PATH = DATA_DIR / "followup_chats_results.jsonl"
MANUAL_REVIEW_PATH = DATA_DIR / "followup_manual_review.jsonl"
AUTO_APPLY_RESULTS_PATH = DATA_DIR / "auto_apply_results.jsonl"
APPLY_LOG_PATH = DATA_DIR / "apply_log.jsonl"
VACANCIES_PREVIEW_PATH = DATA_DIR / "vacancies_preview.json"
APP_LOG_PATH = LOGS_DIR / "app.log"

HOST = "127.0.0.1"
PORT = 8765

HH_DAILY_RESPONSE_LIMIT = 200
HH_LIMIT_WINDOW_HOURS = 24
RUN_SESSION_GAP_MINUTES = 45


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []

    rows: list[dict] = []

    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()

                if not line:
                    continue

                try:
                    row = json.loads(line)

                    if isinstance(row, dict):
                        rows.append(row)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []

    if limit is not None:
        return rows[-limit:]

    return rows


def read_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}

    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            return data

        return {}
    except Exception:
        return {}


def get_nested(data: dict, path: str, default=None):
    current = data

    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default

        current = current[part]

    return current


def read_vacancies_preview() -> list[dict]:
    if not VACANCIES_PREVIEW_PATH.exists():
        return []

    try:
        with VACANCIES_PREVIEW_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            for key in ("vacancies", "items", "results"):
                value = data.get(key)

                if isinstance(value, list):
                    return value

        return []
    except Exception:
        return []


def read_text_tail(path: Path, max_lines: int = 100) -> list[str]:
    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as file:
            lines = file.readlines()

        return [line.rstrip("\n") for line in lines[-max_lines:]]
    except Exception:
        return []


def parse_dt(value: object) -> datetime | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    candidates = [
        text,
        text.replace("Z", "+00:00"),
    ]

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)

            if parsed.tzinfo is not None:
                parsed = parsed.replace(tzinfo=None)

            return parsed
        except Exception:
            pass

    return None


def counter_by_status(rows: list[dict]) -> dict:
    counter = Counter()

    for row in rows:
        status = str(row.get("status", "unknown"))
        counter[status] += 1

    return dict(counter)


def sort_rows_desc(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: str(row.get("created_at", "")),
        reverse=True,
    )


def count_sessions_from_rows(rows: list[dict], gap_minutes: int = RUN_SESSION_GAP_MINUTES) -> int:
    times: list[datetime] = []

    for row in rows:
        created_at = parse_dt(row.get("created_at"))

        if created_at is not None:
            times.append(created_at)

    if not times:
        return 0

    times.sort()

    sessions = 1
    gap = timedelta(minutes=gap_minutes)
    previous = times[0]

    for current in times[1:]:
        if current - previous > gap:
            sessions += 1

        previous = current

    return sessions


def get_sent_response_rows(auto_apply_rows: list[dict], apply_rows: list[dict]) -> list[dict]:
    result: list[dict] = []

    sent_statuses = {
        "sent",
        "applied",
        "success",
        "submitted",
        "response_sent",
        "apply_sent",
    }

    for row in auto_apply_rows + apply_rows:
        status = str(row.get("status", "")).lower().strip()

        if status in sent_statuses:
            result.append(row)

    return result


def count_cover_letters(sent_rows: list[dict], settings: dict) -> int:
    """
    Старые логи могли не писать отдельное поле cover_letter_sent.
    Поэтому считаем так:

    1. Если в строке явно есть cover_letter_sent / cover_letter_used / message — считаем.
    2. Если в строке явно написано no_cover_letter / without_cover_letter — не считаем.
    3. Если apply.auto_insert_cover_letter = true, считаем отправленные отклики как отклики с сопроводительным.
       Это корректно для наших текущих apply-скриптов, которые реально вставляли текст, но раньше не логировали этот факт.
    """

    auto_insert_cover_letter = bool(
        get_nested(settings, "apply.auto_insert_cover_letter", False)
    )

    count = 0

    positive_bool_keys = [
        "cover_letter_sent",
        "cover_letter_inserted",
        "cover_letter_added",
        "cover_letter_used",
        "has_cover_letter",
        "with_cover_letter",
    ]

    negative_bool_keys = [
        "without_cover_letter",
        "no_cover_letter",
        "cover_letter_missing",
    ]

    text_keys = [
        "cover_letter",
        "cover_letter_text",
        "message",
        "letter",
    ]

    for row in sent_rows:
        explicit_negative = False

        for key in negative_bool_keys:
            if row.get(key) is True:
                explicit_negative = True
                break

        if explicit_negative:
            continue

        explicit_positive = False

        for key in positive_bool_keys:
            if row.get(key) is True:
                explicit_positive = True
                break

        if not explicit_positive:
            for key in text_keys:
                value = row.get(key)

                if isinstance(value, str) and value.strip():
                    explicit_positive = True
                    break

        if explicit_positive:
            count += 1
            continue

        if auto_insert_cover_letter:
            count += 1

    return count


def count_test_or_manual_review(manual_rows: list[dict], followup_rows: list[dict]) -> int:
    rows = manual_rows + [
        row for row in followup_rows
        if str(row.get("status", "")) == "manual_review"
    ]

    seen: set[str] = set()
    count = 0

    for row in rows:
        chat_id = str(row.get("chat_id", "")).strip()
        key = chat_id or json.dumps(row, ensure_ascii=False, sort_keys=True)

        if key in seen:
            continue

        seen.add(key)

        reason = str(row.get("reason", "")).lower()
        text = " ".join(
            str(row.get(key, ""))
            for key in (
                "manual_review_text",
                "last_employer_message_text",
                "conversation_text_preview",
            )
        ).lower()

        if (
            "test" in reason
            or "question" in reason
            or "form" in reason
            or "тест" in text
            or "вопрос" in text
            or "анкета" in text
            or "форма" in text
            or "опрос" in text
        ):
            count += 1

    return count


def build_limit_info(
    sent_rows: list[dict],
    auto_apply_rows: list[dict],
    apply_rows: list[dict],
) -> dict:
    """
    HH считает лимит по аккаунту, а dashboard видит только локальные логи.

    Если скрипт поймал лимит HH, показываем 200/200.
    Если события лимита нет, показываем оценку только по локальным логам.
    """

    now = datetime.now()
    window_start = now - timedelta(hours=HH_LIMIT_WINDOW_HOURS)

    recent_sent: list[datetime] = []

    for row in sent_rows:
        created_at = parse_dt(row.get("created_at"))

        if created_at is None:
            continue

        if created_at >= window_start:
            recent_sent.append(created_at)

    recent_sent.sort()

    all_rows = auto_apply_rows + apply_rows
    limit_events: list[datetime] = []

    limit_statuses = {
        "daily_limit_reached",
        "limit_reached",
        "hh_daily_limit",
        "response_limit_reached",
        "responses_limit_reached",
        "too_many_responses",
        "skipped_daily_limit",
        "stopped_daily_limit",
    }

    for row in all_rows:
        status = str(row.get("status", "")).lower().strip()
        text = json.dumps(row, ensure_ascii=False).lower()

        created_at = parse_dt(row.get("created_at"))

        if created_at is None:
            continue

        if created_at < window_start:
            continue

        has_limit_status = status in limit_statuses

        has_limit_text = (
            "лимит" in text
            and (
                "200" in text
                or "24" in text
                or "отклик" in text
                or "откликов" in text
                or "отклика" in text
            )
        )

        if has_limit_status or has_limit_text:
            limit_events.append(created_at)

    used_by_logs = len(recent_sent)
    hh_limit_detected = bool(limit_events)

    if hh_limit_detected:
        used = HH_DAILY_RESPONSE_LIMIT
        remaining = 0
        status = "limit_reached"

        if recent_sent:
            reset_at = recent_sent[0] + timedelta(hours=HH_LIMIT_WINDOW_HOURS)
        else:
            reset_at = min(limit_events) + timedelta(hours=HH_LIMIT_WINDOW_HOURS)

        seconds_left = max(0, int((reset_at - now).total_seconds()))

        source = "hh_limit_event"
        note = "HH уже сообщил о лимите. Локальные логи могут быть неполными, поэтому показываю 200/200."
    else:
        used = used_by_logs
        remaining = max(0, HH_DAILY_RESPONSE_LIMIT - used)

        if recent_sent:
            reset_at = recent_sent[0] + timedelta(hours=HH_LIMIT_WINDOW_HOURS)
            seconds_left = max(0, int((reset_at - now).total_seconds()))
        else:
            reset_at = None
            seconds_left = 0

        status = "available"
        source = "local_logs"
        note = "Оценка только по локальным логам. Ручные отклики на HH сюда не попадают."

    return {
        "limit": HH_DAILY_RESPONSE_LIMIT,
        "used_24h": used,
        "used_by_logs_24h": used_by_logs,
        "remaining": remaining,
        "status": status,
        "reset_at": reset_at.isoformat(timespec="seconds") if reset_at else "",
        "seconds_left": seconds_left,
        "source": source,
        "note": note,
        "limit_events_24h": len(limit_events),
    }


def build_explicit_run_counts(script_rows: list[dict]) -> Counter:
    counts = Counter()

    known_scripts = {
        "src\\main.py": "Сбор вакансий",
        "src/main.py": "Сбор вакансий",
        "src\\apply_from_list.py": "Полуавто отклики",
        "src/apply_from_list.py": "Полуавто отклики",
        "src\\auto_apply_pages.py": "Автоотклики",
        "src/auto_apply_pages.py": "Автоотклики",
        "src\\followup_chats_review.py": "Напоминания в чатах",
        "src/followup_chats_review.py": "Напоминания в чатах",
        "src\\dashboard.py": "Dashboard",
        "src/dashboard.py": "Dashboard",
    }

    for row in script_rows:
        script_name = str(row.get("script_name", "")).strip()
        script_path = str(row.get("script_path", "")).strip()

        normalized_path = script_path.replace("/", "\\")
        label = (
            script_name
            or known_scripts.get(script_path)
            or known_scripts.get(normalized_path)
            or script_path
            or "unknown"
        )

        counts[label] += 1

    return counts


def build_inferred_run_counts(
    vacancies: list[dict],
    apply_rows: list[dict],
    auto_apply_rows: list[dict],
    followup_rows: list[dict],
) -> Counter:
    counts = Counter()

    counts["Сбор вакансий"] = 1 if vacancies else 0
    counts["Полуавто отклики"] = count_sessions_from_rows(apply_rows)
    counts["Автоотклики"] = count_sessions_from_rows(auto_apply_rows)
    counts["Напоминания в чатах"] = count_sessions_from_rows(followup_rows)
    counts["Dashboard"] = 0

    return counts


def merge_run_counts(explicit_counts: Counter, inferred_counts: Counter) -> dict:
    labels = [
        "Сбор вакансий",
        "Полуавто отклики",
        "Автоотклики",
        "Напоминания в чатах",
        "Dashboard",
    ]

    result = {}

    for label in labels:
        result[label] = max(
            int(explicit_counts.get(label, 0)),
            int(inferred_counts.get(label, 0)),
        )

    return result


def build_stats() -> dict:
    settings = read_settings()

    script_rows = read_jsonl(SCRIPT_RUNS_PATH)
    followup_rows = read_jsonl(FOLLOWUP_RESULTS_PATH)
    manual_rows = read_jsonl(MANUAL_REVIEW_PATH)
    auto_apply_rows = read_jsonl(AUTO_APPLY_RESULTS_PATH)
    apply_rows = read_jsonl(APPLY_LOG_PATH)
    vacancies = read_vacancies_preview()
    app_log_tail = read_text_tail(APP_LOG_PATH, max_lines=100)

    followup_status = counter_by_status(followup_rows)
    auto_apply_status = counter_by_status(auto_apply_rows)
    apply_status = counter_by_status(apply_rows)

    explicit_run_counts = build_explicit_run_counts(script_rows)
    inferred_run_counts = build_inferred_run_counts(
        vacancies=vacancies,
        apply_rows=apply_rows,
        auto_apply_rows=auto_apply_rows,
        followup_rows=followup_rows,
    )
    run_counts = merge_run_counts(explicit_run_counts, inferred_run_counts)

    sent_response_rows = get_sent_response_rows(auto_apply_rows, apply_rows)

    limit_info = build_limit_info(
        sent_rows=sent_response_rows,
        auto_apply_rows=auto_apply_rows,
        apply_rows=apply_rows,
    )

    total_sent_responses = len(sent_response_rows)
    total_cover_letters = count_cover_letters(sent_response_rows, settings)
    total_test_or_manual = count_test_or_manual_review(manual_rows, followup_rows)

    followup_checked = len(followup_rows)
    followup_sent = followup_status.get("sent", 0)

    manual_review = followup_status.get("manual_review", 0)

    manual_review_unique_ids = {
        str(row.get("chat_id", "")).strip()
        for row in manual_rows
        if str(row.get("chat_id", "")).strip()
    }

    if manual_review_unique_ids:
        manual_review = max(manual_review, len(manual_review_unique_ids))

    skipped_archived = followup_status.get("skipped_archived", 0)
    skipped_refusal = followup_status.get("skipped_refusal", 0)
    archived_or_refusal = skipped_archived + skipped_refusal

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "files": {
            "settings": str(SETTINGS_PATH),
            "script_runs": str(SCRIPT_RUNS_PATH),
            "followup_results": str(FOLLOWUP_RESULTS_PATH),
            "manual_review": str(MANUAL_REVIEW_PATH),
            "auto_apply_results": str(AUTO_APPLY_RESULTS_PATH),
            "apply_log": str(APPLY_LOG_PATH),
            "vacancies_preview": str(VACANCIES_PREVIEW_PATH),
            "app_log": str(APP_LOG_PATH),
        },
        "run_counts": run_counts,
        "run_counts_meta": {
            "mode": "max_explicit_or_inferred",
            "session_gap_minutes": RUN_SESSION_GAP_MINUTES,
            "note": "Старые запуски восстановлены приблизительно по jsonl-событиям. Новые запуски пишутся точно в script_runs.jsonl.",
            "explicit": dict(explicit_run_counts),
            "inferred": dict(inferred_run_counts),
        },
        "limit": limit_info,
        "summary": {
            "total_sent_responses": total_sent_responses,
            "total_cover_letters": total_cover_letters,
            "total_test_or_manual": total_test_or_manual,
            "followup_checked": followup_checked,
            "followup_sent": followup_sent,
            "archived_or_refusal": archived_or_refusal,
            "manual_review": manual_review,
            "vacancies_preview_count": len(vacancies),
            "auto_apply_total": len(auto_apply_rows),
            "apply_log_total": len(apply_rows),
        },
        "status": {
            "followup": followup_status,
            "auto_apply": auto_apply_status,
            "apply": apply_status,
        },
        "recent": {
            "followup": sort_rows_desc(followup_rows)[0:100],
            "manual_review": sort_rows_desc(manual_rows)[0:100],
            "auto_apply": sort_rows_desc(auto_apply_rows)[0:100],
            "apply": sort_rows_desc(apply_rows)[0:100],
            "script_runs": sort_rows_desc(script_rows)[0:100],
            "app_log_tail": app_log_tail,
        },
    }


def render_html() -> str:
    return """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>HH Job Assistant Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --bg: #0f1115;
      --header: #11141b;
      --panel: #171a21;
      --panel2: #202431;
      --text: #e8eaf0;
      --muted: #9aa3b2;
      --green: #43d17a;
      --blue: #6ea8fe;
      --yellow: #ffd166;
      --red: #ff6b6b;
      --border: #2b3040;
    }

    * {
      box-sizing: border-box;
    }

    html {
      scroll-behavior: smooth;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Arial, sans-serif;
    }

    header {
      padding: 20px 28px 12px;
      border-bottom: 1px solid var(--border);
      background: var(--header);
      position: sticky;
      top: 0;
      z-index: 20;
    }

    h1 {
      margin: 0 0 6px;
      font-size: 24px;
    }

    nav {
      margin-top: 14px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    nav a {
      display: inline-block;
      padding: 7px 11px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: #171b25;
      color: var(--text);
      font-size: 13px;
      text-decoration: none;
    }

    nav a:hover {
      border-color: var(--blue);
      color: var(--blue);
      text-decoration: none;
    }

    main {
      padding: 24px 28px 40px;
    }

    section {
      scroll-margin-top: 130px;
      margin-top: 28px;
    }

    h2 {
      font-size: 19px;
      margin: 0 0 12px;
    }

    .muted {
      color: var(--muted);
      font-size: 13px;
    }

    .stats-row {
      display: grid;
      gap: 14px;
      margin-bottom: 18px;
    }

    .stats-row.runs {
      grid-template-columns: repeat(5, minmax(130px, 1fr));
    }

    .stats-row.responses {
      grid-template-columns: repeat(4, minmax(170px, 1fr));
    }

    .stats-row.followups {
      grid-template-columns: repeat(4, minmax(170px, 1fr));
    }

    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      min-height: 92px;
    }

    .card-title {
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
      line-height: 1.25;
    }

    .card-value {
      font-size: 28px;
      font-weight: 700;
      line-height: 1.1;
    }

    .card-subtitle {
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }

    .green { color: var(--green); }
    .blue { color: var(--blue); }
    .yellow { color: var(--yellow); }
    .red { color: var(--red); }

    .table-wrap {
      overflow: auto;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 900px;
    }

    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
      text-align: left;
      font-size: 13px;
    }

    th {
      color: var(--muted);
      background: var(--panel2);
      position: sticky;
      top: 0;
    }

    tr:hover td {
      background: #1d2230;
    }

    a {
      color: var(--blue);
      text-decoration: none;
    }

    a:hover {
      text-decoration: underline;
    }

    .status {
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      background: #2a3040;
      font-size: 12px;
      white-space: nowrap;
    }

    .status.sent,
    .status.applied,
    .status.success,
    .status.submitted,
    .status.response_sent,
    .status.apply_sent {
      color: var(--green);
    }

    .status.manual_review {
      color: var(--yellow);
    }

    .status.daily_limit_reached,
    .status.limit_reached,
    .status.hh_daily_limit,
    .status.skipped_refusal,
    .status.skipped_archived,
    .status.skipped_disabled_chat {
      color: var(--red);
    }

    .small {
      max-width: 560px;
      color: #cfd5e3;
      line-height: 1.35;
    }

    pre {
      margin: 0;
      white-space: pre-wrap;
      font-size: 12px;
      color: #cfd5e3;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
      max-height: 420px;
      overflow: auto;
    }

    .two-cols {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
    }

    @media (max-width: 1200px) {
      .stats-row.runs,
      .stats-row.responses,
      .stats-row.followups {
        grid-template-columns: repeat(2, 1fr);
      }

      .two-cols {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 700px) {
      header {
        padding: 18px 16px 10px;
      }

      main {
        padding: 16px;
      }

      .stats-row.runs,
      .stats-row.responses,
      .stats-row.followups {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
<header>
  <h1>HH Job Assistant Dashboard</h1>
  <div class="muted">
    Локальная статистика по логам проекта. Автообновление каждые 10 секунд.
    Последнее обновление: <span id="generatedAt">...</span>
  </div>

  <nav>
    <a href="#top-stats">Статистика</a>
    <a href="#manual-review">Manual review</a>
    <a href="#followup-events">Follow-up события</a>
    <a href="#followup-statuses">Статусы</a>
    <a href="#auto-events">Auto apply</a>
    <a href="#logs">Логи</a>
  </nav>
</header>

<main>
  <section id="top-stats">
    <h2>Запуски скриптов</h2>

    <div class="stats-row runs">
      <div class="card">
        <div class="card-title">Сбор вакансий</div>
        <div class="card-value blue" id="runCollect">0</div>
      </div>

      <div class="card">
        <div class="card-title">Полуавто отклики</div>
        <div class="card-value blue" id="runSemiApply">0</div>
      </div>

      <div class="card">
        <div class="card-title">Автоотклики</div>
        <div class="card-value blue" id="runAutoApply">0</div>
      </div>

      <div class="card">
        <div class="card-title">Напоминания в чатах</div>
        <div class="card-value blue" id="runFollowups">0</div>
      </div>

      <div class="card">
        <div class="card-title">Dashboard</div>
        <div class="card-value blue" id="runDashboard">0</div>
      </div>
    </div>

    <h2>Отклики</h2>

    <div class="stats-row responses">
      <div class="card">
        <div class="card-title">Отправлено откликов</div>
        <div class="card-value green" id="sentResponses">0</div>
      </div>

      <div class="card">
        <div class="card-title">Отправлено сопроводительных</div>
        <div class="card-value green" id="coverLetters">0</div>
        <div class="card-subtitle">По явным логам или настройке auto_insert_cover_letter</div>
      </div>

      <div class="card">
        <div class="card-title">Тест / вопросы / форма</div>
        <div class="card-value yellow" id="testOrManual">0</div>
        <div class="card-subtitle">Вакансии, куда нужно вернуться руками</div>
      </div>

      <div class="card">
        <div class="card-title">Лимит HH 200 / 24 часа</div>
        <div class="card-value" id="limitMain">0 / 200</div>
        <div class="card-subtitle" id="limitDetails">...</div>
      </div>
    </div>

    <h2>Чаты HH</h2>

    <div class="stats-row followups">
      <div class="card">
        <div class="card-title">Проверено чатов</div>
        <div class="card-value" id="checkedChats">0</div>
      </div>

      <div class="card">
        <div class="card-title">Отправлено напоминаний</div>
        <div class="card-value green" id="sentFollowups">0</div>
      </div>

      <div class="card">
        <div class="card-title">Архив или отказ</div>
        <div class="card-value red" id="archivedRefusal">0</div>
      </div>

      <div class="card">
        <div class="card-title">Нужен ответ работодателю</div>
        <div class="card-value yellow" id="manualReviewCount">0</div>
      </div>
    </div>
  </section>

  <section id="manual-review">
    <h2>Manual review</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Дата</th>
            <th>Причина</th>
            <th>Вакансия / чат</th>
            <th>Последнее сообщение</th>
            <th>Текст для разбора</th>
          </tr>
        </thead>
        <tbody id="manualTable"></tbody>
      </table>
    </div>
  </section>

  <section id="followup-events">
    <h2>Последние follow-up события</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Дата</th>
            <th>Статус</th>
            <th>Вакансия / чат</th>
            <th>Последнее сообщение</th>
            <th>Автор</th>
          </tr>
        </thead>
        <tbody id="followupTable"></tbody>
      </table>
    </div>
  </section>

  <section id="followup-statuses" class="two-cols">
    <div>
      <h2>Статусы follow-up</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Статус</th>
              <th>Количество</th>
            </tr>
          </thead>
          <tbody id="followupStatusTable"></tbody>
        </table>
      </div>
    </div>

    <div>
      <h2>Статусы auto apply</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Статус</th>
              <th>Количество</th>
            </tr>
          </thead>
          <tbody id="autoStatusTable"></tbody>
        </table>
      </div>
    </div>
  </section>

  <section id="auto-events">
    <h2>Последние auto apply события</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Дата</th>
            <th>Статус</th>
            <th>Вакансия</th>
            <th>Компания</th>
            <th>URL</th>
          </tr>
        </thead>
        <tbody id="autoTable"></tbody>
      </table>
    </div>
  </section>

  <section id="logs">
    <h2>Хвост logs/app.log</h2>
    <pre id="appLog">...</pre>
  </section>
</main>

<script>
function esc(value) {
  if (value === null || value === undefined) return "";

  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusClass(status) {
  return String(status || "").replaceAll(" ", "_");
}

function chatLink(row) {
  if (row.url) {
    return row.url;
  }

  if (row.chat_id) {
    return "https://hh.ru/chat/" + row.chat_id;
  }

  return "";
}

function formatSeconds(seconds) {
  seconds = Number(seconds || 0);

  if (seconds <= 0) {
    return "00:00:00";
  }

  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;

  return [
    String(h).padStart(2, "0"),
    String(m).padStart(2, "0"),
    String(s).padStart(2, "0"),
  ].join(":");
}

function renderStatusTable(targetId, data) {
  const tbody = document.getElementById(targetId);
  const entries = Object.entries(data || {}).sort((a, b) => b[1] - a[1]);

  if (!entries.length) {
    tbody.innerHTML = "<tr><td colspan='2' class='muted'>Нет данных</td></tr>";
    return;
  }

  tbody.innerHTML = entries.map(([status, count]) => `
    <tr>
      <td><span class="status ${statusClass(status)}">${esc(status)}</span></td>
      <td>${count}</td>
    </tr>
  `).join("");
}

function renderManual(rows) {
  const tbody = document.getElementById("manualTable");

  if (!rows || !rows.length) {
    tbody.innerHTML = "<tr><td colspan='5' class='muted'>Нет manual review событий</td></tr>";
    return;
  }

  tbody.innerHTML = rows.map(row => {
    const link = chatLink(row);
    const title = row.title || row.chat_id || "";
    const manualText = row.manual_review_text || row.last_employer_message_text || row.conversation_text_preview || "";

    return `
      <tr>
        <td>${esc(row.created_at)}</td>
        <td><span class="status manual_review">${esc(row.reason || row.status)}</span></td>
        <td>${link ? `<a href="${esc(link)}" target="_blank">${esc(title)}</a>` : esc(title)}</td>
        <td>${esc(row.last_message_at || "")}</td>
        <td class="small">${esc(manualText).slice(0, 900)}</td>
      </tr>
    `;
  }).join("");
}

function renderFollowup(rows) {
  const tbody = document.getElementById("followupTable");

  if (!rows || !rows.length) {
    tbody.innerHTML = "<tr><td colspan='5' class='muted'>Нет follow-up событий</td></tr>";
    return;
  }

  tbody.innerHTML = rows.map(row => {
    const link = chatLink(row);
    const title = row.title || row.chat_id || "";

    return `
      <tr>
        <td>${esc(row.created_at)}</td>
        <td><span class="status ${statusClass(row.status)}">${esc(row.status)}</span></td>
        <td>${link ? `<a href="${esc(link)}" target="_blank">${esc(title)}</a>` : esc(title)}</td>
        <td>${esc(row.last_message_at || "")}</td>
        <td>${esc(row.last_author || "")}</td>
      </tr>
    `;
  }).join("");
}

function renderAutoApply(rows) {
  const tbody = document.getElementById("autoTable");

  if (!rows || !rows.length) {
    tbody.innerHTML = "<tr><td colspan='5' class='muted'>Нет auto apply событий</td></tr>";
    return;
  }

  tbody.innerHTML = rows.map(row => {
    const url = row.url || row.vacancy_url || "";
    const title = row.title || row.vacancy_title || "";
    const company = row.company || row.company_name || "";

    return `
      <tr>
        <td>${esc(row.created_at)}</td>
        <td><span class="status ${statusClass(row.status)}">${esc(row.status)}</span></td>
        <td>${esc(title)}</td>
        <td>${esc(company)}</td>
        <td>${url ? `<a href="${esc(url)}" target="_blank">Открыть</a>` : ""}</td>
      </tr>
    `;
  }).join("");
}

function renderLimit(limit) {
  const main = document.getElementById("limitMain");
  const details = document.getElementById("limitDetails");

  const used = limit.used_24h || 0;
  const usedByLogs = limit.used_by_logs_24h || 0;
  const max = limit.limit || 200;
  const remaining = limit.remaining || 0;

  main.textContent = used + " / " + max;

  if (limit.status === "limit_reached") {
    main.className = "card-value red";

    if (limit.source === "hh_limit_event") {
      details.textContent = "HH сообщил о лимите. По локальным логам за 24ч: " + usedByLogs + ". До обновления: " + formatSeconds(limit.seconds_left);
    } else {
      details.textContent = "Лимит исчерпан. До обновления: " + formatSeconds(limit.seconds_left);
    }
  } else {
    main.className = "card-value green";
    details.textContent = "Оценка по логам за 24ч: " + usedByLogs + ". Доступно примерно: " + remaining + ". Обновление окна: " + formatSeconds(limit.seconds_left);
  }
}

async function loadData() {
  const response = await fetch("/api/stats?t=" + Date.now());
  const data = await response.json();

  document.getElementById("generatedAt").textContent = data.generated_at || "...";

  const s = data.summary || {};
  const runs = data.run_counts || {};

  document.getElementById("runCollect").textContent = runs["Сбор вакансий"] || 0;
  document.getElementById("runSemiApply").textContent = runs["Полуавто отклики"] || 0;
  document.getElementById("runAutoApply").textContent = runs["Автоотклики"] || 0;
  document.getElementById("runFollowups").textContent = runs["Напоминания в чатах"] || 0;
  document.getElementById("runDashboard").textContent = runs["Dashboard"] || 0;

  document.getElementById("sentResponses").textContent = s.total_sent_responses || 0;
  document.getElementById("coverLetters").textContent = s.total_cover_letters || 0;
  document.getElementById("testOrManual").textContent = s.total_test_or_manual || 0;

  document.getElementById("checkedChats").textContent = s.followup_checked || 0;
  document.getElementById("sentFollowups").textContent = s.followup_sent || 0;
  document.getElementById("archivedRefusal").textContent = s.archived_or_refusal || 0;
  document.getElementById("manualReviewCount").textContent = s.manual_review || 0;

  renderLimit(data.limit || {});

  renderManual((data.recent && data.recent.manual_review) || []);
  renderFollowup((data.recent && data.recent.followup) || []);
  renderAutoApply((data.recent && data.recent.auto_apply) || []);

  renderStatusTable("followupStatusTable", (data.status && data.status.followup) || {});
  renderStatusTable("autoStatusTable", (data.status && data.status.auto_apply) || {});

  document.getElementById("appLog").textContent = ((data.recent && data.recent.app_log_tail) || []).join("\\n") || "Нет данных";
}

loadData();
setInterval(loadData, 10000);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def send_json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_html(self, html: str, status: int = 200) -> None:
        payload = html.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self.send_html(render_html())
            return

        if parsed.path == "/api/stats":
            self.send_json(build_stats())
            return

        self.send_json({"error": "not_found"}, status=404)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    ensure_dirs()

    url = f"http://{HOST}:{PORT}"

    print("")
    print("HH Job Assistant Dashboard")
    print(f"Открываю: {url}")
    print("Для остановки нажми Ctrl+C")
    print("")

    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
        print("Dashboard остановлен.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()