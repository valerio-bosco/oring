self:
{ config, lib, pkgs, ... }:

# System part of oring: the access level (oring/level.py). The level file lives in /var/lib/oring,
# owned by root; `oring admin full|sandbox` changes it through pkexec, which polkit allows only to
# the local active session with the admin password (nix/org.oring.set-level.policy).
let
  cfg = config.programs.oring.admin;
in
{
  options.programs.oring.admin = {
    enable = lib.mkEnableOption "the oring access level (sandbox / full), changeable only by an administrator at the machine";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      description = "The oring package (it provides the root helper and the polkit action).";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ];
    security.polkit.enable = true;
    systemd.tmpfiles.rules = [ "d /var/lib/oring 0755 root root -" ];
  };
}
