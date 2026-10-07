"""Creative asset registry + deterministic SVG cards (spec §1: creative assets without computer vision).

GET /assets/creatives                       registry rows (creative_assets schema, provenance SIMULATED)
GET /assets/creatives/{creative_id}.svg     the card: product placeholder + headline + hook + CTA
GET /assets/creatives/{creative_id}_thumb.svg
Cards are a pure function of the creative's catalog attributes, so the same creative always renders the same bytes.
"""

from __future__ import annotations

import hashlib
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from world.catalog import Catalog, Creative
from world.reporting.common import NotSeeded, not_seeded_response, world_of

router = APIRouter(prefix="/assets", tags=["creative-assets"])
SVG = "image/svg+xml"
PALETTE = ["#1f6feb", "#8250df", "#bf3989", "#cf222e", "#bc4c00", "#4d2d00", "#116329", "#0a3069", "#57606a",
           "#953800", "#6639ba", "#1b7c83"]
HOOK_LINE = {
    "discount": "Up to 30% off this week",
    "new arrival": "Just landed: the new season edit",
    "social proof": "Rated 4.6 by 12,000+ customers",
    "benefit": "All-day comfort, built to last",
    "urgency": "Selling fast: limited stock",
}


def _colour(key: str) -> str:
    return PALETTE[int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % len(PALETTE)]


def render_card(cr: Creative, category_label: str, width: int = 600, height: int = 600) -> str:
    accent = _colour(cr.campaign_id)
    headline = escape(category_label)
    hook = escape(HOOK_LINE.get(cr.hook, cr.hook))
    cta = escape(cr.cta)
    badge = escape(cr.format.upper())
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 600 600" '
        f'role="img" aria-label="{headline}: {hook}">'
        f'<rect width="600" height="600" fill="#f6f8fa"/>'
        f'<rect x="40" y="40" width="520" height="330" rx="24" fill="{accent}" opacity="0.12"/>'
        f'<circle cx="300" cy="205" r="110" fill="{accent}" opacity="0.85"/>'
        f'<text x="300" y="215" text-anchor="middle" font-family="Inter, Arial, sans-serif" font-size="28" '
        f'fill="#ffffff">PRODUCT</text>'
        f'<rect x="460" y="56" width="84" height="30" rx="15" fill="#24292f"/>'
        f'<text x="502" y="77" text-anchor="middle" font-family="Inter, Arial, sans-serif" font-size="14" '
        f'fill="#ffffff">{badge}</text>'
        f'<text x="40" y="430" font-family="Inter, Arial, sans-serif" font-size="34" font-weight="700" '
        f'fill="#24292f">{headline}</text>'
        f'<text x="40" y="475" font-family="Inter, Arial, sans-serif" font-size="24" fill="#57606a">{hook}</text>'
        f'<rect x="40" y="510" width="220" height="56" rx="28" fill="{accent}"/>'
        f'<text x="150" y="546" text-anchor="middle" font-family="Inter, Arial, sans-serif" font-size="22" '
        f'font-weight="600" fill="#ffffff">{cta}</text>'
        f'<text x="560" y="590" text-anchor="end" font-family="Inter, Arial, sans-serif" font-size="12" '
        f'fill="#8c959f">SIMULATED creative {escape(cr.creative_id)}</text>'
        f"</svg>"
    )


def _lookup(catalog: Catalog, creative_id: str) -> tuple[Creative, str] | None:
    cr = next((c for c in catalog.creatives if c.creative_id == creative_id), None)
    if cr is None:
        return None
    camp = next(c for c in catalog.campaigns if c.campaign_id == cr.campaign_id)
    cat = next(c for c in catalog.categories if c.code == camp.category_code)
    return cr, cat.label


@router.get("/creatives")
def registry(request: Request) -> JSONResponse:
    try:
        _, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    rows = []
    for cr in sorted(truth.catalog.creatives, key=lambda c: c.creative_id):
        _, label = _lookup(truth.catalog, cr.creative_id)
        rows.append({"creative_asset_id": f"asset-{cr.creative_id}", "creative_id": cr.creative_id,
                     "asset_uri": f"/assets/creatives/{cr.creative_id}.svg", "content_type": SVG,
                     "thumbnail_uri": f"/assets/creatives/{cr.creative_id}_thumb.svg", "format": cr.format,
                     "hook": cr.hook, "cta": cr.cta, "headline_text": label, "provenance": "SIMULATED"})
    return JSONResponse({"creative_assets": rows})


@router.get("/creatives/{name}")
def card(name: str, request: Request) -> Response:
    try:
        _, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    if not name.endswith(".svg"):
        return JSONResponse(status_code=404, content={"detail": "not found"})
    stem = name[:-4]
    thumb = stem.endswith("_thumb")
    found = _lookup(truth.catalog, stem[:-6] if thumb else stem)
    if found is None:
        return JSONResponse(status_code=404, content={"detail": "unknown creative"})
    cr, label = found
    svg = render_card(cr, label, *((160, 160) if thumb else (600, 600)))
    return Response(content=svg, media_type=SVG, headers={"Cache-Control": "public, max-age=86400"})
