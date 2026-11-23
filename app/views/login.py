"""
Login view for the Recon Dashboard.
Matches target reference design:
- Full-viewport power plant photo at dusk with no dark overlay
- Single frosted-glass card centered exactly on both axes
- Translucent input fields for Username and Password
- Bold full-width green "LET'S GOOO" submit button
- Red message pill directly under the button for failed/locked attempts
"""

import base64
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
import streamlit as st

from app import state
from core.database import get_engine, initialize_database
from core.auth import authenticate_user
from core.settings import initialize_settings

logger = logging.getLogger(__name__)


@st.cache_resource
def get_login_bg_css() -> str:
    """
    Read and base64-encode assets/login_bg.jpg once, returning CSS background rule.
    If missing, runs scripts/prepare_login_bg.py to generate it from assets/login_reference.png.
    Falls back to dark teal gradient if neither exists.
    """
    bg_path = Path("assets/login_bg.jpg").resolve()
    if not bg_path.exists():
        try:
            from scripts.prepare_login_bg import prepare_background
            prepare_background()
        except Exception as exc:
            logger.warning("Could not auto-generate login background: %s", exc)

    if bg_path.exists():
        try:
            with open(bg_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
                return f"url('data:image/jpeg;base64,{b64}') center / cover no-repeat fixed"
        except Exception as exc:
            logger.warning("Failed reading login_bg.jpg: %s", exc)

    logger.warning("login_bg.jpg not found, falling back to dark teal gradient.")
    return "linear-gradient(135deg, #0B1E26 0%, #123A44 100%) fixed"


def render_login():
    """Render the single frosted-glass login card matching the target reference design."""
    engine = get_engine()
    initialize_database(engine)
    initialize_settings(engine)

    bg_css = get_login_bg_css()

    st.markdown(
        f"""
        <style>
        /* Hide all Streamlit chrome, header, sidebar, footer, toolbar, deploy button on login view */
        body:has([class*="st-key-login_card"]) [data-testid="stSidebar"],
        body:has([class*="st-key-login_card"]) [data-testid="collapsedControl"],
        body:has([class*="st-key-login_card"]) header[data-testid="stHeader"],
        body:has([class*="st-key-login_card"]) footer,
        body:has([class*="st-key-login_card"]) [data-testid="stToolbar"],
        body:has([class*="st-key-login_card"]) #MainMenu,
        body:has([class*="st-key-login_card"]) .stDeployButton {{
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
            width: 0 !important;
        }}

        /* Full viewport without scrollbars on login view */
        body:has([class*="st-key-login_card"]) {{
            margin: 0 !important;
            padding: 0 !important;
            overflow: hidden !important;
            height: 100vh !important;
        }}

        /* Transparent inner wrappers so photo shows on login */
        body:has([class*="st-key-login_card"]) [data-testid="stAppViewContainer"],
        body:has([class*="st-key-login_card"]) [data-testid="stMain"],
        body:has([class*="st-key-login_card"]) [data-testid="stHeader"] {{
            background: transparent !important;
            background-color: transparent !important;
        }}

        body:has([class*="st-key-login_card"]) [data-testid="stMainBlockContainer"] {{
            max-width: none !important;
            padding: 0 !important;
            margin: 0 !important;
            width: 100vw !important;
            height: 100vh !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            background: transparent !important;
            background-color: transparent !important;
        }}

        /* Full viewport power plant background photo on login view */
        body:has([class*="st-key-login_card"]) .stApp {{
            background: {bg_css} !important;
            min-height: 100vh !important;
            width: 100vw !important;
        }}

        /* Frosted Glass Card Centered in Viewport */
        .st-key-login_card,
        div[class*="st-key-login_card"] {{
            position: fixed !important;
            top: 50% !important;
            left: 50% !important;
            transform: translate(-50%, -50%) !important;
            width: min(400px, 92vw) !important;
            min-height: 350px !important;
            padding: 36px !important;
            border-radius: 20px !important;
            background: rgba(255, 255, 255, 0.22) !important;
            backdrop-filter: blur(16px) saturate(110%) !important;
            -webkit-backdrop-filter: blur(16px) saturate(110%) !important;
            border: 1px solid rgba(255, 255, 255, 0.45) !important;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.18) !important;
            box-sizing: border-box !important;
            z-index: 1000 !important;
            display: flex !important;
            flex-direction: column !important;
            justify-content: flex-start !important;
        }}

        /* Remove default form border, padding, and background */
        div[data-testid="stForm"] {{
            border: none !important;
            padding: 0 !important;
            margin: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
        }}

        /* Set vertical gap to 28px on container and form */
        .st-key-login_card [data-testid="stVerticalBlock"],
        div[class*="st-key-login_card"] [data-testid="stVerticalBlock"] {{
            gap: 28px !important;
        }}

        /* Remove any label space */
        .st-key-login_card [data-testid="stTextInput"] label,
        div[class*="st-key-login_card"] [data-testid="stTextInput"] label {{
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
        }}

        /* Input wrapper styling */
        .st-key-login_card [data-baseweb="input"],
        div[class*="st-key-login_card"] [data-baseweb="input"] {{
            height: 56px !important;
            width: 100% !important;
            border-radius: 10px !important;
            background: rgba(255, 255, 255, 0.12) !important;
            background-color: rgba(255, 255, 255, 0.12) !important;
            border: 1px solid rgba(255, 255, 255, 0.7) !important;
            padding: 0 !important;
            box-shadow: none !important;
            transition: border-color 0.2s ease, box-shadow 0.2s ease !important;
        }}

        .st-key-login_card [data-baseweb="base-input"],
        div[class*="st-key-login_card"] [data-baseweb="base-input"] {{
            height: 56px !important;
            width: 100% !important;
            border-radius: 10px !important;
            background: transparent !important;
            background-color: transparent !important;
        }}

        /* Inner input element */
        .st-key-login_card [data-baseweb="input"] input,
        div[class*="st-key-login_card"] [data-baseweb="input"] input {{
            height: 56px !important;
            width: 100% !important;
            border-radius: 10px !important;
            background: transparent !important;
            background-color: transparent !important;
            border: none !important;
            color: #FFFFFF !important;
            font-size: 17px !important;
            font-family: inherit !important;
            padding: 0 16px !important;
            caret-color: #FFFFFF !important;
            box-sizing: border-box !important;
            outline: none !important;
        }}

        .st-key-login_card [data-baseweb="input"] input::placeholder,
        div[class*="st-key-login_card"] [data-baseweb="input"] input::placeholder {{
            color: rgba(255, 255, 255, 0.92) !important;
            font-size: 17px !important;
        }}

        /* Input focus state */
        .st-key-login_card [data-baseweb="input"]:focus-within,
        div[class*="st-key-login_card"] [data-baseweb="input"]:focus-within {{
            border-color: #FFFFFF !important;
            border: 1px solid #FFFFFF !important;
            box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.25) !important;
        }}

        /* Browser autofill override */
        .st-key-login_card input:-webkit-autofill,
        .st-key-login_card input:-webkit-autofill:hover,
        .st-key-login_card input:-webkit-autofill:focus,
        div[class*="st-key-login_card"] input:-webkit-autofill {{
            -webkit-text-fill-color: #FFFFFF !important;
            -webkit-box-shadow: 0 0 0px 1000px rgba(255, 255, 255, 0.12) inset !important;
            box-shadow: 0 0 0px 1000px rgba(255, 255, 255, 0.12) inset !important;
            transition: background-color 5000s ease-in-out 0s !important;
            caret-color: #FFFFFF !important;
        }}

        /* Hide password reveal button and InputInstructions hint */
        .st-key-login_card button[aria-label*="password" i],
        .st-key-login_card [data-testid="InputInstructions"],
        div[class*="st-key-login_card"] button[aria-label*="password" i],
        div[class*="st-key-login_card"] [data-testid="InputInstructions"] {{
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
            width: 0 !important;
        }}

        /* LET'S GOOO submit button */
        .st-key-login_card div[data-testid="stFormSubmitButton"] button,
        div[class*="st-key-login_card"] div[data-testid="stFormSubmitButton"] button {{
            width: 100% !important;
            height: 56px !important;
            background: #0BAA2A !important;
            background-color: #0BAA2A !important;
            color: #FFFFFF !important;
            font-size: 16px !important;
            font-weight: 700 !important;
            letter-spacing: 0.04em !important;
            border-radius: 8px !important;
            border: none !important;
            cursor: pointer !important;
            box-shadow: none !important;
            transition: background-color 0.15s ease, transform 0.15s ease, box-shadow 0.15s ease !important;
        }}

        .st-key-login_card div[data-testid="stFormSubmitButton"] button:hover,
        div[class*="st-key-login_card"] div[data-testid="stFormSubmitButton"] button:hover {{
            background: #0CC030 !important;
            background-color: #0CC030 !important;
            transform: translateY(-1px) !important;
            box-shadow: 0 6px 16px rgba(11, 170, 42, 0.35) !important;
        }}

        .st-key-login_card div[data-testid="stFormSubmitButton"] button:active,
        div[class*="st-key-login_card"] div[data-testid="stFormSubmitButton"] button:active {{
            transform: none !important;
            box-shadow: none !important;
        }}

        .st-key-login_card div[data-testid="stFormSubmitButton"] button:focus-visible,
        div[class*="st-key-login_card"] div[data-testid="stFormSubmitButton"] button:focus-visible {{
            outline: 2px solid #FFFFFF !important;
            outline-offset: 2px !important;
        }}

        /* Error pill */
        .login-msg-pill {{
            background: rgba(185, 28, 28, 0.85);
            color: #FFFFFF;
            font-size: 13px;
            font-weight: 500;
            border-radius: 8px;
            padding: 8px 12px;
            margin-top: 14px;
            text-align: center;
            line-height: 1.4;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Render single frosted glass card
    with st.container(key="login_card"):
        with st.form("login_form", border=False, clear_on_submit=False):
            username = st.text_input(
                "Username",
                placeholder="Username",
                label_visibility="collapsed",
                autocomplete="username",
                key="login_user_input",
            )
            password = st.text_input(
                "Password",
                type="password",
                placeholder="Password",
                label_visibility="collapsed",
                autocomplete="current-password",
                key="login_pass_input",
            )
            submitted = st.form_submit_button(
                "LET'S GOOO",
                type="primary",
                use_container_width=True,
            )

        if submitted:
            success, msg, user_dict = authenticate_user(engine, username, password)
            if success and user_dict:
                state.set("authenticated", True)
                state.set("user", user_dict)
                state.set("last_activity", datetime.now(timezone.utc).isoformat())
                state.set("flow_stage", "landing")
                st.rerun()
            else:
                if "account locked" in msg.lower() or "too many attempts" in msg.lower() or "attempts." in msg.lower():
                    pill_text = "Too many attempts. Try again in 5 minutes."
                else:
                    pill_text = "Invalid username or password."
                st.markdown(f'<div class="login-msg-pill">{pill_text}</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    render_login()
