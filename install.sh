#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    echo "Usage: $0 {all|codex|claude|grok|codex-macos}"
}

install_provider() {
    case "$1" in
        codex)
            bash "$ROOT_DIR/providers/codex/ubuntu-indicator/install.sh"
            ;;
        claude)
            bash "$ROOT_DIR/providers/claude/install.sh"
            ;;
        grok)
            bash "$ROOT_DIR/providers/grok/ubuntu-indicator/install.sh"
            ;;
        codex-macos)
            bash "$ROOT_DIR/providers/codex/macos-menubar/install.sh"
            ;;
        *)
            usage >&2
            return 2
            ;;
    esac
}

target="${1:-}"
case "$target" in
    all)
        for provider in codex claude grok; do
            echo
            echo "=== Installing $provider ==="
            install_provider "$provider"
        done
        ;;
    codex|claude|grok|codex-macos)
        install_provider "$target"
        ;;
    -h|--help)
        usage
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
