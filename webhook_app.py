import base64
import hashlib
import hmac
import logging
import os
from urllib.parse import parse_qs

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from knowledge_base import (
    CATEGORIES,
    MAIN_MENUS,
    ROOT_DIR,
    TERMS_BY_ID,
    find_category,
    find_menu,
    find_term,
    image_exists,
    terms_for_category,
)


logger = logging.getLogger("kkz.webhook")
app = FastAPI(title="KKZ LINE Knowledge Webhook")
app.mount("/static", StaticFiles(directory=ROOT_DIR / "static"), name="static")
MAX_QUICK_REPLY_ITEMS = 13
TERM_PAGE_SIZE = 10
MAX_QUICK_REPLY_LABEL_LENGTH = 20


def get_channel_secret():
    return os.getenv("LINE_CHANNEL_SECRET", "")


def get_channel_access_token():
    return os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")


def get_public_base_url():
    return os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


def verify_line_signature(body, signature, secret=None):
    secret = secret if secret is not None else get_channel_secret()
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def quick_reply_item(label, data=None, text=None):
    action = {"type": "message", "label": label, "text": text or label}
    if data:
        action = {"type": "postback", "label": label, "data": data, "displayText": label}
    return {"type": "action", "action": action}


def compact_quick_reply_label(value):
    label = str(value).split("（", 1)[0].strip()
    if len(label) <= MAX_QUICK_REPLY_LABEL_LENGTH:
        return label
    return f"{label[: MAX_QUICK_REPLY_LABEL_LENGTH - 3]}..."


def quick_reply(items):
    return {"items": items[:MAX_QUICK_REPLY_ITEMS]}


def main_menu_items():
    return [
        quick_reply_item(menu["title"], f"menu={menu_id}")
        for menu_id, menu in MAIN_MENUS.items()
    ]


def navigation_items(back_to=None):
    items = []
    if back_to:
        items.append(quick_reply_item("返回", back_to))
    items.append(quick_reply_item("主選單", "menu=main"))
    return items


def main_menu_message():
    return {
        "type": "text",
        "text": "請選擇金融知識速查主題：",
        "quickReply": quick_reply(main_menu_items()),
    }


def category_message(menu_id):
    menu = MAIN_MENUS[menu_id]
    items = [
        quick_reply_item(CATEGORIES[category_id]["title"], f"category={category_id}")
        for category_id in menu["categories"]
    ]
    items.extend(navigation_items("menu=main"))
    return {
        "type": "text",
        "text": f"{menu['title']}：請選擇子分類。",
        "quickReply": quick_reply(items),
    }


def term_list_message(category_id, page=1):
    category = CATEGORIES[category_id]
    terms = terms_for_category(category_id)
    total_pages = max(1, (len(terms) + TERM_PAGE_SIZE - 1) // TERM_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * TERM_PAGE_SIZE
    visible_terms = terms[start : start + TERM_PAGE_SIZE]
    items = [
        quick_reply_item(compact_quick_reply_label(term.title), f"term={term.id}")
        for term in visible_terms
    ]
    if page > 1:
        items.append(quick_reply_item("上一頁", f"category={category_id}&page={page - 1}"))
    if page < total_pages:
        items.append(quick_reply_item("下一頁", f"category={category_id}&page={page + 1}"))
    items.extend(navigation_items(f"menu={category['menu']}"))
    page_label = f"（{page}/{total_pages}）" if total_pages > 1 else ""
    return {
        "type": "text",
        "text": f"{category['title']}{page_label}：請選擇名詞。",
        "quickReply": quick_reply(items),
    }


def term_messages(term):
    messages = []
    base_url = get_public_base_url()
    if term.image and base_url.startswith("https://") and image_exists(term):
        image_url = f"{base_url}/static/knowledge/{term.image}"
        messages.append(
            {
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url,
            }
        )

    category = CATEGORIES[term.category]
    messages.append(
        {
            "type": "text",
            "text": f"{term.title}\n\n{term.text}",
            "quickReply": quick_reply(
                navigation_items(f"category={term.category}")
                + [quick_reply_item(category["title"], f"category={term.category}")]
            ),
        }
    )
    return messages


def parse_postback_data(data):
    parsed = parse_qs(data or "", keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values}


def messages_for_payload(payload):
    if not payload:
        return []

    parsed = parse_postback_data(payload)
    if parsed.get("menu") == "main":
        return [main_menu_message()]
    if menu_id := parsed.get("menu"):
        if menu_id in MAIN_MENUS:
            return [category_message(menu_id)]
    if category_id := parsed.get("category"):
        if category_id in CATEGORIES:
            try:
                page = int(parsed.get("page", "1"))
            except ValueError:
                page = 1
            return [term_list_message(category_id, page)]
    if term_id := parsed.get("term"):
        if term_id in TERMS_BY_ID:
            return term_messages(TERMS_BY_ID[term_id])

    if menu_id := find_menu(payload):
        return [category_message(menu_id)]
    if category_id := find_category(payload):
        return [term_list_message(category_id)]
    if term := find_term(payload):
        return term_messages(term)

    return []


def payload_from_event(event):
    event_type = event.get("type")
    if event_type == "message":
        message = event.get("message", {})
        if message.get("type") == "text":
            return message.get("text", "")
    if event_type == "postback":
        return event.get("postback", {}).get("data", "")
    return ""


def reply_to_line(reply_token, messages):
    token = get_channel_access_token()
    if not token or not reply_token or not messages:
        if not token:
            logger.warning("LINE_CHANNEL_ACCESS_TOKEN is not set; skip reply.")
        return

    response = requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"replyToken": reply_token, "messages": messages[:5]},
        timeout=20,
    )
    response.raise_for_status()


def handle_event(event):
    messages = messages_for_payload(payload_from_event(event))
    if not messages:
        return
    try:
        reply_to_line(event.get("replyToken"), messages)
    except requests.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", "unknown")
        logger.warning("LINE reply failed, status=%s", status_code)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request, x_line_signature: str = Header(default="")):
    body = await request.body()
    if not verify_line_signature(body, x_line_signature):
        raise HTTPException(status_code=403, detail="invalid LINE signature")

    payload = await request.json()
    for event in payload.get("events", []):
        handle_event(event)

    return {"status": "ok"}
