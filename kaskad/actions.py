"""Carry out the actions modules return (standard library only).

home_assistant() returns an on_action(action) callable for Assistant(on_action=...). The action
{"service": "light.turn_on", "area": "kitchen"} becomes POST /api/services/light/turn_on with the
body {"area_id": "kitchen"}. {"entity": "light.desk"} targets one entity instead; with neither,
every entity of the domain is targeted ({"entity_id": "all"}). Extra service data goes in "data".
Area names in the module data must be the area IDs of your Home Assistant.
"""
import os

from .net import post_json


def home_assistant_request(action, base_url, token):
    """(url, headers, body) for one service call."""
    domain, _, service = str(action.get("service", "")).partition(".")
    if not domain or not service:
        raise ValueError("action needs a service like 'light.turn_on': %r" % (action,))
    body = dict(action.get("data") or {})
    if action.get("area"):
        body.setdefault("area_id", action["area"])
    elif action.get("entity"):
        body.setdefault("entity_id", action["entity"])
    else:
        body.setdefault("entity_id", "all")
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = "Bearer " + token
    return "%s/api/services/%s/%s" % (base_url.rstrip("/"), domain, service), headers, body


def home_assistant(base_url=None, token=None, timeout=5):
    """Send actions to Home Assistant. Reads KASKAD_HA_URL and KASKAD_HA_TOKEN."""
    base_url = base_url or os.environ.get("KASKAD_HA_URL", "http://homeassistant.local:8123")
    token = token if token is not None else os.environ.get("KASKAD_HA_TOKEN", "")

    def on_action(action):
        url, headers, body = home_assistant_request(action, base_url, token)
        post_json(url, headers, body, timeout)

    return on_action
