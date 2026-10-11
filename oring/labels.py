"""Human-readable descriptions of tool calls, shown in the panel."""

LABELS = {
    "it": {
        "elenca_finestre": "Guardo le finestre aperte",
        "elenca_workspace": "Guardo i workspace",
        "metti_a_fuoco": "Metto a fuoco",
        "sposta_in_workspace": "Sposto la finestra",
        "vai_al_workspace": "Cambio workspace",
        "chiudi_finestra": "Chiudo la finestra",
        "schermo_intero": "Schermo intero",
        "flottante": "Finestra flottante",
        "avvia_applicazione": "Avvio",
        "screenshot": "Guardo lo schermo",
        "elenca_sfondi": "Guardo gli sfondi",
        "cambia_sfondo": "Cambio sfondo",
        "notifica": "Notifica",
        "controlla_musica": "Musica",
        "leggi_testo": "Leggo il testo sullo schermo",
        "trova_testo": "Cerco sullo schermo",
        "muovi_mouse": "Sposto il mouse",
        "clicca": "Clic",
        "scorri": "Scorro",
        "scrivi_testo": "Scrivo",
        "premi_tasti": "Premo",
        "leggi_appunti": "Leggo gli appunti",
        "scrivi_appunti": "Copio negli appunti",
        "Read": "Leggo un file",
        "Bash": "Eseguo un comando",
        "Edit": "Modifico un file",
        "Write": "Scrivo un file",
        "WebSearch": "Cerco sul web",
        "WebFetch": "Apro una pagina web",
    },
    "en": {
        "elenca_finestre": "Looking at open windows",
        "elenca_workspace": "Looking at workspaces",
        "metti_a_fuoco": "Focusing",
        "sposta_in_workspace": "Moving window",
        "vai_al_workspace": "Switching workspace",
        "chiudi_finestra": "Closing window",
        "schermo_intero": "Fullscreen",
        "flottante": "Floating window",
        "avvia_applicazione": "Launching",
        "screenshot": "Looking at the screen",
        "elenca_sfondi": "Looking at wallpapers",
        "cambia_sfondo": "Changing wallpaper",
        "notifica": "Notification",
        "controlla_musica": "Music",
        "leggi_testo": "Reading text on screen",
        "trova_testo": "Searching the screen",
        "muovi_mouse": "Moving the mouse",
        "clicca": "Click",
        "scorri": "Scrolling",
        "scrivi_testo": "Typing",
        "premi_tasti": "Pressing",
        "leggi_appunti": "Reading the clipboard",
        "scrivi_appunti": "Copying to clipboard",
        "Read": "Reading a file",
        "Bash": "Running a command",
        "Edit": "Editing a file",
        "Write": "Writing a file",
        "WebSearch": "Searching the web",
        "WebFetch": "Opening a web page",
    },
}

# Internal tools that are not worth showing
HIDDEN = {"ToolSearch"}


def short_name(name: str) -> str:
    """mcp__desktop__clicca / desktop__clicca → clicca"""
    if name.startswith("mcp__"):
        name = name.split("__", 2)[-1]
    elif "__" in name:
        name = name.split("__", 1)[1]
    return name


def describe(name: str, args: dict | None, language: str) -> str | None:
    if name in HIDDEN:
        return None
    short = short_name(name)
    label = LABELS.get(language, LABELS["en"]).get(short, short)
    args = args or {}
    key = next((k for k in ("file_path", "command", "url", "query", "pattern", "path") if k in args), None)
    values = [args[key]] if key else args.values()
    details = [str(v) for v in values if isinstance(v, (str, int, float, bool))]
    text = label + (": " + ", ".join(details) if details else "")
    return text if len(text) <= 160 else text[:157] + "…"
