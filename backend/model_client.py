"""Shared Chat Completions transport for the workbench's tool-calling agents."""
from dataclasses import dataclass, field
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from zhihu_agent.kimi import KimiError


@dataclass(frozen=True)
class Provider:
    label: str
    base_url: str
    default_model: str = ''
    token_parameter: str = 'max_tokens'
    parameters: dict = field(default_factory=dict)


PROVIDERS = {
    'kimi': Provider('Kimi Coding', 'https://api.kimi.com/coding/v1', 'k3', parameters={'reasoning_effort': 'low'}),
    'openai': Provider('OpenAI', 'https://api.openai.com/v1', token_parameter='max_completion_tokens'),
    'deepseek': Provider('DeepSeek', 'https://api.deepseek.com', 'deepseek-flash',
                         parameters={'thinking': {'type': 'disabled'}}),
    'qwen': Provider('通义千问 · 百炼北京', 'https://dashscope.aliyuncs.com/compatible-mode/v1'),
    'gemini': Provider('Google Gemini', 'https://generativelanguage.googleapis.com/v1beta/openai'),
    'claude': Provider('Anthropic Claude（兼容接口）', 'https://api.anthropic.com/v1'),
    'openrouter': Provider('OpenRouter', 'https://openrouter.ai/api/v1'),
}


def providers():
    result = dict(PROVIDERS)
    custom = os.environ.get('ZHIHU_WEB_COMPATIBLE_BASE_URL', '').strip().rstrip('/')
    if custom:
        try:
            parsed = urlsplit(custom)
            if (parsed.scheme == 'https' and parsed.hostname and parsed.username is None
                    and not parsed.query and not parsed.fragment):
                result['compatible'] = Provider('部署方配置的兼容服务', custom)
        except ValueError:
            pass
    return result


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    api_key: str = field(repr=False)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ModelClient:
    def __init__(self, config, *, max_tokens=1800):
        provider = providers().get(config.provider) if config is not None else None
        if provider is None:
            raise KimiError('Model provider is unavailable')
        self.config = config
        self.provider = provider
        self.max_tokens = max_tokens
        self.opener = build_opener(NoRedirect())

    def chat(self, messages, tools):
        payload = {'model': self.config.model, 'messages': messages,
                   self.provider.token_parameter: self.max_tokens, **self.provider.parameters}
        if tools:
            payload.update(tools=tools, tool_choice='auto')
        request = Request(self.provider.base_url + '/chat/completions',
                          data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                          headers={'Authorization': 'Bearer ' + self.config.api_key, 'Content-Type': 'application/json'},
                          method='POST')
        try:
            with self.opener.open(request, timeout=90) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise KimiError(f'Model API returned HTTP {exc.code}') from None
        except (URLError, OSError, ValueError):
            raise KimiError('Model API connection or response failed') from None

        choices = result.get('choices') if isinstance(result, dict) else None
        message = choices[0].get('message') if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise KimiError('Model response has no assistant message')
        calls = message.get('tool_calls')
        if calls is not None:
            if not isinstance(calls, list):
                raise KimiError('Model returned invalid tool calls')
            for call in calls:
                function = call.get('function') if isinstance(call, dict) else None
                if (not isinstance(function, dict) or not isinstance(call.get('id'), str) or not call['id']
                        or not isinstance(function.get('name'), str) or not isinstance(function.get('arguments'), str)):
                    raise KimiError('Model returned invalid tool calls')
        return result
