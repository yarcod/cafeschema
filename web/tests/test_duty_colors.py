"""Duty colors come from a hash of the duty's name, not a fixed category list."""


def _chip(app, name):
    return app.jinja_env.filters["chip_class"](name)


def _dot(app, name):
    return app.jinja_env.filters["dot_class"](name)


def test_same_duty_name_always_gets_the_same_color(app):
    assert _chip(app, "Bästkustcupen") == _chip(app, "Bästkustcupen")
    assert _dot(app, "Bästkustcupen") == _dot(app, "Bästkustcupen")


def test_color_ignores_case_and_surrounding_whitespace(app):
    assert _chip(app, "  bästkustcupen ") == _chip(app, "Bästkustcupen")


def test_chip_and_dot_use_the_same_bucket_for_one_duty(app):
    chip = _chip(app, "Arena värdskap höst")
    dot = _dot(app, "Arena värdskap höst")
    assert chip.removeprefix("chip chip-") == dot.removeprefix("dot dot-")


def test_every_duty_name_maps_to_a_defined_palette_class(app):
    css = (
        __import__("pathlib").Path(app.root_path) / "static" / "style.css"
    ).read_text(encoding="utf-8")
    names = [
        "Bästkustcupen", "Cafépass", "Arena värdskap höst", "Arena värdskap vinter",
        "Åby Julmarknad", "Bemanning Gothia", "",
    ]
    for name in names:
        for class_name in _chip(app, name).split() + _dot(app, name).split():
            assert f".{class_name} {{" in css
