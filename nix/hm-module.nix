self:
{ config, lib, pkgs, ... }:

let
  cfg = config.programs.oring;
  toml = pkgs.formats.toml { };
  configFile = toml.generate "oring.toml" cfg.settings;
in
{
  options.programs.oring = {
    enable = lib.mkEnableOption "oring, the agent panel for serpantinum";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      description = "The oring package.";
    };

    settings = lib.mkOption {
      type = toml.type;
      default = { };
      example = lib.literalExpression ''
        {
          language = "it";
          agents = [
            { id = "claude"; name = "Claude"; type = "claude-cli"; }
            { id = "gemini"; name = "Gemini"; type = "acp"; command = "gemini --acp"; }
            { id = "local"; name = "Qwen (local)"; type = "openai"; model = "qwen3:4b"; }
          ];
        }
      '';
      description = "Written to ~/.config/oring/config.toml. See config.example.toml.";
    };

    extraPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [ ];
      example = lib.literalExpression "[ pkgs.gemini-cli pkgs.codex-acp ]";
      description = "Agent CLIs made available to the daemon (in addition to the user's PATH).";
    };

    gateways.enable = lib.mkOption {
      type = lib.types.bool;
      default = cfg.settings ? gateways && cfg.settings.gateways != [ ];
      defaultText = lib.literalMD "true when `settings.gateways` is not empty";
      description = "Run the messaging gateways (`oring gateways`) as a user service.";
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      example = { ORING_WIDTH = "600"; };
      description = "Extra environment for the daemon and the panel (ORING_WIDTH, ORING_MARGIN_TOP, ORING_FONT, API keys, ...).";
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ];
    xdg.configFile."oring/config.toml" = lib.mkIf (cfg.settings != { }) { source = configFile; };

    systemd.user.services = let
      env = lib.mapAttrsToList (k: v: "${k}=${v}") cfg.environment;
      path = lib.concatStringsSep ":" [
        (lib.makeBinPath cfg.extraPackages)
        "/run/wrappers/bin"
        "/etc/profiles/per-user/${config.home.username}/bin"
        "${config.home.homeDirectory}/.nix-profile/bin"
        "/run/current-system/sw/bin"
      ];
    in {
      oring = {
        Unit = {
          Description = "oring agent daemon";
          After = [ "graphical-session.target" ];
          PartOf = [ "graphical-session.target" ];
          X-Restart-Triggers = [ "${configFile}" ];
        };
        Service = {
          ExecStart = "${cfg.package}/bin/oring daemon";
          Restart = "on-failure";
          Environment = [ "PATH=${path}" ] ++ env;
        };
        Install.WantedBy = [ "graphical-session.target" ];
      };
      oring-gateways = lib.mkIf cfg.gateways.enable {
        Unit = {
          Description = "oring messaging gateways";
          After = [ "oring.service" ];
          PartOf = [ "graphical-session.target" ];
          X-Restart-Triggers = [ "${configFile}" ];
        };
        Service = {
          ExecStart = "${cfg.package}/bin/oring gateways";
          Restart = "always";
          RestartSec = 10;
          Environment = env;
        };
        Install.WantedBy = [ "graphical-session.target" ];
      };
      oring-ui = {
        Unit = {
          Description = "oring panel";
          After = [ "graphical-session.target" "oring.service" ];
          PartOf = [ "graphical-session.target" ];
        };
        Service = {
          ExecStart = "${cfg.package}/bin/oring ui";
          Restart = "on-failure";
          Environment = env;
        };
        Install.WantedBy = [ "graphical-session.target" ];
      };
    };
  };
}
