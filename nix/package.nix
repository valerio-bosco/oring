{ lib, stdenvNoCC, makeWrapper, python3, quickshell, tesseract
, hyprland, grim, playerctl, libnotify, wtype, wlrctl, wl-clipboard
}:

let
  python = python3.withPackages (ps: [ ps.mcp ps.httpx ]);
  ocr = tesseract.override { enableLanguages = [ "eng" "ita" ]; };
  # tools used by the desktop MCP server
  desktopTools = [ hyprland grim playerctl libnotify ocr wtype wlrctl wl-clipboard ];
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
      --suffix PATH : ${lib.makeBinPath desktopTools}
    runHook postInstall
  '';

  meta = {
    description = "Agent panel for the serpantinum shell: Claude Code, ACP agents and local models";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "oring";
  };
}
