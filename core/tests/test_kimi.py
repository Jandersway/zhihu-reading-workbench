import io
import json
import unittest
from unittest.mock import patch

from zhihu_agent.kimi import KimiClient, KimiError


class KimiResponseTests(unittest.TestCase):
    def test_malformed_response_is_a_controlled_error(self):
        malformed = [None, [], {}, {"choices": "bad"}, {"choices": [None]},
                     {"choices": [{"message": {"tool_calls": "bad"}}]},
                     {"choices": [{"message": {"tool_calls": [None]}}]}]
        for payload in malformed:
            with self.subTest(payload=payload):
                with patch("zhihu_agent.kimi.urllib.request.urlopen",
                           return_value=io.BytesIO(json.dumps(payload).encode())):
                    with self.assertRaises(KimiError):
                        KimiClient("test-placeholder").chat([], [])

    def test_tool_response_and_usage_are_preserved(self):
        payload = {"choices": [{"message": {"tool_calls": [{"id": "call-1",
                   "function": {"name": "get_recommend_feed", "arguments": "{}"}}]}}],
                   "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}}
        with patch("zhihu_agent.kimi.urllib.request.urlopen",
                   return_value=io.BytesIO(json.dumps(payload).encode())):
            self.assertEqual(KimiClient("test-placeholder").chat([], []), payload)
