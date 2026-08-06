from __future__ import annotations

import pytest

from aletheia.ingestion.parsing import (
    MEDIA_TYPES,
    UnsupportedMediaType,
    detect_media_type,
    parse,
)


def test_detect_media_type_from_extension() -> None:
    assert detect_media_type("policy.pdf") == "application/pdf"
    assert detect_media_type("notes.MD") == "text/markdown"
    assert detect_media_type("page.htm") == "text/html"


def test_declared_media_type_wins_and_drops_parameters() -> None:
    assert detect_media_type("x.bin", "text/html; charset=utf-8") == "text/html"


def test_unknown_extension_is_rejected() -> None:
    with pytest.raises(UnsupportedMediaType):
        detect_media_type("archive.zip")


def test_unsupported_media_type_is_rejected() -> None:
    with pytest.raises(UnsupportedMediaType):
        parse(b"...", media_type="application/zip")


def test_plain_text_takes_its_title_from_the_first_line() -> None:
    parsed = parse(b"Notice Period Policy\n\nThirty days.", media_type="text/plain")
    assert parsed.title == "Notice Period Policy"
    assert parsed.text.startswith("Notice Period Policy")


def test_markdown_keeps_its_markup_and_reads_the_h1() -> None:
    source = b"# Service Agreement\n\nEither party may terminate.\n"
    parsed = parse(source, media_type="text/markdown", filename="sa.md")

    assert parsed.title == "Service Agreement"
    # Headings and list markers are retrieval signal; stripping them would also
    # shift every span relative to what the user sees.
    assert "# Service Agreement" in parsed.text


def test_html_drops_scripts_and_keeps_paragraph_breaks() -> None:
    source = b"""<html><head><title>KVKK</title><style>p{color:red}</style></head>
    <body><script>alert(1)</script><p>Birinci paragraf.</p><p>Ikinci paragraf.</p></body></html>"""
    parsed = parse(source, media_type="text/html", filename="kvkk.html")

    assert parsed.title == "KVKK"
    assert "alert" not in parsed.text
    assert "color:red" not in parsed.text
    # Chunking splits on paragraph boundaries first, so they have to survive.
    assert "\n\n" in parsed.text


def test_turkish_text_survives_cp1254() -> None:
    """Turkish legislation still circulates in cp1254.

    Decoding it as UTF-8 with replacement characters would corrupt exactly the
    terminology the verifier has to match against a claim.
    """
    original = "Sözleşme feshi ihbar süresi otuz gündür."
    parsed = parse(original.encode("cp1254"), media_type="text/plain")
    assert parsed.text == original


def test_utf8_bom_is_stripped() -> None:
    parsed = parse("Başlık\n\nGövde.".encode("utf-8-sig"), media_type="text/plain")
    assert parsed.text.startswith("Başlık")
    assert "﻿" not in parsed.text


def test_whitespace_is_normalised_without_losing_paragraphs() -> None:
    parsed = parse(b"A   \r\n\r\n\r\n\r\nB\r\n", media_type="text/plain")
    assert parsed.text == "A\n\nB"


def test_title_falls_back_to_the_filename() -> None:
    parsed = parse(b"x" * 200, media_type="text/plain", filename="kvkk_metni-2024.txt")
    assert parsed.title == "kvkk metni 2024"


def test_every_declared_extension_has_a_parser() -> None:
    """Guards against adding an extension to MEDIA_TYPES and forgetting the parser."""
    for media_type in set(MEDIA_TYPES.values()):
        try:
            parse(b"", media_type=media_type)
        except UnsupportedMediaType:
            pytest.fail(f"{media_type} is advertised but has no parser")
        except Exception:
            # Empty bytes are not a valid PDF or DOCX; only the routing matters.
            pass
