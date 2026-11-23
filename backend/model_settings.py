"""Account-owned API keys for external model calls."""
import os
import re
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .model_client import ModelConfig, providers
from .windows_storage import WindowsProtectedFile

router = APIRouter(prefix='/settings/model', tags=['model settings'])


class ModelKey(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api_key: SecretStr = Field(min_length=1, max_length=512)
    provider: str = Field(default='kimi', max_length=32)
    model: str = Field(default='', max_length=160)


class AccountModelKeys:
    def __init__(self, root):
        self.root = root
        self.lock = Lock()
        self.memory_keys = {}

    def get(self, account):
        with self.lock:
            if os.name != 'nt':
                data = self.memory_keys.get(account, {})
            else:
                try:
                    data = WindowsProtectedFile(self.root / account / 'model.dpapi').read()
                except (OSError, ValueError):
                    raise HTTPException(503, '无法读取本机的模型设置。') from None
        if not isinstance(data, dict):
            return None
        provider_id = data.get('provider', 'kimi')
        provider = providers().get(provider_id)
        key = data.get('api_key') or data.get('kimi')
        if not provider or not isinstance(key, str) or not key:
            return None
        if provider_id == 'compatible' and data.get('endpoint') != provider.base_url:
            return None
        return ModelConfig(provider_id, data.get('model') or provider.default_model, key)

    def set(self, account, config):
        data = {}
        if config:
            data = {'provider': config.provider, 'model': config.model, 'api_key': config.api_key,
                    'endpoint': providers()[config.provider].base_url}
        with self.lock:
            if os.name == 'nt':
                try:
                    WindowsProtectedFile(self.root / account / 'model.dpapi').write(data)
                except OSError:
                    raise HTTPException(503, '无法保存本机的模型设置。') from None
            elif data:
                self.memory_keys[account] = data
            else:
                self.memory_keys.pop(account, None)


def current_account(request):
    account = request.state.account_key
    if not account:
        raise HTTPException(401, '请先登录工作台，再管理自己的模型 API Key。')
    return account


def model_status(request, config):
    allowed = request.app.state.live_allowed
    return {'configured': config is not None, 'live_allowed': allowed, 'live_enabled': allowed and config is not None,
            'provider': config.provider if config else None, 'model': config.model if config else None,
            'providers': [{'id': key, 'label': option.label, 'default_model': option.default_model,
                           'host': option.base_url.split('/')[2]} for key, option in providers().items()]}


def require_live_model(request, consent):
    if not request.app.state.live_allowed or not consent:
        raise HTTPException(403, '请确认本次会向你配置的模型发送问题和相关原文。')
    account = request.state.account_key
    config = request.app.state.model_keys.get(account) if account else None
    if config is None:
        raise HTTPException(403, '请先在「模型设置」中配置自己的模型和 API Key。')
    return config


@router.get('')
def get_model_settings(request: Request):
    account = current_account(request)
    return model_status(request, request.app.state.model_keys.get(account))


@router.put('')
def set_model_key(payload: ModelKey, request: Request):
    account = current_account(request)
    key = payload.api_key.get_secret_value().strip()
    if not key or any(not 33 <= ord(char) <= 126 for char in key):
        raise HTTPException(422, '请输入单行 API Key。')
    provider = providers().get(payload.provider)
    if not provider:
        raise HTTPException(422, '请选择已配置的模型提供商。')
    model = payload.model.strip() or provider.default_model
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:/@+\-]{0,159}', model):
        raise HTTPException(422, '请输入该提供商支持工具调用的模型 ID。')
    config = ModelConfig(payload.provider, model, key)
    request.app.state.model_keys.set(account, config)
    return model_status(request, config)


@router.delete('')
def delete_model_key(request: Request):
    request.app.state.model_keys.set(current_account(request), None)
    return model_status(request, None)
