"""Lettura del CV (docx / pdf / txt) e costruzione del profilo con Claude."""

from __future__ import annotations

import base64
import io
from typing import Any

import docx
from anthropic import AsyncAnthropic

from .models import Profile
from .stats import RunStats

SUPPORTED = (".docx", ".pdf", ".txt", ".md")


def cv_to_content(filename: str, data: bytes) -> tuple[list[dict[str, Any]], str | None]:
    """Converte il file in blocchi di contenuto per Claude. Restituisce anche il testo, se estraibile."""
    name = filename.lower()
    if name.endswith(".docx"):
        document = docx.Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells if c.text.strip()))
        text = "\n".join(parts)
        return [{"type": "text", "text": f"<cv>\n{text}\n</cv>"}], text
    if name.endswith(".pdf"):
        block = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(data).decode(),
            },
        }
        return [block], None
    if name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="replace")
        return [{"type": "text", "text": f"<cv>\n{text}\n</cv>"}], text
    raise ValueError(f"Formato non supportato. Usa uno tra: {', '.join(SUPPORTED)}")


PROFILE_INSTRUCTIONS = """\
Sei un career advisor per professionisti freelance nel digitale.
Analizza il CV qui sopra e costruisci il profilo professionale del candidato, che servirà a cercare \
offerte di lavoro freelance compatibili (remoto o ibrido, Milano/Italia/Europa).

- target_roles: i ruoli più realistici da cercare, dal più pertinente al meno pertinente, \
inclusi eventuali ruoli secondari (es. coordinamento, PM) se il CV li supporta.
- search_keywords_*: query brevi e concrete che un recruiter userebbe negli annunci \
(es. "React Native developer freelance", "sviluppatore frontend partita iva").
- Scrivi summary e target_roles in italiano.
"""


async def build_profile(
    client: AsyncAnthropic,
    model: str,
    cv_content: list[dict[str, Any]],
    preferences: list[str],
    stats: RunStats | None = None,
) -> Profile:
    extra = ""
    if preferences:
        notes = "\n".join(f"- {p}" for p in preferences)
        extra = (
            "\n\nIl candidato ha aggiunto queste indicazioni, che hanno la precedenza sul CV:\n"
            f"<preferenze>\n{notes}\n</preferenze>"
        )
    request = dict(
        model=model,
        max_tokens=16000,
        output_config={"effort": "medium"},
        messages=[
            {
                "role": "user",
                "content": [*cv_content, {"type": "text", "text": PROFILE_INSTRUCTIONS + extra}],
            }
        ],
        output_format=Profile,
    )
    if stats is None:
        response = await client.messages.parse(**request)
    else:
        with stats.phase("Analisi CV", model) as ph:
            response = await client.messages.parse(**request)
            ph.add_usage(response.usage)
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError("Non sono riuscito ad analizzare il CV. Riprova o usa un altro formato.")
    return response.parsed_output
