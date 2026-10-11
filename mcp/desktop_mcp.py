"""Server MCP per Hyprland: gli "attrezzi" con cui Claude agisce sul desktop.

Usato dal pannello assistente (SUPER+I) e dalle sessioni di Claude Code nel terminale.
Parla con Hyprland tramite hyprctl (sintassi Lua di Hyprland 0.56: hl.dsp.*) e con serpantinum
tramite la sua IPC.
"""

import functools
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("desktop")

SFONDI = Path.home() / "Pictures" / "Wallpapers"
ESTENSIONI_SFONDI = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mkv", ".mov", ".webm"}


# ── Utilità ──────────────────────────────────────────────────────────────────

def _esegui(argomenti: list[str], timeout: int = 15) -> str:
    r = subprocess.run(argomenti, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"{argomenti[0]} è uscito con codice {r.returncode}")
    return r.stdout


def _hyprctl_json(comando: str):
    return json.loads(_esegui(["hyprctl", "-j", comando]))


def _dispatch(espressione_lua: str) -> None:
    risposta = _esegui(["hyprctl", "dispatch", espressione_lua]).strip()
    if risposta and risposta != "ok":
        raise RuntimeError(risposta)


def _lua_stringa(s: str) -> str:
    return json.dumps(s)  # una stringa JSON è anche una stringa Lua valida


def _trova_finestra(finestra: str) -> dict:
    """Accetta un indirizzo (0x...) oppure parte della classe o del titolo."""
    finestre = [c for c in _hyprctl_json("clients") if c.get("mapped") and c["workspace"]["id"] > 0]
    f = finestra.strip().lower()
    if f.startswith("0x"):
        for c in finestre:
            if c["address"].lower() == f:
                return c
        raise ValueError(f"Nessuna finestra con indirizzo {finestra}")
    trovate = [c for c in finestre if f in c["class"].lower() or f in c["title"].lower()]
    if not trovate:
        raise ValueError(f"Nessuna finestra corrisponde a '{finestra}'. Usa elenca_finestre per vederle.")
    esatte = [c for c in trovate if c["class"].lower() == f]
    if len(esatte) == 1:
        return esatte[0]
    if len(trovate) > 1:
        elenco = "; ".join(f"{c['address']} [{c['workspace']['id']}] {c['class']} — {c['title']}" for c in trovate)
        raise ValueError(f"Più finestre corrispondono a '{finestra}', specifica l'indirizzo: {elenco}")
    return trovate[0]


def _descrivi(c: dict) -> str:
    return f"{c['class']} — {c['title']} (workspace {c['workspace']['id']})"


def _serpantinum(*argomenti: str) -> str:
    return _esegui(["serpantinum", "ipc", "call", *argomenti]).strip()


def _con_conferma(funzione):
    """Azioni rischiose: se il server è avviato da oring (ORING_SOCKET), prima di eseguirle chiede
    al daemon, che a seconda della configurazione chiede conferma all'utente nel pannello.
    Senza risposta dal daemon l'azione viene negata. Avviato da altri (es. Claude Code nel
    terminale) il controllo non c'è: decidono i permessi di quel programma."""
    @functools.wraps(funzione)
    def controllata(**argomenti):
        percorso = os.environ.get("ORING_SOCKET")
        if percorso:
            richiesta = {"op": "gate", "agent": os.environ.get("ORING_AGENT", ""),
                         "tool": funzione.__name__, "args": argomenti}
            try:
                with socket.socket(socket.AF_UNIX) as s:
                    s.connect(percorso)
                    s.sendall((json.dumps(richiesta) + "\n").encode())
                    risposta = next((e for e in map(json.loads, s.makefile()) if e.get("ev") == "gate"), {})
            except (OSError, ValueError):
                risposta = {}
            if not risposta.get("allow"):
                raise PermissionError("Azione non permessa dall'utente")
        return funzione(**argomenti)
    return controllata


def _schermo_bloccato() -> bool:
    """Sessione bloccata (WlSessionLock, il blocco di serpantinum): Hyprland lo indica con "LOCK"
    tra i motivi per cui il monitor non è in modalità solitaria. loginctl invece non lo sa."""
    try:
        return any("LOCK" in (m.get("solitaryBlockedBy") or []) for m in _hyprctl_json("monitors"))
    except Exception:
        return False


