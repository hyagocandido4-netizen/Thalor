# Package RCF-3A

- corrige compatibilidade legacy do comando `sync` no control-plane
- restaura suporte a `--base-ref`, `--write-manifest`, `--strict-clean` e `--strict-base-ref`
- normaliza caminhos path-like para formato POSIX em surfaces/metadata cross-platform
- elimina falhas observadas no pytest no Windows após o RCF-3
