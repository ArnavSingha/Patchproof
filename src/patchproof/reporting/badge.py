"""Dynamic SVG badge generator for PatchProof verification attestation."""

from __future__ import annotations

from typing import NamedTuple

from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    PatchVerdict,
)


class BadgeStyle(NamedTuple):
    """Visual style attributes for an SVG badge."""

    status_text: str
    bg_color: str
    right_width: int


VERDICT_BADGE_CONFIG: dict[PatchVerdict, BadgeStyle] = {
    PatchVerdict.ACCEPTED: BadgeStyle("verified", "#2ea44f", 64),
    PatchVerdict.REJECTED: BadgeStyle("rejected", "#cb2431", 64),
    PatchVerdict.SUSPICIOUS: BadgeStyle("suspicious", "#dfb317", 74),
    PatchVerdict.INFRASTRUCTURE_FAILURE: BadgeStyle("infra failure", "#959da5", 84),
}


def generate_svg_badge(result: ComparativeExecutionResult) -> str:
    """Render a standalone, GitHub-compatible SVG badge reflecting verification verdict."""
    style = VERDICT_BADGE_CONFIG.get(
        result.verdict,
        BadgeStyle(result.verdict.value, "#959da5", 64),
    )

    left_label = "patchproof"
    left_width = 72
    right_width = style.right_width
    total_width = left_width + right_width
    height = 20

    label_x = left_width / 2.0
    status_x = left_width + (right_width / 2.0)
    text_y = 14
    bg_color = style.bg_color
    status_text = style.status_text

    font_family = "Verdana,Geneva,DejaVu Sans,sans-serif"
    header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="{height}" '
        f'role="img" aria-label="{left_label}: {status_text}">'
    )
    group_text = (
        f'  <g fill="#fff" text-anchor="middle" font-family="{font_family}" '
        'text-rendering="geometricPrecision" font-size="11">'
    )
    left_shadow = (
        f'    <text aria-hidden="true" x="{label_x}" y="{text_y + 1}" '
        f'fill="#010101" fill-opacity=".3">{left_label}</text>'
    )
    right_shadow = (
        f'    <text aria-hidden="true" x="{status_x}" y="{text_y + 1}" '
        f'fill="#010101" fill-opacity=".3">{status_text}</text>'
    )

    return f"""{header}
  <title>{left_label}: {status_text}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="{total_width}" height="{height}" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{left_width}" height="{height}" fill="#555"/>
    <rect x="{left_width}" width="{right_width}" height="{height}" fill="{bg_color}"/>
    <rect width="{total_width}" height="{height}" fill="url(#s)"/>
  </g>
{group_text}
{left_shadow}
    <text x="{label_x}" y="{text_y}">{left_label}</text>
{right_shadow}
    <text x="{status_x}" y="{text_y}">{status_text}</text>
  </g>
</svg>"""