def _se_sbloccato(funzione):
    """Mouse e tastiera a schermo bloccato finirebbero nella schermata di blocco (anche nel campo
    della password): vengono rifiutati prima ancora di chiedere conferma."""
    @functools.wraps(funzione)
    def controllata(**argomenti):
        if _schermo_bloccato():
            raise RuntimeError("Lo schermo è bloccato: mouse e tastiera non sono utilizzabili. Puoi "
                               "comunque gestire finestre, workspace e app, e vedere o leggere una "
                               "finestra con screenshot/leggi_testo indicando il parametro finestra.")
        return funzione(**argomenti)
    return controllata


# ── Finestre e workspace ─────────────────────────────────────────────────────

@mcp.tool()
def elenca_finestre() -> list[dict]:
    """Elenca tutte le finestre aperte: indirizzo, applicazione (class), titolo, workspace,
    posizione e dimensioni, se è flottante o a schermo intero, e quale ha il focus."""
    attiva = _hyprctl_json("activewindow").get("address")
    return [
        {
            "indirizzo": c["address"],
            "applicazione": c["class"],
            "titolo": c["title"],
            "workspace": c["workspace"]["id"],
            "posizione": c["at"],
            "dimensioni": c["size"],
            "flottante": c["floating"],
            "schermo_intero": c["fullscreen"] != 0,
            "attiva": c["address"] == attiva,
        }
        for c in _hyprctl_json("clients")
        if c.get("mapped") and c["workspace"]["id"] > 0
    ]


@mcp.tool()
def elenca_workspace() -> dict:
    """Elenca i workspace in uso (numero e quante finestre contengono) e indica quello attivo."""
    attivo = _hyprctl_json("activeworkspace")["id"]
    ws = sorted(_hyprctl_json("workspaces"), key=lambda w: w["id"])
    return {
        "attivo": attivo,
        "workspace": [{"numero": w["id"], "finestre": w["windows"]} for w in ws if w["id"] > 0],
    }


@mcp.tool()
def metti_a_fuoco(finestra: str) -> str:
    """Porta il focus su una finestra (e sul suo workspace). `finestra` è l'indirizzo (0x...)
    oppure parte del nome dell'applicazione o del titolo (es. "firefox", "spotify")."""
    c = _trova_finestra(finestra)
    _dispatch(f'hl.dsp.focus({{ window = "address:{c["address"]}" }})')
    return f"Focus su {_descrivi(c)}"


@mcp.tool()
def sposta_in_workspace(finestra: str, workspace: int, seguila: bool = False) -> str:
    """Sposta una finestra nel workspace indicato (1-10). Con seguila=true anche la vista si sposta lì."""
    c = _trova_finestra(finestra)
    _dispatch(f'hl.dsp.window.move({{ workspace = "{int(workspace)}", window = "address:{c["address"]}" }})')
    if seguila:
        _dispatch(f'hl.dsp.focus({{ window = "address:{c["address"]}" }})')
    return f"Spostata {c['class']} nel workspace {workspace}"


@mcp.tool()
def vai_al_workspace(workspace: int) -> str:
    """Mostra il workspace indicato (1-10)."""
    _dispatch(f'hl.dsp.focus({{ workspace = "{int(workspace)}" }})')
    return f"Ora sul workspace {workspace}"


@mcp.tool()
@_con_conferma
def chiudi_finestra(finestra: str) -> str:
    """Chiude una finestra. Usalo SOLO se l'utente ha chiesto esplicitamente di chiuderla:
    eventuale lavoro non salvato nell'applicazione potrebbe andare perso."""
    c = _trova_finestra(finestra)
    _dispatch(f'hl.dsp.window.close({{ window = "address:{c["address"]}" }})')
    return f"Chiusa {_descrivi(c)}"


@mcp.tool()
def schermo_intero(finestra: str, modo: str = "massimizza") -> str:
    """Attiva/disattiva lo schermo intero di una finestra. modo: "massimizza" (la barra resta
    visibile) oppure "intero" (schermo intero vero)."""
    c = _trova_finestra(finestra)
    _dispatch(f'hl.dsp.focus({{ window = "address:{c["address"]}" }})')
    if modo == "intero":
        _dispatch("hl.dsp.window.fullscreen()")
    else:
        _dispatch('hl.dsp.window.fullscreen({ mode = "maximized" })')
    return f"Schermo intero ({modo}) alternato per {c['class']}"


