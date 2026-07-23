import base64
import hashlib
import hmac
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from knowledge_base import find_menu, find_term
from webhook_app import (
    app,
    category_message,
    messages_for_payload,
    term_messages,
    verify_line_signature,
)


class KnowledgeBaseTest(unittest.TestCase):
    def test_rich_menu_entry_aliases_are_recognized(self):
        self.assertEqual(find_menu("Technical"), "technical")
        self.assertEqual(find_menu("Options"), "options")
        self.assertEqual(find_menu("Market Basics"), "market_basics")
        self.assertEqual(find_menu("menu=technical"), "technical")
        self.assertEqual(find_menu("menu=options"), "options")
        self.assertEqual(find_menu("menu=market_basics"), "market_basics")

    def test_term_alias_is_recognized(self):
        self.assertEqual(find_term("Moving Average").id, "ma")
        self.assertEqual(find_term("雙重頂").id, "double_top")


class WebhookNavigationTest(unittest.TestCase):
    def test_menu_payload_returns_category_quick_replies(self):
        messages = messages_for_payload("menu=technical")

        self.assertEqual(messages[0]["text"], "Technical：請選擇子分類。")
        labels = [
            item["action"]["label"]
            for item in messages[0]["quickReply"]["items"]
        ]
        self.assertEqual(labels, ["技術指標", "常見線型", "價格行為", "返回", "主選單"])

    def test_category_payload_returns_terms_within_line_limit(self):
        message = category_message("market_basics")
        self.assertLessEqual(len(message["quickReply"]["items"]), 13)

        messages = messages_for_payload("category=tw_flows")
        labels = [
            item["action"]["label"]
            for item in messages[0]["quickReply"]["items"]
        ]
        self.assertIn("外資", labels)
        self.assertIn("借券", labels)
        self.assertLessEqual(len(labels), 13)

    def test_unknown_payload_returns_no_messages(self):
        self.assertEqual(messages_for_payload("not-a-known-topic"), [])

    def test_missing_image_file_falls_back_to_text_only(self):
        term = find_term("Bull Flag")
        with patch("webhook_app.get_public_base_url", return_value="https://example.com"):
            messages = term_messages(term)

        self.assertEqual([message["type"] for message in messages], ["text"])
        self.assertIn("Bull Flag", messages[0]["text"])

    def test_existing_image_file_is_returned_before_text(self):
        term = find_term("Bull Flag")
        with (
            patch("webhook_app.get_public_base_url", return_value="https://example.com"),
            patch("webhook_app.image_exists", return_value=True),
        ):
            messages = term_messages(term)

        self.assertEqual([message["type"] for message in messages], ["image", "text"])
        self.assertEqual(
            messages[0]["originalContentUrl"],
            "https://example.com/static/knowledge/bull-flag.png",
        )

    def test_image_message_requires_https_base_url(self):
        term = find_term("Bull Flag")
        with (
            patch("webhook_app.get_public_base_url", return_value="http://example.com"),
            patch("webhook_app.image_exists", return_value=True),
        ):
            messages = term_messages(term)

        self.assertEqual([message["type"] for message in messages], ["text"])


class LineWebhookEndpointTest(unittest.TestCase):
    def signed_headers(self, body, secret="secret"):
        signature = base64.b64encode(
            hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        ).decode("utf-8")
        return {"X-Line-Signature": signature}

    def test_signature_verification(self):
        body = b'{"events":[]}'
        headers = self.signed_headers(body)

        self.assertTrue(verify_line_signature(body, headers["X-Line-Signature"], "secret"))
        self.assertFalse(verify_line_signature(body, "bad-signature", "secret"))

    def test_webhook_replies_to_message_event(self):
        client = TestClient(app)
        body = (
            b'{"events":[{"type":"message","replyToken":"reply-token",'
            b'"message":{"type":"text","text":"Technical"}}]}'
        )
        response = Mock()
        response.raise_for_status.return_value = None

        with (
            patch.dict(
                "webhook_app.os.environ",
                {
                    "LINE_CHANNEL_SECRET": "secret",
                    "LINE_CHANNEL_ACCESS_TOKEN": "channel-token",
                },
            ),
            patch("webhook_app.requests.post", return_value=response) as mocked_post,
        ):
            result = client.post("/webhook", content=body, headers=self.signed_headers(body))

        self.assertEqual(result.status_code, 200)
        self.assertEqual(mocked_post.call_args.kwargs["json"]["replyToken"], "reply-token")
        self.assertEqual(
            mocked_post.call_args.kwargs["headers"]["Authorization"],
            "Bearer channel-token",
        )

    def test_webhook_rejects_invalid_signature(self):
        client = TestClient(app)

        with patch.dict("webhook_app.os.environ", {"LINE_CHANNEL_SECRET": "secret"}):
            result = client.post(
                "/webhook",
                content=b'{"events":[]}',
                headers={"X-Line-Signature": "bad"},
            )

        self.assertEqual(result.status_code, 403)


if __name__ == "__main__":
    unittest.main()
