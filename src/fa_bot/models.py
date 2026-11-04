"""Modelli dati condivisi (usati anche come schema per le risposte strutturate di Claude)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Profile(BaseModel):
    """Profilo professionale estratto dal CV."""

    full_name: str
    headline: str = Field(description="Titolo professionale sintetico")
    years_experience: int
    summary: str = Field(description="2-3 frasi sul profilo, in italiano")
    target_roles: list[str] = Field(description="Ruoli freelance per cui candidarsi, dal più al meno pertinente")
    core_skills: list[str]
    secondary_skills: list[str]
    sectors: list[str] = Field(description="Settori/industrie in cui ha esperienza")
    languages: list[str]
    search_keywords_it: list[str] = Field(description="Parole chiave di ricerca in italiano")
    search_keywords_en: list[str] = Field(description="Parole chiave di ricerca in inglese")
    match_terms: list[str] = Field(
        description="10-25 termini brevi (1-2 parole, minuscolo) che, se presenti nel titolo o nel testo "
        "di un annuncio, lo rendono potenzialmente pertinente: tecnologie principali e nomi di ruolo "
        "(es. 'react', 'frontend', 'front-end', 'scrum master'). Evita termini generici come 'developer'."
    )


CompanyType = Literal["agenzia_comunicazione", "media_grande_impresa", "consulenza", "altro"]
Area = Literal["milano", "italia", "europa", "altro"]
WorkMode = Literal["remoto", "ibrido", "in_sede", "non_indicato"]
DurationType = Literal["breve", "media", "lunga", "non_indicata"]


class Job(BaseModel):
    title: str
    company: str
    company_type: CompanyType
    location: str = Field(description="Città/paese come indicato nell'annuncio")
    area: Area
    work_mode: WorkMode
    duration_type: DurationType = Field(description="breve: fino a 3 mesi; media: 3-6 mesi; lunga: oltre 6 mesi")
    duration_text: Optional[str] = Field(description="Durata come scritta nell'annuncio, se presente")
    daily_rate_min: Optional[int] = Field(description="Rate giornaliero minimo in EUR, se indicato o deducibile")
    daily_rate_max: Optional[int] = Field(description="Rate giornaliero massimo in EUR, se indicato o deducibile")
    rate_text: Optional[str] = Field(description="Compenso come scritto nell'annuncio, se presente")
    match_score: int = Field(description="Compatibilità con il profilo, da 0 a 100")
    match_reason: str = Field(description="Una frase in italiano sul perché è compatibile")
    url: str = Field(description="Link diretto all'annuncio")
    source: str = Field(description="Sito su cui è pubblicato l'annuncio")
    posted_date: Optional[str] = Field(description="Data di pubblicazione, se nota (YYYY-MM-DD)")
    verified: bool = Field(description="true se la pagina dell'annuncio è stata aperta e letta durante la ricerca")


class JobList(BaseModel):
    jobs: list[Job]
