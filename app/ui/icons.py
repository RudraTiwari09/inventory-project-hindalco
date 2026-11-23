"""
Self-contained inline SVG icon library for Recon Dashboard.
Zero external CDN, font, or network dependencies (air-gapped plant network safe).
"""

from typing import Dict

_SVG_PATHS: Dict[str, str] = {
    "upload": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />'
        '<polyline points="17 8 12 3 7 8" />'
        '<line x1="12" y1="3" x2="12" y2="15" />'
    ),
    "analytics": (
        '<line x1="18" y1="20" x2="18" y2="10" />'
        '<line x1="12" y1="20" x2="12" y2="4" />'
        '<line x1="6" y1="20" x2="6" y2="14" />'
    ),
    "check": (
        '<polyline points="20 6 9 17 4 12" />'
    ),
    "lock": (
        '<rect x="3" y="11" width="18" height="11" rx="2" ry="2" />'
        '<path d="M7 11V7a5 5 0 0 1 10 0v4" />'
    ),
    "inventory": (
        '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />'
        '<polyline points="3.27 6.96 12 12.01 20.73 6.96" />'
        '<line x1="12" y1="22.08" x2="12" y2="12" />'
    ),
    "permit": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />'
        '<polyline points="14 2 14 8 20 8" />'
        '<line x1="16" y1="13" x2="8" y2="13" />'
        '<line x1="16" y1="17" x2="8" y2="17" />'
        '<polyline points="10 9 9 9 8 9" />'
    ),
    "back": (
        '<line x1="19" y1="12" x2="5" y2="12" />'
        '<polyline points="12 19 5 12 12 5" />'
    ),
    "logout": (
        '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />'
        '<polyline points="16 17 21 12 16 7" />'
        '<line x1="21" y1="12" x2="9" y2="12" />'
    ),
    "warning": (
        '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />'
        '<line x1="12" y1="9" x2="12" y2="13" />'
        '<line x1="12" y1="17" x2="12.01" y2="17" />'
    ),
    "clock": (
        '<circle cx="12" cy="12" r="10" />'
        '<polyline points="12 6 12 12 16 14" />'
    ),
    "download": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />'
        '<polyline points="7 10 12 15 17 10" />'
        '<line x1="12" y1="15" x2="12" y2="3" />'
    ),
    "user": (
        '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />'
        '<circle cx="12" cy="7" r="4" />'
    ),
    "cross": (
        '<line x1="18" y1="6" x2="6" y2="18" />'
        '<line x1="6" y1="6" x2="18" y2="18" />'
    ),
}


def icon(
    name: str,
    size: int = 20,
    color: str = "currentColor",
    extra_style: str = "",
) -> str:
    """
    Generate an inline SVG element for the requested icon name.
    
    Parameters
    ----------
    name : str
        Icon name (e.g. 'upload', 'analytics', 'check', 'back', 'lock')
    size : int
        Width and height in pixels (default 20)
    color : str
        Stroke color (default 'currentColor')
    extra_style : str
        Additional CSS rules to inject into style attribute

    Returns
    -------
    str
        Complete inline <svg> HTML string.
    """
    path_data = _SVG_PATHS.get(name.lower(), _SVG_PATHS["check"])
    style_attr = f"vertical-align: middle; display: inline-block; {extra_style}".strip()
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" style="{style_attr}">'
        f'{path_data}'
        f'</svg>'
    )