@mcp.tool()
def flottante(finestra: str) -> str:
    """Rende flottante una finestra affiancata, o la riaffianca se è già flottante."""
    c = _trova_finestra(finestra)
    _dispatch(f'hl.dsp.focus({{ window = "address:{c["address"]}" }})')
    _dispatch('hl.dsp.window.float({ action = "toggle" })')
    return f"Modalità flottante alternata per {c['class']}"


@mcp.tool()
def avvia_applicazione(comando: str, workspace: int | None = None) -> str:
    """Avvia un'applicazione (es. "firefox", "kitty", "nautilus", "code", "spotify").
    Se `workspace` è indicato, prima passa a quel workspace così l'app si apre lì."""
    if workspace is not None:
        _dispatch(f'hl.dsp.focus({{ workspace = "{int(workspace)}" }})')
    _dispatch(f"hl.dsp.exec_cmd({_lua_stringa(comando)})")
    return f"Avviato: {comando}"


# ── Schermo ──────────────────────────────────────────────────────────────────

def _file_temporaneo(nome: str) -> Path:
    cartella = Path(tempfile.gettempdir()) / "oring"
    cartella.mkdir(exist_ok=True)
    return cartella / f"{nome}-{time.time_ns()}.png"


def _bersaglio(cosa: str, finestra: str | None, x: int | None = None, y: int | None = None,
              larghezza: int | None = None, altezza: int | None = None) -> dict:
    """Cosa catturare: una finestra (cattura diretta della finestra, funziona anche se è coperta,
    in un altro workspace o con lo schermo bloccato) oppure un'area dello schermo.
    Restituisce {"finestra": id per grim -T, ...} oppure {"area": (x, y, l, a)}; sempre
    "origine" (x, y) in coordinate dello schermo e "descrizione"."""
    if finestra or cosa == "finestra":
        c = _trova_finestra(finestra) if finestra else _hyprctl_json("activewindow")
        if not c.get("stableId"):
            raise ValueError("Nessuna finestra attiva")
        return {"finestra": c["stableId"], "origine": tuple(c["at"]), "descrizione": _descrivi(c)}
    if _schermo_bloccato():
        raise RuntimeError("Lo schermo è bloccato: si vedrebbe solo la schermata di blocco. Indica una "
                           "finestra (parametro finestra, vedi elenca_finestre) per catturare quella.")
    if cosa == "area":
        if None in (x, y, larghezza, altezza):
            raise ValueError("Per cosa='area' servono x, y, larghezza e altezza")
        a = (int(x), int(y), int(larghezza), int(altezza))
        return {"area": a, "origine": a[:2], "descrizione": f"area {a[0]},{a[1]} {a[2]}x{a[3]}"}
    monitor = next((m for m in _hyprctl_json("monitors") if m.get("focused")), None)
    if monitor is None:
        raise RuntimeError("Nessun monitor attivo trovato")
    a = (monitor["x"], monitor["y"], monitor["width"], monitor["height"])
    return {"area": a, "origine": a[:2], "descrizione": "schermo intero"}


def _cattura(b: dict, nome: str, scala: float | None = None) -> Path:
    file = _file_temporaneo(nome)
    if "finestra" in b:
        args = ["grim", "-T", b["finestra"]]
    else:
        ax, ay, aw, ah = b["area"]
        args = ["grim", "-g", f"{ax},{ay} {aw}x{ah}"]
    if scala:
        args += ["-s", str(scala)]
    # timeout: con il monitor spento grim aspetterebbe all'infinito un fotogramma
    _esegui(args + [str(file)], timeout=10)
    return file


@mcp.tool()
def screenshot(cosa: str = "schermo", finestra: str | None = None, x: int | None = None, y: int | None = None,
               larghezza: int | None = None, altezza: int | None = None) -> list:
    """Fa uno screenshot e te lo mostra, per vedere cosa c'è sul desktop.
    cosa: "schermo" (tutto il monitor), "finestra" (la finestra attiva) oppure "area" (con x, y,
    larghezza, altezza in pixel dello schermo). finestra: nome o indirizzo di una finestra precisa
    (come in elenca_finestre): viene catturata anche se è coperta, in un altro workspace o con lo
    schermo bloccato. L'immagine è ridotta: insieme all'immagine ricevi come convertire le sue
    coordinate in coordinate dello schermo (per muovere il mouse o cliccare, solo se è visibile).
    Per leggere testo preciso preferisci leggi_testo / trova_testo."""
    b = _bersaglio(cosa, finestra, x, y, larghezza, altezza)
    scala = 0.4 if b["descrizione"] == "schermo intero" else 0.6
    file = _cattura(b, "screenshot", scala)
    ox, oy = b["origine"]
    nota = (f"Immagine ridotta (scala {scala}) di: {b['descrizione']}. "
            f"Coordinate schermo = (x_immagine / {scala} + {ox}, y_immagine / {scala} + {oy}).")
    return [Image(path=str(file)), nota]


