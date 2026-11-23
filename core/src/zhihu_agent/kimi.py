from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class KimiError(RuntimeError):
    pass


class KimiClient:
    def __init__(self, api_key: str, base_url: str = "https://api.kimi.com/coding/v1", *, max_tokens: int = 1800):
        if not api_key:
            raise KimiError("KIMI_API_KEY is not configured")
        if not base_url.startswith("https://"):
            raise KimiError("KIMI_BASE_URL must use HTTPS")
        if type(max_tokens) is not int or not 256 <= max_tokens <= 8192:
            raise ValueError("max_tokens must be an integer in [256, 8192]")
        self.api_key = api_key
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.max_tokens = max_tokens

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": "k3",
            "messages": messages,
            "reasoning_effort": "low",
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise KimiError(f"Kimi API returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            cause = getattr(exc, "reason", exc)
            raise KimiError(f"Kimi API request failed: {type(exc).__name__} ({type(cause).__name__})") from None
        except (ValueError, UnicodeError) as exc:
            raise KimiError(f"Kimi API returned invalid JSON: {type(exc).__name__}") from None
        choices = result.get("choices") if isinstance(result, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict):
            raise KimiError("Kimi API returned no assistant message")
        calls = choices[0]["message"].get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list):
                raise KimiError("Kimi API returned invalid tool calls")
            for call in calls:
                function = call.get("function") if isinstance(call, dict) else None
                if (not isinstance(function, dict) or not isinstance(call.get("id"), str) or not call["id"]
                        or not isinstance(function.get("name"), str)
                        or not isinstance(function.get("arguments"), str)):
                    raise KimiError("Kimi API returned invalid tool calls")
        return result
