{
  description = "oring: agent panel for the serpantinum shell (Quickshell + Hyprland)";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        oring = pkgs.callPackage ./nix/package.nix { };
        default = oring;
      });

      homeManagerModules.default = import ./nix/hm-module.nix self;

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pkgs.python3.withPackages (ps: [ ps.mcp ps.httpx ])) pkgs.quickshell ];
        };
      });
    };
}
