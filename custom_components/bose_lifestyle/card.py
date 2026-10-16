"""Serve the Bose card file and register it with the frontend once."""
import json
import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)
URL_PATH = '/bose_lifestyle_static'
HERE = Path(__file__).parent


def _version():
    return json.loads((HERE / 'manifest.json').read_text())['version']


async def async_register_card(hass):
    http = getattr(hass, 'http', None)
    if http is None:
        _LOGGER.warning('HTTP component not loaded; the Bose card was not registered')
        return
    try:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig
    except ImportError:
        _LOGGER.warning('Frontend component not available; the Bose card was not registered')
        return
    version = await hass.async_add_executor_job(_version)
    await http.async_register_static_paths([StaticPathConfig(URL_PATH, str(HERE / 'frontend'), False)])
    add_extra_js_url(hass, f'{URL_PATH}/bose-control-card.js?v={version}')
