"""Centralised bot configuration reader.

Reads the ``[BOT_CONFIG]`` section from ``config.toml`` and exposes typed
accessor functions.  The TOML file is read once and cached via
``@lru_cache``.
"""

import os
import tomllib
from functools import lru_cache
from typing import List

# Walk up from this file to find config.toml:
#   utils/bot_config.py -> intelligence/ -> qq_social_bot_app/ -> config/config.toml
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(
    _THIS_DIR, os.pardir, os.pardir, 'config', 'config.toml'
)


@lru_cache(maxsize=1)
def _load_bot_config() -> dict:
    path = os.path.normpath(_CONFIG_PATH)
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    return data.get('BOT_CONFIG', {})


# -- typed accessors --------------------------------------------------------

def get_data_root() -> str:
    return os.path.expanduser(_load_bot_config().get('data_root', '~/.qq_bot_data'))


def get_image_cache_dir() -> str:
    return os.path.expanduser(_load_bot_config().get('image_cache_dir', '~/.qq_bot_data/image_cache'))


def get_redis_url() -> str:
    return _load_bot_config().get('redis_url', 'redis://localhost:6379/0')


def get_bot_id() -> str:
    return _load_bot_config().get('bot_id', 'bot_self')


def get_bot_names() -> List[str]:
    return _load_bot_config().get('bot_names', ['米浴', '米宝', '神の人形'])


def get_ws_host() -> str:
    return _load_bot_config().get('ws_host', '127.0.0.1')


def get_ws_port() -> int:
    return int(_load_bot_config().get('ws_port', 8082))


def get_ws_path() -> str:
    return _load_bot_config().get('ws_path', '/ws/onebot')


def get_periodic_check_interval() -> int:
    return int(_load_bot_config().get('periodic_check_interval', 120))


def get_prob_floor() -> float:
    return float(_load_bot_config().get('prob_floor', 0.2))


def get_prob_ceil() -> float:
    return float(_load_bot_config().get('prob_ceil', 0.7))
