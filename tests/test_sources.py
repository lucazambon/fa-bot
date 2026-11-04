from datetime import datetime, timedelta, timezone

from fa_bot.search import _is_employee_only
from fa_bot.sources import Posting, is_recent, matches_terms, parse_iprog_detail, parse_iprog_feed

FEED = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<item><title><![CDATA[React Native Developer]]></title>
<link>https://www.iprogrammatori.it/lavoro/ricerca_x_1.aspx</link>
<dc:creator><![CDATA[Jumpit S.r.l]]></dc:creator>
<pubDate>Wed, 04 Nov 2026 10:53:24 GMT</pubDate>
<description><![CDATA[<p>Cerchiamo <strong>React</strong> dev</p>]]></description></item>
</channel></rss>"""

DETAIL = """<html><body><h1>Offerta</h1> Aggiornato il 29/10/2026 Ruolo Dev Luogo lavoro Remoto
Modalità di lavoro Remote Working Impegno Full-time Retribuzione (lorda) &euro; 200 - &euro; 300 al giorno
Posti 1 Tipo di contratto Partita Iva Determinato Descrizione dell'annuncio di lavoro Progetto React Native.</body></html>"""


def test_parse_feed():
    [p] = parse_iprog_feed(FEED)
    assert (p.title, p.company) == ("React Native Developer", "Jumpit S.r.l")
    assert p.published.year == 2026 and "React" in p.description


def test_parse_detail():
    details, desc = parse_iprog_detail(DETAIL)
    assert details["luogo"] == "Remoto"
    assert details["retribuzione"] == "€ 200 - € 300 al giorno"
    assert details["contratto"] == "Partita Iva Determinato"
    assert desc.startswith("Progetto React Native")


def test_matches_terms_word_boundary():
    p = Posting(source="x", title="Senior Reactor Engineer", company="c", url="u")
    assert not matches_terms(p, ["react"])
    p.title = "React/Next.js developer"
    assert matches_terms(p, ["next.js"]) and matches_terms(p, ["react"])


def test_recent_and_employee_filter():
    old = Posting(source="x", title="t", company="c", url="u",
                  published=datetime.now(timezone.utc) - timedelta(days=40))
    assert not is_recent(old)
    assert _is_employee_only(Posting(source="x", title="t", company="c", url="u", details={"contratto": "Indeterminato"}))
    assert not _is_employee_only(Posting(source="x", title="t", company="c", url="u", details={"contratto": "Partita Iva Indeterminato"}))
    assert not _is_employee_only(Posting(source="x", title="t", company="c", url="u", details={"contratto": "Da determinare"}))
