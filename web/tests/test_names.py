"""Two spellings of one place must never become two places."""

from duty_web.names import normalize_key


def test_spellings_of_one_cafe_share_a_key():
    assert (
        normalize_key("Café  Arena A")
        == normalize_key("cafe arena a")
        == normalize_key("CAFÉ ARENA A")
        == "cafe arena a"
    )


def test_a_decomposed_accent_is_the_same_letter():
    # "e" followed by U+0301 COMBINING ACUTE ACCENT, as some keyboards send it.
    assert normalize_key("Café") == normalize_key("Café") == "cafe"


def test_swedish_letters_fold_too():
    assert normalize_key("Mölnlycke Idrottshall") == "molnlycke idrottshall"


def test_none_and_blank_are_empty():
    assert normalize_key(None) == ""
    assert normalize_key("   ") == ""


def test_different_names_stay_different():
    assert normalize_key("Café Arena A") != normalize_key("Café Arena B")
