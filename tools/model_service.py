"""Configured OpenAI-compatible transport for the workload router.

Only server/operator configuration supplies endpoints and credentials.
No requests are made unless the caller explicitly invokes a configured route.
"""
import json
import logging
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from tools.model_router import FAST, FRONTIER, Model, Result, Router, RoutingError

LOG = logging.getLogger("jobai.model_usage")
MAX_RESPONSE = 2 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CompatibleProvider:
    def __init__(self, endpoint, credential, opener=None):
        parts = urlsplit(endpoint)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
            raise ValueError("provider requires an HTTPS endpoint")
        if not credential:
            raise ValueError("provider credential is missing")
        self.endpoint = endpoint
        self.credential = credential
        self.opener = opener or build_opener(NoRedirect())

    def __call__(self, name, prompt, *, timeout, max_output):
        body = json.dumps({"model": name, "messages": [{"role": "user", "content": prompt}],
                           "max_tokens": max_output}).encode("utf-8")
        request = Request(self.endpoint, data=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.credential,
        }, method="POST")
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as exc:
            if exc.code in (408, 429, 500, 502, 503, 504):
                raise ConnectionError("temporary provider failure") from None
            raise RoutingError("provider rejected request") from None
        except (URLError, socket.timeout, OSError):
            raise ConnectionError("provider unavailable") from None
        if len(raw) > MAX_RESPONSE:
            raise RoutingError("provider response too large")
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError()
            answer = choice["message"]["content"]
            if not isinstance(answer, str):
                raise ValueError()
            return Result(answer, data["usage"]["prompt_tokens"], data["usage"]["completion_tokens"])
        except (ValueError, TypeError, IndexError, KeyError):
            raise RoutingError("invalid provider response") from None


def usage_event(event):
    LOG.info("model_usage %s", json.dumps(event, sort_keys=True))


def configured_router(config, environ=None, provider_factory=CompatibleProvider, telemetry=usage_event):
    environ = os.environ if environ is None else environ
    adapters = {}
    for name, provider in config["providers"].items():
        credential = environ.get(provider["credential_env"])
        if credential:
            adapters[name] = provider_factory(provider["endpoint"], credential)
    fast = [Model(**model) for model in config["fast"]]
    frontier = [Model(**model) for model in config["frontier"]]
    if {(m.provider, m.name) for m in fast} & {(m.provider, m.name) for m in frontier}:
        raise ValueError("fast and frontier model lists must be distinct")
    budget = config["budgets_usd"]
    if set(budget) != FAST | FRONTIER or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in budget.values()):
        raise ValueError("configure a budget for each workload")
    return Router(fast, frontier, adapters, budget, telemetry, timeouts=config.get("timeouts_seconds"))


def route_text(workload, prompt, *, environ=None):
    environ = os.environ if environ is None else environ
    raw = environ.get("JOB_AI_MODEL_CONFIG")
    if not raw:
        raise RoutingError("model routing is not configured")
    try:
        router = configured_router(json.loads(raw), environ)
    except (KeyError, TypeError, ValueError):
        raise RoutingError("invalid model routing configuration") from None
    # Conservative byte-based input estimate plus protocol overhead; provider
    # tokenization can vary. Actual usage is checked after the call.
    bound = len(prompt.encode("utf-8")) + 256
    return router.call(workload, prompt, bound).text
