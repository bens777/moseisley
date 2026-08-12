"""Cantina-themed email templates.

Password reset is the only email Moseisley sends — no verification, no welcome,
no notifications. Anything added here should clear that bar first.

Email clients strip <style> blocks and external CSS, so everything here is
inline styles on table-free block markup, with the .cantina palette hardcoded
(--cw-* has no meaning inside an inbox). Every template returns
(subject, text_body, html_body) — the plain-text part is written by hand, not
derived, because it is what plain-text clients and spam filters actually read.
"""
from __future__ import annotations

from backend.core.config import get_settings

# .cantina palette, frozen as literals for the inbox
BLACK = "#07040f"
PANEL = "#171029"
LINE = "#35284f"
PURPLE = "#a06ef2"
MAGENTA = "#e14fd0"
INK = "#f1eaff"
MUTE = "#b6a9d8"
FAINT = "#7d6fa3"

LOGO_PATH = "/brand/logo-mark.webp"


def _origin() -> str:
    return get_settings().frontend_origin.rstrip("/")


def _shell(heading: str, intro_html: str, cta_label: str, cta_href: str,
           footnote_html: str) -> str:
    """Shared cantina card: deep-space ground, neon panel, one clear CTA."""
    origin = _origin()
    return f"""\
<div style="margin:0;padding:32px 16px;background:{BLACK};
     font-family:ui-sans-serif,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:520px;margin:0 auto;">

    <div style="text-align:center;padding-bottom:24px;">
      <img src="{origin}{LOGO_PATH}" alt="Moseisley" width="56" height="56"
           style="width:56px;height:56px;border-radius:9999px;border:2px solid {PURPLE};" />
      <div style="margin-top:10px;font-size:18px;font-weight:bold;color:{INK};
           letter-spacing:-0.01em;">
        moseisley<span style="color:{MAGENTA};">.sh</span>
      </div>
    </div>

    <div style="background:{PANEL};border:1px solid {LINE};border-radius:26px 10px 26px 10px;
         padding:32px 28px;">
      <h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;font-weight:800;color:{INK};">
        {heading}
      </h1>
      <div style="margin:0 0 24px;font-size:15px;line-height:1.65;color:{MUTE};">
        {intro_html}
      </div>
      <div style="text-align:center;margin:28px 0 8px;">
        <a href="{cta_href}"
           style="display:inline-block;background:{MAGENTA};color:#ffffff;text-decoration:none;
                  padding:14px 32px;border-radius:9999px;font-size:15px;font-weight:bold;
                  letter-spacing:0.02em;">
          {cta_label}
        </a>
      </div>
      <div style="margin-top:20px;font-size:12px;line-height:1.6;color:{FAINT};
           word-break:break-all;">
        {footnote_html}
      </div>
    </div>

    <div style="text-align:center;padding-top:20px;font-size:11px;line-height:1.6;color:{FAINT};">
      Moseisley — a cantina of AI agents.<br>
      Questions? <a href="mailto:{get_settings().support_email}"
         style="color:{FAINT};">{get_settings().support_email}</a>
    </div>

  </div>
</div>"""


def reset_password_email(link: str, ttl_minutes: int) -> tuple[str, str, str]:
    subject = "Your Moseisley reset link 🛸"
    text = (
        "Someone asked to reset the password on your Moseisley account.\n\n"
        f"Open this link to choose a new one — it expires in {ttl_minutes} minutes:\n"
        f"{link}\n\n"
        "If that wasn't you, ignore this email. Your password stays as it is, and "
        "the link dies on its own.\n\n"
        f"— The Cantina · {get_settings().support_email}\n"
    )
    html = _shell(
        heading="Reset your password",
        intro_html=(
            "Someone asked to reset the password on your Moseisley account. "
            "Pick a new one with the button below — the link expires in "
            f"<strong style=\"color:{INK};\">{ttl_minutes} minutes</strong>."
            "<br><br>If that wasn't you, ignore this email: your password stays "
            "as it is, and the link dies on its own."
        ),
        cta_label="Choose a new password",
        cta_href=link,
        footnote_html=f"Button not working? Paste this into your browser:<br>{link}",
    )
    return subject, text, html
