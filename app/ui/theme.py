from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette


DEFAULT_THEME = "light"

THEMES: dict[str, dict[str, object]] = {
    "light": {
        "label": "Claro",
        "description": "",
        "colors": {
            "green_deep": "#27953c",
            "green_accessible": "#1f7830",
            "green": "#40b73c",
            "green_lime": "#7eca29",
            "charcoal": "#4f4c4c",
            "gray": "#656263",
            "gray_light": "#7a7879",
            "canvas": "#f5f6f5",
            "panel": "#ffffff",
            "surface": "#fafbfa",
            "surface_hover": "#f7faf7",
            "green_soft": "#eef7ef",
            "border": "#dfe3df",
            "border_strong": "#c8cdc8",
            "text": "#202220",
            "text_soft": "#4f4c4c",
            "muted": "#656263",
            "nav_checked_border": "#cfe7d3",
            "secondary_hover_border": "#afb6af",
            "primary_hover": "#238537",
            "disabled_bg": "#eceeec",
            "placeholder": "#767676",
            "card_selected_bg": "#f3faf4",
            "neutral_badge_bg": "#f0f1f0",
            "alert_bg": "#f1f8e9",
            "alert_hover": "#fbe9e9",
            "alert_border": "#b8d884",
            "alert_text": "#3d6c12",
            "mailbox_bg": "#eef7ef",
            "mailbox_border": "#cfe7d3",
            "mailbox_text": "#1f7830",
            "transcript_row_border": "#edf0ed",
            "transcript_critical_bg": "#f7fbf7",
            "transcript_critical_border": "#d8eadb",
            "transcript_active_border": "#cbe5cf",
            "transcript_played_bg": "#e5f4e7",
            "toast_bg": "#4f4c4c",
            "toast_text": "#ffffff",
            "scroll_handle": "#c8cdc8",
            "tooltip_bg": "#4f4c4c",
            "tooltip_text": "#ffffff",
        },
        "assets": {
            "bases": "bases.svg",
            "chevron": "chevron-down.svg",
            "settings": "settings.svg",
        },
    },
    "dark": {
        "label": "Oscuro",
        "description": "Zinc / Noche",
        "colors": {
            "green_deep": "#40b73c",
            "green_accessible": "#38c160",
            "green": "#40b73c",
            "green_lime": "#86efac",
            "charcoal": "#d4d4d8",
            "gray": "#a1a1aa",
            "gray_light": "#71717a",
            "canvas": "#09090b",
            "panel": "#121215",
            "surface": "#1a1a1e",
            "surface_hover": "#22242a",
            "green_soft": "#142918",
            "border": "#27272a",
            "border_strong": "#3f3f46",
            "text": "#f4f4f5",
            "text_soft": "#d4d4d8",
            "muted": "#a1a1aa",
            "nav_checked_border": "#1e4a25",
            "secondary_hover_border": "#52525b",
            "primary_hover": "#2ea542",
            "disabled_bg": "#202024",
            "placeholder": "#71717a",
            "card_selected_bg": "#142918",
            "neutral_badge_bg": "#1f1f23",
            "alert_bg": "#2a1517",
            "alert_hover": "#35191c",
            "alert_border": "#4d1d22",
            "alert_text": "#ef4444",
            "mailbox_bg": "#142918",
            "mailbox_border": "#1e4a25",
            "mailbox_text": "#38c160",
            "transcript_row_border": "#27272a",
            "transcript_critical_bg": "#142918",
            "transcript_critical_border": "#1e4a25",
            "transcript_active_border": "#1e4a25",
            "transcript_played_bg": "#172d1b",
            "toast_bg": "#27272a",
            "toast_text": "#f4f4f5",
            "scroll_handle": "#3f3f46",
            "tooltip_bg": "#27272a",
            "tooltip_text": "#f4f4f5",
        },
        "assets": {
            "bases": "bases-dark.svg",
            "chevron": "chevron-down-dark.svg",
            "settings": "settings-dark.svg",
        },
    },
}


def normalize_theme(theme_name: str | None) -> str:
    return theme_name if theme_name in THEMES else DEFAULT_THEME


def theme_colors(theme_name: str | None) -> dict[str, str]:
    return THEMES[normalize_theme(theme_name)]["colors"]  # type: ignore[return-value]


def theme_asset(theme_name: str | None, asset: str) -> str:
    assets = THEMES[normalize_theme(theme_name)]["assets"]
    return assets[asset]  # type: ignore[index,return-value]


def theme_options() -> tuple[tuple[str, str], ...]:
    return tuple(
        (key, " · ".join(part for part in (value["label"], value["description"]) if part))
        for key, value in THEMES.items()
    )


def apply_app_theme(app, theme_name: str | None) -> str:
    theme_name = normalize_theme(theme_name)
    colors = theme_colors(theme_name)
    set_color_scheme = getattr(app.styleHints(), "setColorScheme", None)
    if callable(set_color_scheme):
        scheme = Qt.ColorScheme.Dark if theme_name == "dark" else Qt.ColorScheme.Light
        set_color_scheme(scheme)

    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: colors["canvas"],
        QPalette.ColorRole.WindowText: colors["text"],
        QPalette.ColorRole.Base: colors["panel"],
        QPalette.ColorRole.AlternateBase: colors["surface"],
        QPalette.ColorRole.ToolTipBase: colors["tooltip_bg"],
        QPalette.ColorRole.ToolTipText: colors["tooltip_text"],
        QPalette.ColorRole.Text: colors["text"],
        QPalette.ColorRole.Button: colors["panel"],
        QPalette.ColorRole.ButtonText: colors["text_soft"],
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Highlight: colors["green_soft"],
        QPalette.ColorRole.HighlightedText: colors["text"],
        QPalette.ColorRole.Link: colors["green_accessible"],
        QPalette.ColorRole.LinkVisited: colors["green_deep"],
        QPalette.ColorRole.PlaceholderText: colors["placeholder"],
        QPalette.ColorRole.Light: colors["panel"],
        QPalette.ColorRole.Midlight: colors["border"],
        QPalette.ColorRole.Mid: colors["border_strong"],
        QPalette.ColorRole.Dark: colors["gray_light"],
        QPalette.ColorRole.Shadow: colors["canvas"],
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    from app.ui.idle_wheel import install_idle_wheel_guard
    install_idle_wheel_guard(app)
    return theme_name
