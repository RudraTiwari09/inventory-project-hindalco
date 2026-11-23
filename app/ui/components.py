"""
Reusable UI components for Recon Dashboard.
Consistent with the sleek dark data-first design system.
"""

from pathlib import Path
import re
from typing import Any, Callable, Optional, Union
import html

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.ui.theme import (
    ACCENT_AMBER,
    ACCENT_BLUE,
    ACCENT_CORAL,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_PURPLE,
    COLOR_BG,
    COLOR_CARD,
    COLOR_CARD_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
    FONT_FAMILY,
    HEADER_GRADIENT,
)


def inject_css() -> None:
    """Inject global CSS styles into the current Streamlit app page."""
    css_path = Path(__file__).parent / "styles.css"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def page_header(title: str, subtitle: Optional[str] = None) -> None:
    """
    Render the gradient rounded banner header.

    Parameters
    ----------
    title : str
        Page title (displayed prominently on left).
    subtitle : str, optional
        Short description (max 12 words per copy guidelines).
    """
    safe_title = html.escape(title)
    safe_subtitle = html.escape(subtitle) if subtitle else ""

    sub_html = (
        f'<p class="page-header-subtitle">{safe_subtitle}</p>'
        if safe_subtitle
        else ""
    )

    banner_html = (
        f'<div class="page-header-banner">'
        f'<h1 class="page-header-title">{safe_title}</h1>'
        f'{sub_html}'
        f'</div>'
    )
    if hasattr(st, "html"):
        st.html(banner_html)
    else:
        st.markdown(banner_html, unsafe_allow_html=True)


def kpi_card(
    label: str,
    value: Union[str, int, float],
    delta: Optional[str] = None,
    accent_color: str = ACCENT_GREEN,
    icon: Optional[str] = None,
) -> None:
    """
    Render a single metric card with a colored accent strip on the left.

    Parameters
    ----------
    label : str
        Small uppercase metric label (max 3 words).
    value : str, int, float
        Main value to display.
    delta : str, optional
        Muted subtitle or trend description.
    accent_color : str
        Hex color for the left accent strip.
    icon : str, optional
        Material icon or short symbol indicator.
    """
    safe_label = html.escape(str(label))
    safe_value = html.escape(str(value))
    safe_delta = html.escape(str(delta)) if delta else ""
    safe_icon = html.escape(str(icon)) if icon else ""

    delta_html = f'<div class="kpi-delta">{safe_delta}</div>' if safe_delta else ""
    icon_html = f'<span style="color: {accent_color};">{safe_icon}</span>' if safe_icon else ""

    card_html = (
        f'<div class="kpi-card">'
        f'<div class="kpi-accent-bar" style="background-color: {accent_color};"></div>'
        f'<div class="kpi-label"><span>{safe_label}</span>{icon_html}</div>'
        f'<div class="kpi-value">{safe_value}</div>'
        f'{delta_html}'
        f'</div>'
    )
    if hasattr(st, "html"):
        st.html(card_html)
    else:
        st.markdown(card_html, unsafe_allow_html=True)


def apply_chart_theme(fig: go.Figure, default_color: str = ACCENT_GREEN) -> go.Figure:
    """
    Apply standard sleek dark theme to a Plotly figure.

    - Transparent background
    - Inter font
    - Faint dotted gridlines
    - Green/accent bars
    - No toolbar, no legend clutter
    """
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color=COLOR_TEXT, size=12),
        margin=dict(l=10, r=10, t=30, b=20),
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(30, 74, 82, 0.35)",
            gridwidth=1,
            griddash="dot",
            linecolor="rgba(30, 74, 82, 0.6)",
            tickfont=dict(color=COLOR_TEXT_MUTED, size=11),
            title_font=dict(color=COLOR_TEXT_MUTED, size=12),
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(30, 74, 82, 0.35)",
            gridwidth=1,
            griddash="dot",
            linecolor="rgba(30, 74, 82, 0.6)",
            tickfont=dict(color=COLOR_TEXT_MUTED, size=11),
            title_font=dict(color=COLOR_TEXT_MUTED, size=12),
        ),
        showlegend=False,
    )

    # Set default bar trace colors if not specified
    for trace in fig.data:
        if trace.type == "bar":
            if not getattr(trace, "marker", None) or trace.marker.color is None:
                trace.marker.color = default_color
                trace.marker.line = dict(color="rgba(0,0,0,0)", width=0)

    return fig


