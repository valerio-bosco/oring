// oring panel: chat with the agent chosen in the header, on the right edge of the screen.
// Talks to the oring daemon over its Unix socket; colors follow the serpantinum theme.
// Toggle: oring toggle   (quickshell -p <this file> ipc call oring toggle)
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
    id: root

    property bool open: false
    property bool busy: false
    property bool connected: false
    property var agents: []
    property string active: ""
    property string language: "en"
    property bool agentMenu: false

    readonly property string activeName: {
        for (const a of agents) if (a.id === active) return a.name;
        return "oring";
    }

    // ── serpantinum theme colors (updated live when the theme changes) ──
    property var theme: ({})
    readonly property color cBase:    theme.base     || "#101010"
    readonly property color cSurf:    theme.surface0 || "#1e1e1e"
    readonly property color cSurf2:   theme.surface1 || "#2a2a2a"
    readonly property color cText:    theme.text     || "#f0f0f0"
    readonly property color cSubtle:  theme.subtext0 || "#a0a0a0"
    readonly property color cAccent:  theme.blue     || "#ffffff"
    readonly property color cError:   theme.red      || "#e06c75"
    readonly property string fontFamily: Quickshell.env("ORING_FONT") || "Google Sans"

    FileView {
        path: Quickshell.env("HOME") + "/.local/state/serpantinum/qs_colors.json"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: { try { root.theme = JSON.parse(text()); } catch (e) { } }
    }

    // ── Strings ──
    readonly property var strings: ({
        it: {
            working: "sta lavorando…", idle: "pronto", offline: "daemon non raggiungibile",
            newChat: "Nuova chat", stop: "Ferma", placeholder: "Scrivi…  (Invio invia, Maiusc+Invio va a capo)",
            placeholderBusy: "Sta lavorando…", cancelled: "Interrotto", busyErr: "Aspetta la fine della risposta",
            notAllowed: "Azione non consentita: ", permission: "Richiesta di permesso",
            empty: "Chiedimi qualcosa, oppure di fare qualcosa sul desktop.\n\n«Metti Spotify nel workspace 3»\n«Cosa c'è aperto?»\n«Cosa vedi sullo schermo?»",
            offlineHelp: "Il daemon di oring non risponde.\nControlla:  systemctl --user status oring",
            switchAgent: "Cambia agente (nuova chat)"
        },
        en: {
            working: "working…", idle: "ready", offline: "daemon unreachable",
            newChat: "New chat", stop: "Stop", placeholder: "Type…  (Enter sends, Shift+Enter for a new line)",
            placeholderBusy: "Working…", cancelled: "Cancelled", busyErr: "Wait for the answer to finish",
            notAllowed: "Not allowed: ", permission: "Permission request",
            empty: "Ask me something, or to do something on the desktop.\n\n«Move Spotify to workspace 3»\n«What's open?»\n«What do you see on screen?»",
            offlineHelp: "The oring daemon is not responding.\nCheck:  systemctl --user status oring",
            switchAgent: "Switch agent (new chat)"
        }
    })
    function tr(key) { return (strings[language] || strings.en)[key] || strings.en[key]; }

    function localize(role, text) {
        if (role === "info" && text === "cancelled") return tr("cancelled");
        if (role === "error" && text === "busy") return tr("busyErr");
        if (role === "error" && text.startsWith("not allowed: ")) return tr("notAllowed") + text.slice(13);
        return text;
    }

    // ── Conversation ──
    ListModel { id: messages }          // role: user | agent | tool | info | error
    ListModel { id: permissions }       // id, title, options (JSON string)

    function append(role, text) {
        messages.append({ role: role, text: localize(role, text) });
        view.positionViewAtEnd();
    }

    function handle(line) {
        if (!line.trim()) return;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { return; }
        switch (ev.ev) {
        case "state":
            agents = ev.agents; active = ev.active; busy = ev.busy; language = ev.language;
            messages.clear();
            for (const h of ev.history) messages.append({ role: h.role, text: localize(h.role, h.text) });
            permissions.clear();
            for (const p of ev.permissions) permissions.append({ pid: p.id, title: p.title, options: JSON.stringify(p.options) });
            view.positionViewAtEnd();
            break;
        case "append": append(ev.role, ev.text); break;
        case "extend":
            if (messages.count > 0) {
                messages.setProperty(messages.count - 1, "text", messages.get(messages.count - 1).text + ev.text);
                view.positionViewAtEnd();
            }
            break;
        case "busy": busy = ev.value; break;
        case "permission":
            permissions.append({ pid: ev.id, title: ev.title, options: JSON.stringify(ev.options) });
            open = true;
            break;
        case "permission_done":
            for (let i = 0; i < permissions.count; i++)
                if (permissions.get(i).pid === ev.id) { permissions.remove(i); break; }
            break;
        }
    }

    function sendOp(obj) {
        const socket = connection.item;
        if (!root.connected || !socket) return;
        socket.write(JSON.stringify(obj) + "\n");
        socket.flush();
    }
    function send(text) {
        text = text.trim();
        if (!text || busy) return;
        sendOp({ op: "send", text: text });
    }

    // ── Daemon connection ──
    // The socket is recreated on every attempt: a Quickshell Socket does not reliably retry
    // after the server went away
    Loader {
        id: connection
        sourceComponent: Component {
            Socket {
                path: Quickshell.env("ORING_SOCKET") || (Quickshell.env("XDG_RUNTIME_DIR") + "/oring/daemon.sock")
                parser: SplitParser { onRead: data => root.handle(data) }
                onConnectedChanged: {
                    root.connected = connected;
                    if (connected) { write(JSON.stringify({ op: "hello" }) + "\n"); flush(); }
                }
                Component.onCompleted: connected = true
            }
        }
    }
    Timer {
        interval: 2000; repeat: true
        running: !root.connected
        onTriggered: { connection.active = false; connection.active = true; }
    }

    IpcHandler {
        target: "oring"
        function toggle(): void { root.open = !root.open; }
        function open(): void { root.open = true; }
        function close(): void { root.open = false; }
        function ask(text: string): void { root.open = true; root.send(text); }
    }

    // ── Panel ──
    PanelWindow {
        visible: root.open
        color: "transparent"

        WlrLayershell.namespace: "oring"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: root.open ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None
        exclusionMode: ExclusionMode.Ignore

        anchors.top: true
        anchors.right: true
        anchors.bottom: true
        margins.top: Number(Quickshell.env("ORING_MARGIN_TOP") || 64)    // below the serpantinum bar
        margins.right: 12
        margins.bottom: 12
        implicitWidth: Number(Quickshell.env("ORING_WIDTH") || 520)

        Rectangle {
            anchors.fill: parent
            radius: 18
            color: Qt.rgba(root.cBase.r, root.cBase.g, root.cBase.b, 0.94)
            border.width: 1
            border.color: Qt.rgba(root.cText.r, root.cText.g, root.cText.b, 0.08)
            Keys.onEscapePressed: root.open = false

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 12

                // ── Header ──
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Rectangle {
                        width: 10; height: 10; radius: 5
                        color: !root.connected ? root.cError : root.busy ? root.cAccent : root.cSubtle
                        SequentialAnimation on opacity {
                            running: root.busy
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.25; duration: 600 }
                            NumberAnimation { to: 1.0; duration: 600 }
                        }
                    }
                    // agent name: click to switch agent
                    Rectangle {
                        implicitWidth: nameRow.implicitWidth + 16
                        implicitHeight: 34
                        radius: 12
                        color: nameArea.containsMouse || root.agentMenu ? root.cSurf : "transparent"
                        Row {
                            id: nameRow
                            anchors.centerIn: parent
                            spacing: 6
                            Text {
                                text: root.activeName
                                color: root.cText
                                font.family: root.fontFamily
                                font.pixelSize: 20
                                font.weight: Font.Medium
                            }
                            Text {
                                visible: root.agents.length > 1
                                anchors.verticalCenter: parent.verticalCenter
                                text: root.agentMenu ? "▴" : "▾"
                                color: root.cSubtle
                                font.pixelSize: 14
                            }
                        }
                        MouseArea {
                            id: nameArea
                            anchors.fill: parent
                            hoverEnabled: true
                            enabled: root.agents.length > 1
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.agentMenu = !root.agentMenu
                        }
                    }
                    Text {
                        text: !root.connected ? root.tr("offline") : root.busy ? root.tr("working") : root.tr("idle")
                        color: root.cSubtle
                        font.family: root.fontFamily
                        font.pixelSize: 13
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    PillButton {
                        label: root.busy ? root.tr("stop") : root.tr("newChat")
                        onClicked: root.sendOp({ op: root.busy ? "cancel" : "new" })
                    }
                }

                // ── Agent menu ──
                Rectangle {
                    visible: root.agentMenu
                    Layout.fillWidth: true
                    implicitHeight: agentColumn.implicitHeight + 12
                    radius: 14
                    color: root.cSurf
                    Column {
                        id: agentColumn
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 6 }
                        Text {
                            text: root.tr("switchAgent")
                            color: root.cSubtle
                            font.family: root.fontFamily
                            font.pixelSize: 11
                            leftPadding: 10; topPadding: 4; bottomPadding: 4
                        }
                        Repeater {
                            model: root.agents
                            delegate: Rectangle {
                                required property var modelData
                                width: agentColumn.width
                                height: 36
                                radius: 10
                                color: rowArea.containsMouse ? root.cSurf2 : "transparent"
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    x: 10
                                    text: modelData.name
                                    color: modelData.id === root.active ? root.cAccent : root.cText
                                    font.family: root.fontFamily
                                    font.pixelSize: 14
                                    font.weight: modelData.id === root.active ? Font.Medium : Font.Normal
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.right: parent.right
                                    anchors.rightMargin: 10
                                    text: modelData.type
                                    color: root.cSubtle
                                    font.family: root.fontFamily
                                    font.pixelSize: 11
                                }
                                MouseArea {
                                    id: rowArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        root.agentMenu = false;
                                        root.sendOp({ op: "select", agent: modelData.id });
                                    }
                                }
                            }
                        }
                    }
                }

                // ── Messages ──
                ListView {
                    id: view
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 10
                    model: messages
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    Text {
                        visible: messages.count === 0
                        anchors.centerIn: parent
                        width: parent.width - 40
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        color: root.cSubtle
                        font.family: root.fontFamily
                        font.pixelSize: 14
                        lineHeight: 1.3
                        text: root.connected ? root.tr("empty") : root.tr("offlineHelp")
                    }

                    delegate: Item {
                        id: entry
                        required property string role
                        required property string text
                        width: view.width
                        height: bubble.height

                        Rectangle {
                            id: bubble
                            readonly property bool mine: entry.role === "user"
                            readonly property bool small: entry.role === "tool" || entry.role === "info"
                            anchors.right: mine ? parent.right : undefined
                            anchors.left: mine ? undefined : parent.left
                            width: small ? Math.min(body.implicitWidth + 24, view.width)
                                         : Math.min(body.implicitWidth + 28, view.width * (mine ? 0.85 : 1.0))
                            height: body.implicitHeight + (small ? 12 : 20)
                            radius: small ? 10 : 14
                            color: mine ? root.cSurf2
                                 : entry.role === "error" ? Qt.rgba(root.cError.r, root.cError.g, root.cError.b, 0.15)
                                 : small ? "transparent"
                                 : root.cSurf
                            border.width: entry.role === "tool" ? 1 : 0
                            border.color: Qt.rgba(root.cText.r, root.cText.g, root.cText.b, 0.12)

                            TextEdit {
                                id: body
                                anchors.centerIn: parent
                                width: Math.min(implicitWidth, view.width * (bubble.mine ? 0.85 : 1.0) - 28)
                                readOnly: true
                                selectByMouse: true
                                wrapMode: TextEdit.Wrap
                                textFormat: entry.role === "agent" ? TextEdit.MarkdownText : TextEdit.PlainText
                                text: entry.role === "tool" ? "⚙ " + entry.text : entry.text
                                color: bubble.small ? root.cSubtle
                                     : entry.role === "error" ? root.cError
                                     : root.cText
                                selectionColor: root.cAccent
                                font.family: root.fontFamily
                                font.pixelSize: bubble.small ? 12 : 14
                                font.italic: entry.role === "info"
                            }
                        }
                    }
                }

                // ── Permission requests ──
                Repeater {
                    model: permissions
                    delegate: Rectangle {
                        id: card
                        required property string pid
                        required property string title
                        required property string options
                        Layout.fillWidth: true
                        implicitHeight: cardColumn.implicitHeight + 24
                        radius: 14
                        color: root.cSurf
                        border.width: 1
                        border.color: Qt.rgba(root.cAccent.r, root.cAccent.g, root.cAccent.b, 0.5)
                        Column {
                            id: cardColumn
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
                            spacing: 8
                            Text {
                                text: root.tr("permission")
                                color: root.cSubtle
                                font.family: root.fontFamily
                                font.pixelSize: 11
                            }
                            Text {
                                width: parent.width
                                text: card.title
                                wrapMode: Text.Wrap
                                color: root.cText
                                font.family: root.fontFamily
                                font.pixelSize: 14
                            }
                            Flow {
                                width: parent.width
                                spacing: 8
                                Repeater {
                                    model: JSON.parse(card.options)
                                    delegate: PillButton {
                                        required property var modelData
                                        label: modelData.name
                                        accent: modelData.kind.startsWith("allow")
                                        onClicked: root.sendOp({ op: "permission", id: card.pid, option: modelData.id })
                                    }
                                }
                            }
                        }
                    }
                }

                // ── Input ──
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(Math.max(input.implicitHeight + 20, 48), 160)
                    radius: 14
                    color: root.cSurf
                    border.width: 1
                    border.color: input.activeFocus ? Qt.rgba(root.cAccent.r, root.cAccent.g, root.cAccent.b, 0.5)
                                                    : "transparent"
                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 10
                        TextArea {
                            id: input
                            focus: root.open
                            wrapMode: TextArea.Wrap
                            placeholderText: root.busy ? root.tr("placeholderBusy") : root.tr("placeholder")
                            placeholderTextColor: root.cSubtle
                            color: root.cText
                            font.family: root.fontFamily
                            font.pixelSize: 14
                            background: null
                            Keys.onPressed: event => {
                                if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                        && !(event.modifiers & Qt.ShiftModifier)) {
                                    if (!root.busy) { root.send(input.text); input.text = ""; }
                                    event.accepted = true;
                                } else if (event.key === Qt.Key_Escape) {
                                    root.open = false;
                                    event.accepted = true;
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    component PillButton: Rectangle {
        id: pill
        property string label
        property bool accent: false
        signal clicked()
        implicitWidth: pillText.implicitWidth + 28
        implicitHeight: 30
        radius: 15
        color: accent ? (pillArea.containsMouse ? Qt.lighter(root.cAccent, 1.1) : root.cAccent)
                      : (pillArea.containsMouse ? root.cSurf2 : root.cSurf)
        Text {
            id: pillText
            anchors.centerIn: parent
            text: pill.label
            color: pill.accent ? root.cBase : root.cText
            font.family: root.fontFamily
            font.pixelSize: 12
        }
        MouseArea {
            id: pillArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: pill.clicked()
        }
    }
}
