"""HTML rendering of the reminder mail, in the web app's visual language.

Mail clients are not browsers: no external stylesheets, no web fonts, no
flexbox worth relying on. So this is table-based with inline styles, and the
colors are the web app's own tokens hardcoded to their light-theme values —
a mail can't read prefers-color-scheme reliably, and a client that inverts
the page on its own does better with an explicitly light design than with
one that half-declares its own dark mode.

The plain-text body in templates.py stays the primary content; this is the
alternative part.
"""

from __future__ import annotations

from html import escape

BG = "#EEF3EE"
SURFACE = "#FFFFFF"
SURFACE_2 = "#E1EBE1"
INK = "#0E1A14"
INK_DIM = "#4C5D53"
ACCENT = "#0C7A50"
ACCENT_INK = "#FFFFFF"
LINE = "#C3D3C5"

SERIF = "Georgia, 'Times New Roman', serif"
SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "'SF Mono', Consolas, monospace"


def _button(href: str, label: str, *, primary: bool) -> str:
    background = ACCENT if primary else SURFACE
    color = ACCENT_INK if primary else INK
    border = ACCENT if primary else INK
    return (
        f'<a href="{escape(href, quote=True)}" '
        f'style="display:inline-block;background:{background};color:{color};'
        f"border:2px solid {border};border-radius:10px;padding:11px 18px;"
        f'font-family:{SANS};font-size:14px;font-weight:700;text-decoration:none;">'
        f"{escape(label)}</a>"
    )


def render_html(
    *,
    subject: str,
    headline: str,
    when: str,
    names: str,
    intro: str,
    handover: str | None,
    key_location: str | None,
    schedule_link: str | None,
    documents_link: str | None,
) -> str:
    """One reminder, as a self-contained HTML document."""
    rows: list[str] = []

    rows.append(
        f'<tr><td style="padding:0 0 4px;">'
        f'<div style="font-family:{SANS};font-size:11px;font-weight:700;'
        f"letter-spacing:0.08em;text-transform:uppercase;color:{INK_DIM};\">"
        f"{escape(when)}</div></td></tr>"
    )
    rows.append(
        f'<tr><td style="padding:0 0 14px;">'
        f'<div style="font-family:{SERIF};font-size:26px;font-weight:700;'
        f'line-height:1.2;color:{INK};">{escape(headline)}</div></td></tr>'
    )
    rows.append(
        f'<tr><td style="padding:0 0 18px;">'
        f'<div style="font-family:{SANS};font-size:15px;line-height:1.55;color:{INK};">'
        f"{escape(intro)}</div></td></tr>"
    )

    rows.append(
        f'<tr><td style="padding:0 0 16px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        f'style="background:{SURFACE_2};border-radius:12px;">'
        f'<tr><td style="padding:14px 16px;">'
        f'<div style="font-family:{SANS};font-size:10.5px;font-weight:700;'
        f"letter-spacing:0.06em;text-transform:uppercase;color:{INK_DIM};"
        f'padding-bottom:5px;">På passet</div>'
        f'<div style="font-family:{SANS};font-size:15px;font-weight:700;color:{INK};">'
        f"{escape(names)}</div>"
        f"</td></tr></table></td></tr>"
    )

    if key_location:
        rows.append(
            f'<tr><td style="padding:0 0 16px;">'
            f'<div style="font-family:{SANS};font-size:14px;line-height:1.55;color:{INK};">'
            f'<strong style="font-weight:700;">Nycklarna:</strong> '
            f"{escape(key_location)}</div></td></tr>"
        )

    if handover:
        rows.append(
            f'<tr><td style="padding:0 0 16px;">'
            f'<div style="font-family:{SANS};font-size:14px;line-height:1.55;color:{INK_DIM};">'
            f"{escape(handover)}</div></td></tr>"
        )

    buttons = []
    if schedule_link:
        buttons.append(_button(schedule_link, "Öppna schemat", primary=True))
    if documents_link:
        buttons.append(_button(documents_link, "Läs instruktionerna", primary=False))
    if buttons:
        rows.append(
            f'<tr><td style="padding:6px 0 0;">'
            + "".join(f'<span style="padding-right:8px;">{b}</span>' for b in buttons)
            + "</td></tr>"
        )

    body_rows = "".join(rows)
    return f"""<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background:{BG};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:460px;">

<tr><td style="padding:0 0 14px;">
  <span style="display:inline-block;width:28px;height:28px;background:{ACCENT};color:{ACCENT_INK};
    border-radius:8px;text-align:center;font-family:{SERIF};font-size:15px;font-weight:700;
    line-height:28px;vertical-align:middle;">C</span>
  <span style="font-family:{SERIF};font-size:17px;font-weight:700;color:{INK};
    padding-left:8px;vertical-align:middle;">Caféschema</span>
</td></tr>

<tr><td style="background:{SURFACE};border:2px solid {INK};border-radius:18px;padding:22px;">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%">{body_rows}</table>
</td></tr>

<tr><td style="padding:14px 4px 0;">
  <div style="font-family:{MONO};font-size:11px;line-height:1.5;color:{INK_DIM};
    border-top:1px solid {LINE};padding-top:12px;">
    Du får det här mailet för att du står i lagets caféschema.
    Vill du inte ha mail kan du stänga av det under Inställningar.
  </div>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""