def gauge_card(
    title: str,
    value: Optional[float],
    max_val: float = 100.0,
    accent_color: str = ACCENT_GREEN,
    format_str: str = "{:.0f}%",
    key: Optional[str] = None,
) -> None:
    """
    Render a semi-circle gauge (Plotly Indicator) inside a card.

    Parameters
    ----------
    title : str
        Metric title above gauge.
    value : float or None
        Current gauge value. None displays muted "-".
    max_val : float
        Maximum range of the gauge.
    accent_color : str
        Hex color for the gauge fill.
    format_str : str
        Formatting string for gauge number.
    key : str, optional
        Unique key for the Plotly chart element.
    """
    is_loaded = value is not None

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=float(value) if is_loaded else 0.0,
            number=dict(
                font=dict(
                    family="Inter, sans-serif",
                    size=24,
                    color=COLOR_TEXT if is_loaded else COLOR_TEXT_MUTED,
                    weight="bold",
                ),
                suffix="%" if is_loaded else "",
                valueformat=".0f" if is_loaded else "",
            ),
            gauge=dict(
                axis=dict(range=[0, max_val], visible=False),
                bar=dict(
                    color=accent_color if is_loaded else "rgba(30, 74, 82, 0.3)",
                    thickness=0.22,
                ),
                bgcolor="rgba(30, 74, 82, 0.35)",
                borderwidth=0,
                steps=[dict(range=[0, max_val], color="rgba(30, 74, 82, 0.25)")],
            ),
        )
    )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=10, b=0),
        height=100,
    )

    num_display = format_str.format(value) if is_loaded else "—"

    card_html = (
        f'<div class="kpi-card" style="padding-bottom: 6px;">'
        f'<div class="kpi-accent-bar" style="background-color: {accent_color};"></div>'
        f'<div class="kpi-label"><span>{html.escape(title)}</span></div>'
        f'<div style="display: flex; justify-content: space-between; align-items: baseline;">'
        f'<div class="kpi-value" style="color: {COLOR_TEXT if is_loaded else COLOR_TEXT_MUTED};">{num_display}</div>'
        f'</div>'
        f'</div>'
    )
    if hasattr(st, "html"):
        st.html(card_html)
    else:
        st.markdown(card_html, unsafe_allow_html=True)
    gauge_key = key or f"gauge_{re.sub(r'[^a-zA-Z0-9_]', '_', title.lower())}"
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=gauge_key)


def chart_card(
    title: str,
    fig: go.Figure,
    key: Optional[str] = None,
) -> None:
    """
    Render a rounded bordered container with a title and a transparent Plotly chart.

    Parameters
    ----------
    title : str
        Header text for the chart.
    fig : go.Figure
        Plotly figure to render.
    key : str, optional
        Unique key for the Plotly chart element.
    """
    apply_chart_theme(fig)
    safe_title = html.escape(title)

    container_html = (
        f'<div class="chart-card-container">'
        f'<div class="chart-card-title">{safe_title}</div>'
        f'</div>'
    )
    if hasattr(st, "html"):
        st.html(container_html)
    else:
        st.markdown(container_html, unsafe_allow_html=True)
    chart_key = key or f"chart_{re.sub(r'[^a-zA-Z0-9_]', '_', title.lower())}"
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=chart_key)


def empty_state(
    icon: str,
    message: str,
    button_text: Optional[str] = None,
    button_key: Optional[str] = None,
    on_click: Optional[Callable] = None,
) -> bool:
    """
    Render a centered, minimal empty state.

    Parameters
    ----------
    icon : str
        Icon character or Material icon name.
    message : str
        Single line message explaining state.
    button_text : str, optional
        Action button text.
    button_key : str, optional
        Widget key for button.
    on_click : callable, optional
        Callback for button click.

    Returns
    -------
    bool
        True if the button was clicked.
    """
    from app.ui.icons import icon as render_icon
    icon_str = str(icon)
    if "inventory" in icon_str.lower():
        icon_html = render_icon("inventory", size=36, color=COLOR_TEXT_MUTED)
    elif "permit" in icon_str.lower() or "assignment" in icon_str.lower():
        icon_html = render_icon("permit", size=36, color=COLOR_TEXT_MUTED)
    elif "upload" in icon_str.lower():
        icon_html = render_icon("upload", size=36, color=COLOR_TEXT_MUTED)
    elif "warning" in icon_str.lower() or "alert" in icon_str.lower():
        icon_html = render_icon("warning", size=36, color=ACCENT_AMBER)
    elif icon_str.startswith("<svg"):
        icon_html = icon_str
    else:
        icon_html = f'<span style="font-size: 28px;">{html.escape(icon_str)}</span>'

    safe_message = html.escape(message)

    html_code = (
        f'<div class="empty-state-box">'
        f'<div class="empty-state-icon">{icon_html}</div>'
        f'<div class="empty-state-text">{safe_message}</div>'
        f'</div>'
    )
    if hasattr(st, "html"):
        st.html(html_code)
    else:
        st.markdown(html_code, unsafe_allow_html=True)

    if button_text:
        col_l, col_btn, col_r = st.columns([2, 1, 2])
        with col_btn:
            return st.button(
                button_text,
                key=button_key,
                type="primary",
                use_container_width=True,
                on_click=on_click,
            )
    return False