# ── Lettura del testo sullo schermo (OCR) ────────────────────────────────────

def _ocr_tsv(b: dict, lingua: str) -> list[dict]:
    file = _cattura(b, "ocr")
    tsv = _esegui(["tesseract", str(file), "-", "-l", lingua, "tsv"], timeout=60)
    file.unlink(missing_ok=True)
    ox, oy = b["origine"]
    parole = []
    righe = tsv.splitlines()
    for riga in righe[1:]:
        c = riga.split("\t")
        if len(c) == 12 and c[11].strip() and float(c[10]) > 30:
            parole.append({
                "testo": c[11], "blocco": (c[2], c[3], c[4]),
                "x": int(c[6]) + ox, "y": int(c[7]) + oy, "l": int(c[8]), "a": int(c[9]),
            })
    return parole


@mcp.tool()
def leggi_testo(cosa: str = "finestra", finestra: str | None = None, lingua: str = "ita+eng",
                x: int | None = None, y: int | None = None,
                larghezza: int | None = None, altezza: int | None = None) -> str:
    """Legge il testo visibile sullo schermo con l'OCR. cosa: "finestra" (attiva), "schermo"
    oppure "area" (x, y, larghezza, altezza). finestra: nome o indirizzo di una finestra precisa
    (funziona anche se coperta, in un altro workspace o a schermo bloccato).
    lingua: codici tesseract, es. "ita+eng"."""
    righe: dict = {}
    for p in _ocr_tsv(_bersaglio(cosa, finestra, x, y, larghezza, altezza), lingua):
        righe.setdefault(p["blocco"], []).append(p["testo"])
    testo = "\n".join(" ".join(r) for r in righe.values())
    return testo or "(nessun testo riconosciuto)"


