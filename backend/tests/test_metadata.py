"""Generated titles and descriptions are cleaned before they reach a platform."""

from __future__ import annotations

from clipforge.media.metadata import local_copy, one_line, youtube_safe


def test_model_output_is_reduced_to_one_clean_title_line():
    # Real output from a 1B local model: title and description run together.
    assert one_line("Raising prices without losing trust\n\nHow to price your business correctly") == \
        "Raising prices without losing trust"
    assert one_line('**Title:** "Never compete on price"') == "Never compete on price"
    assert one_line("\n  - caption:  Hello   world ") == "Hello world"
    assert one_line("") == ""


def test_youtube_forbidden_characters_are_replaced():
    assert youtube_safe("Price <right> now") == "Price ‹right› now"
    copy = local_copy("Use <b>bold</b> captions > plain ones.", "Use <b>bold</b> captions")
    assert "<" not in copy.youtube_title and ">" not in copy.youtube_title
    assert "<" not in copy.youtube_description and ">" not in copy.youtube_description
    assert copy.youtube_title.endswith("#shorts") and len(copy.youtube_title) <= 100
