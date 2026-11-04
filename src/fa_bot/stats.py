"""Contabilità di un giro di ricerca: tempi, token, costi e imbuto di selezione."""

from __future__ import annotations

import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

# USD per milione di token: (input, output, lettura da cache).
# La scrittura in cache (5 minuti) costa 1,25 × il prezzo di input.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-fable-5-1": (10.0, 50.0, 0.25),
}
DEFAULT_PRICES = PRICES["claude-opus-5-5"]  # modello non in tabella: stima prudente
CACHE_WRITE_MULTIPLIER = 1.25
WEB_SEARCH_USD = 10 / 1000  # 10 $ ogni 1000 ricerche; web fetch non ha costo aggiuntivo


@dataclass
class Usage:
    input: int = 0  # token di input non provenienti dalla cache
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0  # include i token di ragionamento
    web_searches: int = 0
    web_fetches: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input + self.cache_write + self.cache_read + self.output

    def __iadd__(self, other: Usage) -> Usage:
        self.input += other.input
        self.cache_write += other.cache_write
        self.cache_read += other.cache_read
        self.output += other.output
        self.web_searches += other.web_searches
        self.web_fetches += other.web_fetches
        return self


@dataclass
class Phase:
    label: str
    model: str | None = None
    seconds: float = 0.0
    usage: Usage = field(default_factory=Usage)

    def add_usage(self, u: Any) -> None:
        """Somma l'oggetto `usage` di una risposta dell'API."""
        self.usage.input += getattr(u, "input_tokens", 0) or 0
        self.usage.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.usage.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.usage.output += getattr(u, "output_tokens", 0) or 0
        server = getattr(u, "server_tool_use", None)
        if server is not None:
            self.usage.web_searches += getattr(server, "web_search_requests", 0) or 0
            self.usage.web_fetches += getattr(server, "web_fetch_requests", 0) or 0

    @property
    def token_cost_usd(self) -> float:
        if not self.model:
            return 0.0
        inp, out, read = PRICES.get(self.model, DEFAULT_PRICES)
        u = self.usage
        return (
            u.input * inp + u.cache_write * inp * CACHE_WRITE_MULTIPLIER + u.cache_read * read + u.output * out
        ) / 1_000_000

    @property
    def search_fee_usd(self) -> float:
        return self.usage.web_searches * WEB_SEARCH_USD

    @property
    def cost_usd(self) -> float:
        return self.token_cost_usd + self.search_fee_usd


@dataclass
class SourceStat:
    scanned: int = 0  # annunci letti dalla fonte
    relevant: int = 0  # quelli pertinenti al profilo
    error: str | None = None


@dataclass
class RunStats:
    usd_eur: float = 0.92
    started: float = field(default_factory=time.monotonic)
    elapsed: float = 0.0
    phases: list[Phase] = field(default_factory=list)
    sources: dict[str, SourceStat] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)

    @contextmanager
    def phase(self, label: str, model: str | None = None) -> Iterator[Phase]:
        ph = Phase(label, model)
        t0 = time.monotonic()
        try:
            yield ph
        finally:
            ph.seconds = time.monotonic() - t0
            self.phases.append(ph)

    def phase_named(self, label: str) -> Phase | None:
        return next((p for p in self.phases if p.label == label), None)

    def finish(self) -> None:
        self.elapsed = time.monotonic() - self.started

    @property
    def usage(self) -> Usage:
        total = Usage()
        for p in self.phases:
            total += p.usage
        return total

    @property
    def token_cost_usd(self) -> float:
        return sum(p.token_cost_usd for p in self.phases)

    @property
    def search_fee_usd(self) -> float:
        return sum(p.search_fee_usd for p in self.phases)

    @property
    def cost_usd(self) -> float:
        return self.token_cost_usd + self.search_fee_usd

    def eur(self, usd: float) -> float:
        return usd * self.usd_eur

    @property
    def cost_eur(self) -> float:
        return self.eur(self.cost_usd)