@mcp.tool()
def trova_testo(testo: str, cosa: str = "schermo", finestra: str | None = None, lingua: str = "ita+eng") -> list[dict]:
    """Cerca un testo sullo schermo (OCR) e restituisce dove si trova: le coordinate del centro,
    utili per cliccarci sopra con clicca(x, y). cosa: "schermo" o "finestra" (attiva); finestra:
    nome o indirizzo di una finestra precisa. Cerca parole o frasi brevi (es. il testo di un
    pulsante o di un link)."""
    parole = _ocr_tsv(_bersaglio(cosa, finestra), lingua)
    cercate = testo.lower().split()
    if not cercate:
        raise ValueError("Testo da cercare vuoto")
    risultati = []
    for i in range(len(parole) - len(cercate) + 1):
        gruppo = parole[i:i + len(cercate)]
        if all(c in g["testo"].lower() for c, g in zip(cercate, gruppo)) and len({g["blocco"] for g in gruppo}) == 1:
            x1 = min(g["x"] for g in gruppo)
            y1 = min(g["y"] for g in gruppo)
            x2 = max(g["x"] + g["l"] for g in gruppo)
            y2 = max(g["y"] + g["a"] for g in gruppo)
            risultati.append({"testo": " ".join(g["testo"] for g in gruppo),
                              "centro_x": (x1 + x2) // 2, "centro_y": (y1 + y2) // 2})
    return risultati or [{"esito": f"'{testo}' non trovato sullo schermo"}]


# ── Mouse ────────────────────────────────────────────────────────────────────
# Il cursore si sposta con Hyprland (posizione esatta); clic e scorrimento con wlrctl
# (puntatore virtuale di Wayland, senza servizi o permessi speciali).

TASTI_MOUSE = {"sinistro": "left", "destro": "right", "centrale": "middle"}


def _muovi(x: int, y: int) -> None:
    _dispatch(f"hl.dsp.cursor.move({{ x = {int(x)}, y = {int(y)} }})")
    time.sleep(0.05)


@mcp.tool()
@_se_sbloccato
@_con_conferma
def muovi_mouse(x: int, y: int) -> str:
    """Sposta il cursore del mouse nel punto (x, y) in pixel dello schermo (3440x1440)."""
    _muovi(x, y)
    return f"Cursore in {x},{y}"


@mcp.tool()
@_se_sbloccato
@_con_conferma
def clicca(x: int | None = None, y: int | None = None, tasto: str = "sinistro", doppio: bool = False) -> str:
    """Clicca con il mouse. Se x e y sono indicati, prima sposta lì il cursore (pixel dello schermo).
    tasto: "sinistro", "destro" o "centrale". doppio=true per il doppio clic.
    Usalo SOLO se l'utente ha chiesto un'azione che lo richiede; per finestre e workspace preferisci
    gli attrezzi dedicati. Per trovare dove cliccare usa trova_testo o screenshot."""
    if tasto not in TASTI_MOUSE:
        raise ValueError("tasto deve essere sinistro, destro o centrale")
    if x is not None and y is not None:
        _muovi(x, y)
    for _ in range(2 if doppio else 1):
        _esegui(["wlrctl", "pointer", "click", TASTI_MOUSE[tasto]])
        time.sleep(0.08)
    pos = _esegui(["hyprctl", "cursorpos"]).strip()
    return f"{'Doppio clic' if doppio else 'Clic'} {tasto} in {pos}"


@mcp.tool()
@_se_sbloccato
@_con_conferma
def scorri(direzione: str = "giu", quantita: int = 5, x: int | None = None, y: int | None = None) -> str:
    """Scorre con la rotellina (direzione: "giu", "su", "sinistra", "destra") nella finestra sotto
    il cursore. Se x e y sono indicati, prima sposta lì il cursore."""
    if x is not None and y is not None:
        _muovi(x, y)
    passi = max(1, min(int(quantita), 50)) * 10
    dy, dx = {"giu": (passi, 0), "su": (-passi, 0), "destra": (0, passi), "sinistra": (0, -passi)}[direzione]
    _esegui(["wlrctl", "pointer", "scroll", str(dy), str(dx)])
    return f"Scorrimento {direzione}"


# ── Tastiera ─────────────────────────────────────────────────────────────────
# wtype: tastiera virtuale di Wayland, scrive nella finestra che ha il focus.

MODIFICATORI = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "maiusc": "shift",
                "alt": "alt", "super": "logo", "win": "logo", "meta": "logo"}
NOMI_TASTI = {"invio": "Return", "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
              "tab": "Tab", "spazio": "space", "space": "space", "canc": "Delete", "delete": "Delete",
              "backspace": "BackSpace", "su": "Up", "giu": "Down", "sinistra": "Left", "destra": "Right",
              "up": "Up", "down": "Down", "left": "Left", "right": "Right", "home": "Home", "fine": "End",
              "end": "End", "pagsu": "Prior", "pageup": "Prior", "paggiu": "Next", "pagedown": "Next"}


@mcp.tool()
@_se_sbloccato
@_con_conferma
def scrivi_testo(testo: str) -> str:
    """Scrive un testo nella finestra che ha il focus, come se lo digitasse l'utente.
    Prima assicurati che la finestra giusta abbia il focus (metti_a_fuoco o clicca).
    NON usarlo per password o dati sensibili."""
    _esegui(["wtype", "-d", "4", "--", testo], timeout=120)
    return f"Scritti {len(testo)} caratteri"


@mcp.tool()
@_se_sbloccato
@_con_conferma
def premi_tasti(combinazione: str) -> str:
    """Preme un tasto o una combinazione nella finestra che ha il focus. Esempi: "invio", "esc",
    "ctrl+c", "ctrl+shift+t", "alt+f4", "ctrl+l", "f5", "giu". I tasti si separano con "+"."""
    parti = [p.strip() for p in combinazione.split("+") if p.strip()]
    if not parti:
        raise ValueError("Combinazione vuota")
    *mods, tasto = parti
    argomenti = ["wtype"]
    for m in mods:
        if m.lower() not in MODIFICATORI:
            raise ValueError(f"Modificatore sconosciuto: {m}")
        argomenti += ["-M", MODIFICATORI[m.lower()]]
    t = tasto.lower()
    if t in NOMI_TASTI:
        argomenti += ["-k", NOMI_TASTI[t]]
    elif len(t) > 1 and t[0] == "f" and t[1:].isdigit():
        argomenti += ["-k", t.upper()]
    elif len(tasto) == 1:
        argomenti += [tasto]
    else:
        argomenti += ["-k", tasto]   # nome XKB (es. "Print", "XF86AudioPlay")
    for m in reversed(mods):
        argomenti += ["-m", MODIFICATORI[m.lower()]]
    _esegui(argomenti)
    return f"Premuto: {combinazione}"


# ── Appunti ──────────────────────────────────────────────────────────────────

@mcp.tool()
def leggi_appunti() -> str:
    """Legge il testo negli appunti (quello copiato con Ctrl+C)."""
    try:
        tipi = _esegui(["wl-paste", "--list-types"]).split()
    except RuntimeError:
        return "(appunti vuoti)"
    if not any(t.startswith("text/") or t in ("UTF8_STRING", "STRING", "TEXT") for t in tipi):
        return f"Negli appunti non c'è testo (tipi: {', '.join(tipi) or 'vuoto'})"
    return _esegui(["wl-paste", "--no-newline"]) or "(appunti vuoti)"


@mcp.tool()
@_con_conferma
def scrivi_appunti(testo: str) -> str:
    """Mette un testo negli appunti, pronto da incollare con Ctrl+V."""
    # wl-copy resta in background a "tenere" gli appunti: niente pipe su stdout/stderr, altrimenti
    # subprocess aspetterebbe la sua fine
    r = subprocess.run(["wl-copy"], input=testo, text=True, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError("wl-copy non riuscito")
    return "Testo copiato negli appunti"


# ── Sfondi e tema (serpantinum) ──────────────────────────────────────────────

@mcp.tool()
def elenca_sfondi() -> dict:
    """Elenca gli sfondi disponibili in ~/Pictures/Wallpapers (immagini e video) e quello attuale."""
    sfondi = sorted(p.name for p in SFONDI.iterdir() if p.suffix.lower() in ESTENSIONI_SFONDI) if SFONDI.is_dir() else []
    try:
        attuale = Path(_serpantinum("wallpaper", "getWallpaperPath", "")).name
    except Exception:
        attuale = None
    return {"attuale": attuale, "sfondi": sfondi}


@mcp.tool()
def cambia_sfondo(nome: str) -> str:
    """Cambia lo sfondo (con transizione animata). `nome` è il nome del file o una sua parte,
    come restituito da elenca_sfondi. Se il tema colori è Matugen, anche i colori cambiano."""
    candidati = [p for p in SFONDI.iterdir() if p.suffix.lower() in ESTENSIONI_SFONDI and nome.lower() in p.name.lower()]
    if not candidati:
        raise ValueError(f"Nessuno sfondo corrisponde a '{nome}'")
    if len(candidati) > 1:
        esatti = [p for p in candidati if p.stem.lower() == nome.lower() or p.name.lower() == nome.lower()]
        if len(esatti) != 1:
            raise ValueError("Più sfondi corrispondono: " + ", ".join(p.name for p in candidati))
        candidati = esatti
    sfondo = candidati[0]
    _serpantinum("wallpaper", "setWallpaper", "all", str(sfondo), "random")
    if sfondo.suffix.lower() not in {".mp4", ".mkv", ".mov", ".webm"}:
        try:
            _serpantinum("matugen", "generateImage", str(sfondo))
        except Exception:
            pass
    return f"Sfondo cambiato: {sfondo.name}"


# ── Varie ────────────────────────────────────────────────────────────────────

@mcp.tool()
def notifica(titolo: str, testo: str = "") -> str:
    """Mostra una notifica sul desktop."""
    _esegui(["notify-send", "-a", "Claude", titolo, testo])
    return "Notifica inviata"


@mcp.tool()
def controlla_musica(azione: str) -> str:
    """Controlla il lettore musicale attivo (es. Spotify). azione: "play-pause", "next",
    "previous", "stop" oppure "stato" (cosa sta suonando)."""
    if azione == "stato":
        stato = _esegui(["playerctl", "status"]).strip()
        brano = _esegui(["playerctl", "metadata", "--format", "{{ artist }} — {{ title }}"]).strip()
        return f"{stato}: {brano}"
    if azione not in {"play-pause", "next", "previous", "stop"}:
        raise ValueError("azione non valida")
    _esegui(["playerctl", azione])
    return f"Musica: {azione}"


if __name__ == "__main__":
    mcp.run()
