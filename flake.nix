{
  description = "oring: agent panel for the serpantinum shell (Quickshell + Hyprland)";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      pythonFor = pkgs: pkgs.python3.withPackages (ps: [ ps.mcp ps.httpx ps.pytest ]);
    in
    {
      packages = forAll (pkgs: rec {
        oring = pkgs.callPackage ./nix/package.nix { };
        default = oring;
        # used by scripts/check and scripts/dev
        dev-python = pythonFor pkgs;
        dev-quickshell = pkgs.quickshell;
      });

      homeManagerModules.default = import ./nix/hm-module.nix self;

      # `nix flake check`: the package builds and the test suite passes (fake agents only)
      checks = forAll (pkgs: {
        package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        tests = pkgs.runCommand "oring-tests" { nativeBuildInputs = [ (pythonFor pkgs) ]; } ''
          cp -r ${pkgs.lib.cleanSource ./.} src && chmod -R u+w src && cd src
          export HOME=$TMPDIR
          unset SSL_CERT_FILE NIX_SSL_CERT_FILE   # the sandbox points them to a missing file (httpx fails)
          python -m pytest -q -p no:cacheprovider tests
          touch $out
        '';
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pythonFor pkgs) pkgs.quickshell ];
        };
      });
    };
}
