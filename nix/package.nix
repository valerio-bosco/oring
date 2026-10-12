{ lib, stdenvNoCC, makeWrapper, python3, quickshell, tesseract, coreutils
, hyprland, grim, playerctl, libnotify, wtype, wlrctl, wl-clipboard
, bubblewrap, socat
}:

let
  python = python3.withPackages (ps: [ ps.mcp ps.httpx ]);
  ocr = tesseract.override { enableLanguages = [ "eng" "ita" ]; };
  # tools used by the desktop MCP server
  desktopTools = [ hyprland grim playerctl libnotify ocr wtype wlrctl wl-clipboard ];
  # Claude Code's sandbox (sandbox = true, the default for claude-cli agents)
  sandboxTools = [ bubblewrap socat ];
in
stdenvNoCC.mkDerivation {
  pname = "oring";
  version = "0.1.0";
  src = lib.cleanSource ../.;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/oring $out/bin
    cp -r oring ui mcp bin $out/share/oring/
    makeWrapper $out/share/oring/bin/oring $out/bin/oring \
      --set ORING_SHARE $out/share/oring \
      --set ORING_PYTHON ${python}/bin/python3 \
      --set-default ORING_QUICKSHELL ${quickshell}/bin/quickshell \
      --set-default ORING_DESKTOP_MCP "$out/bin/oring mcp" \
      --set-default ORING_LEVEL_HELPER $out/libexec/oring-level-helper \
      --suffix PATH : ${lib.makeBinPath (desktopTools ++ sandboxTools)}

    # access level (oring/level.py): root helper run by pkexec with a fixed PATH, and its polkit
    # action (picked up by polkit when the package is in environment.systemPackages)
    mkdir -p $out/libexec $out/share/polkit-1/actions
    makeWrapper $out/share/oring/bin/oring-level-helper $out/libexec/oring-level-helper \
      --set PATH ${lib.makeBinPath [ coreutils ]} --unset ORING_LEVEL_FILE
    substitute ${./org.oring.set-level.policy} $out/share/polkit-1/actions/org.oring.set-level.policy \
      --subst-var-by helper $out/libexec/oring-level-helper
    runHook postInstall
  '';

  meta = {
    description = "Agent panel for the serpantinum shell: Claude Code, ACP agents and local models";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "oring";
  };
}
