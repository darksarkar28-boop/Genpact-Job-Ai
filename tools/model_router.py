"""Provider-neutral workload routing with bounded calls and content-free telemetry.

Adapters must enforce the supplied timeout and output limit at the transport.
Costs are conservative estimates from configured USD per million token rates.
"""
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Callable

FAST = frozenset({"extract", "deduplicate", "rank"})
FRONTIER = frozenset({"draft", "review"})


@dataclass(frozen=True)
class Model:
    provider: str
    name: str
    input_rate: float
    output_rate: float
    timeout: float = 30
    max_output: int = 1024

    def __post_init__(self):
        if not self.provider or not self.name:
            raise ValueError("provider and model required")
        if any(not isfinite(v) or v < 0 for v in (self.input_rate, self.output_rate)):
            raise ValueError("invalid rates")
        if not isfinite(self.timeout) or self.timeout <= 0 or type(self.max_output) is not int or self.max_output <= 0:
            raise ValueError("invalid limits")

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.input_rate + output_tokens * self.output_rate) / 1_000_000


@dataclass(frozen=True)
class Result:
    text: str
    input_tokens: int
    output_tokens: int


class RoutingError(RuntimeError):
    pass


class Router:
    def __init__(self, fast, frontier, adapters, budget, telemetry: Callable = lambda event: None, *, timeouts=None, clock=monotonic):
        self.fast, self.frontier = tuple(fast), tuple(frontier)
        self.adapters = dict(adapters)
        self.budget = dict(budget)
        self.telemetry = telemetry
        self.timeouts = dict(timeouts or {})
        self.clock = clock
        if any(k not in FAST | FRONTIER or isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) or v <= 0 for k, v in self.timeouts.items()):
            raise ValueError("invalid workload timeout")

    def call(self, workload, prompt, input_token_bound):
        if workload not in FAST | FRONTIER:
            raise ValueError("unknown workload")
        if type(input_token_bound) is not int or input_token_bound < 0:
            raise ValueError("invalid input token bound")
        remaining = self.budget.get(workload, 0)
        if not isfinite(remaining) or remaining < 0:
            raise ValueError("invalid budget")
        deadline = self.clock() + self.timeouts[workload] if workload in self.timeouts else None
        for model in self.fast if workload in FAST else self.frontier:
            timeout = model.timeout if deadline is None else min(model.timeout, deadline - self.clock())
            if timeout <= 0:
                break
            adapter = self.adapters.get(model.provider)
            if adapter is None:
                continue
            reserve = model.cost(input_token_bound, model.max_output)
            if reserve > remaining:
                continue
            remaining -= reserve
            event = {"workload": workload, "provider": model.provider, "model": model.name}
            try:
                result = adapter(model.name, prompt, timeout=timeout, max_output=model.max_output)
            except (TimeoutError, ConnectionError):
                # A failed remote request can still be billed. Keep its reservation.
                self.telemetry(dict(event, status="failed", estimated_cost=reserve))
                continue
            except RoutingError:
                self.telemetry(dict(event, status="rejected", estimated_cost=reserve))
                raise
            if deadline is not None and self.clock() > deadline:
                self.telemetry(dict(event, status="deadline_exceeded", estimated_cost=reserve))
                raise RoutingError("workload deadline exceeded")
            if (not isinstance(result, Result)
                    or type(result.input_tokens) is not int or type(result.output_tokens) is not int
                    or not 0 <= result.input_tokens <= input_token_bound
                    or not 0 <= result.output_tokens <= model.max_output):
                self.telemetry(dict(event, status="invalid_usage", estimated_cost=reserve))
                raise RoutingError("provider exceeded declared usage bounds")
            self.telemetry(dict(event, status="ok", input_tokens=result.input_tokens,
                                output_tokens=result.output_tokens,
                                estimated_cost=model.cost(result.input_tokens, result.output_tokens)))
            return result
        raise RoutingError("no affordable available route within workload limits")
