import asyncio
import os
import re
from pathlib import Path
from telethon.sessions import StringSession

import requests
from telethon import TelegramClient
from telethon.tl.types import MessageEntityTextUrl
from singbox2proxy import SingBoxBatch


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "subscription.txt"

MESSAGE_LIMIT = int(os.getenv("MESSAGE_LIMIT", "7"))
if MESSAGE_LIMIT not in (5, 6, 7):
    raise ValueError("MESSAGE_LIMIT должен быть 5, 6 или 7")

HEALTHCHECK_URL = os.getenv(
    "HEALTHCHECK_URL",
    "https://www.gstatic.com/generate_204",
)

SCHEME_RE = re.compile(
    r"""(?i)\b(?:vless|trojan|vmess|hy2|ss)://[^\s<>"']+"""
)
TRAILING_PUNCTUATION = ".,!?;:)]}"


def clean_link(link):
    return link.rstrip(TRAILING_PUNCTUATION)


def normalize_link(link):
    """Заменяет неподдерживаемый sing-box параметр fp=unsafe на fp=chrome."""
    return re.sub(
        r"(?i)([?&]fp=)unsafe(?=(&|#|$))",
        r"\1chrome",
        link,
    )


def extract_links(message):
    found = []

    # Ссылки, написанные прямо в тексте сообщения.
    text = message.raw_text or ""
    found.extend(
        clean_link(match.group(0))
        for match in SCHEME_RE.finditer(text)
    )

    # Ссылки, скрытые за кликабельным текстом.
    for entity in message.entities or []:
        if isinstance(entity, MessageEntityTextUrl):
            url = clean_link(entity.url)
            if url.lower().startswith(
                ("vless://", "trojan://", "vmess://", "hy2://", "ss://")
            ):
                found.append(url)

    return found


async def collect_recent_links():
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    channel = os.environ["TG_CHANNEL"]

    session_string = os.getenv("TG_SESSION")
    session = (
        StringSession(session_string)
        if session_string
        else "telegram_session"
    )

    client = TelegramClient(
        session,
        api_id,
        api_hash,
        timeout=5,
        connection_retries=1,
        request_retries=1,
        retry_delay=0,
        auto_reconnect=False,
    )

    links = []

    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram-сессия не авторизована. "
                "Проверь TG_SESSION или локальный telegram_session.session."
            )

        async for message in client.iter_messages(
            channel,
            limit=MESSAGE_LIMIT,
        ):
            links.extend(extract_links(message))
    finally:
        await client.disconnect()

    normalized = (normalize_link(link) for link in links)
    return list(dict.fromkeys(normalized))



def is_working(link):
    """Проверяет, проходит ли HTTPS-запрос через прокси."""
    batch = SingBoxBatch(
        [link],
        batch_size=1,
        log_level="error",
    )

    try:
        proxy = batch[0]

        with requests.Session() as session:
            # Не используем системные настройки прокси из окружения.
            session.trust_env = False

            response = session.get(
                HEALTHCHECK_URL,
                proxies=proxy.proxies,
                timeout=(4, 8),
                allow_redirects=False,
            )

            try:
                return response.status_code == 204
            finally:
                response.close()
    finally:
        batch.stop()


def save_subscription(links):
    temp_file = OUTPUT_FILE.with_name(".subscription.txt.tmp")
    content = "\n".join(links)

    if content:
        content += "\n"

    temp_file.write_text(content, encoding="utf-8")
    temp_file.replace(OUTPUT_FILE)


async def main():
    links = await collect_recent_links()
    print(f"Найдено ссылок: {len(links)}")

    working = []
    failed = 0

    for index, link in enumerate(links, start=1):
        try:
            if is_working(link):
                working.append(link)
                print(f"[{index}/{len(links)}] Работает")
            else:
                failed += 1
                print(f"[{index}/{len(links)}] Не прошла проверку")
        except Exception as exc:
            # Не выводим ссылку или текст исключения: там могут быть
            # данные доступа. Тип ошибки безопаснее для диагностики.
            failed += 1
            print(
                f"[{index}/{len(links)}] "
                f"Ошибка или таймаут ({type(exc).__name__})"
            )
    if not working:
        raise RuntimeError(
            "Рабочих ссылок не найдено; существующий subscription.txt "
            "оставлен без изменений."
        )

    save_subscription(working)


    save_subscription(working)

    print(f"Готово: рабочих {len(working)}, не прошли {failed}")
    print(f"Файл подписки: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())