def status_chip(text: str, state: str = "success") -> str:
    """
    Return HTML for a small status pill chip (green, amber, grey).

    Parameters
    ----------
    text : str
        Chip text.
    state : str
        One of 'success', 'warning', 'neutral'.
    """
    cls_map = {
        "success": "status-chip-green",
        "warning": "status-chip-amber",
        "neutral": "status-chip-grey",
    }
    chip_cls = cls_map.get(state, "status-chip-grey")
    return f'<span class="status-chip {chip_cls}">{html.escape(text)}</span>'


def stepper(current_step: int) -> None:
    """
    Render the 3-step upload progress stepper.

    Parameters
    ----------
    current_step : int
        1 (Upload), 2 (Map columns), or 3 (Review).
    """
    steps = [
        (1, "Upload"),
        (2, "Map columns"),
        (3, "Review"),
    ]

    items_html = []
    for step_num, step_name in steps:
        if step_num == current_step:
            cls = "stepper-step stepper-step-active"
        elif step_num < current_step:
            cls = "stepper-step stepper-step-completed"
        else:
            cls = "stepper-step stepper-step-inactive"

        items_html.append(
            f'<div class="{cls}">'
            f'<span>{step_num}. {step_name}</span>'
            f'</div>'
        )

    stepper_html = f"""
    <div class="stepper-bar">
        {items_html[0]}
        <span class="stepper-arrow">→</span>
        {items_html[1]}
        <span class="stepper-arrow">→</span>
        {items_html[2]}
    </div>
    """
    st.markdown(stepper_html, unsafe_allow_html=True)


def format_currency_inr(val: Any) -> str:
    """
    Format monetary amount in Indian numbering system (Rs. with L / Cr).

    Examples:
      2010000 -> Rs. 20.1 L
      15000000 -> Rs. 1.5 Cr
      45000 -> Rs. 45.0 K
    """
    if val is None or val == "" or str(val) == "—" or str(val) == "-":
        return "—"
    try:
        num = float(val)
        if num >= 10_000_000:
            return f"Rs. {num / 10_000_000:.1f} Cr"
        elif num >= 100_000:
            return f"Rs. {num / 100_000:.1f} L"
        elif num >= 1_000:
            return f"Rs. {num / 1_000:.1f} K"
        else:
            return f"Rs. {num:,.0f}"
    except (ValueError, TypeError):
        return "—"


def safe_render(
    render_fn: Callable[[], None],
    screen_name: str = "Screen",
    on_back: Optional[Callable[[], None]] = None,
) -> None:
    """
    Wrap screen rendering in an exception safety boundary.
    Logs full traceback to logs/app.log, displays a clean user-facing error message,
    provides a 'Back to home' recovery button, and keeps the session alive.
    If DEBUG=1, also displays the traceback in an expander.
    """
    try:
        render_fn()
    except Exception as exc:
        import os
        import traceback
        from datetime import datetime, timezone
        from pathlib import Path

        # Log traceback to logs/app.log
        try:
            log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "app.log"
            tb = traceback.format_exc()
            now = datetime.now(timezone.utc).isoformat()
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n[{now}] ERROR in {screen_name}: {exc}\n{tb}\n{'-'*60}\n")
        except Exception:
            tb = traceback.format_exc()

        # Display user-facing recovery message
        st.markdown(
            f"""
            <div style="background: {COLOR_CARD}; border: 1px solid {COLOR_CARD_BORDER}; border-radius: 16px; padding: 32px 24px; max-width: 580px; margin: 40px auto; text-align: center;">
                <div style="font-size: 20px; font-weight: 700; color: {COLOR_TEXT}; margin-bottom: 8px;">
                    Something went wrong
                </div>
                <div style="font-size: 14px; color: {COLOR_TEXT_MUTED}; margin-bottom: 24px;">
                    Go back and try again.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        col1, col2, col3 = st.columns([1, 1.2, 1])
        with col2:
            btn_key = f"btn_safe_recover_{screen_name.lower().replace(' ', '_')}"
            if st.button("Back to home", key=btn_key, type="primary", use_container_width=True):
                if on_back:
                    on_back()
                else:
                    from app import state
                    state.set("flow_stage", "landing")
                st.rerun()

        if os.getenv("DEBUG") == "1":
            with st.expander("Debug traceback (DEBUG=1)"):
                st.code(tb, language="python")


