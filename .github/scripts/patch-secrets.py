#!/usr/bin/env python3

import os
import re
import sys
from pathlib import Path


CONFIG_PATH = Path("libs/hbb_common/src/config.rs")

RENDEZVOUS_MARKER = "// PERSONAL_BUILD_RENDEZVOUS_SERVER"
PUBKEY_MARKER = "// PERSONAL_BUILD_RS_PUB_KEY"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def get_secret(name: str) -> str:
    value = os.environ.get(name)

    if value is None or value == "":
        fail(f"Required environment variable {name} is missing or empty.")

    # Secrets should be a single-line value.
    # Reject control characters instead of silently producing invalid Rust.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        fail(f"{name} contains control characters or line breaks.")

    return value


def rust_string(value: str) -> str:
    """
    Escape a Python string so it can safely be inserted inside
    a Rust normal string literal.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def main() -> None:
    if not CONFIG_PATH.is_file():
        fail(
            f"{CONFIG_PATH} was not found. "
            "Make sure hbb_common was checked out recursively before running this script."
        )

    rendezvous_server = get_secret("RENDEZVOUS_SERVER")
    rs_pub_key = get_secret("RS_PUB_KEY")

    text = CONFIG_PATH.read_text(encoding="utf-8")

    has_rendezvous_marker = RENDEZVOUS_MARKER in text
    has_pubkey_marker = PUBKEY_MARKER in text

    # Idempotency: if both patches are already present, do nothing.
    if has_rendezvous_marker and has_pubkey_marker:
        print("Personal RustDesk server configuration is already patched.")
        return

    # A partial patch is suspicious and should never be silently accepted.
    if has_rendezvous_marker != has_pubkey_marker:
        fail(
            "Only one personal-build marker was found. "
            "Refusing to continue with a partially patched config.rs."
        )

    rendezvous_pattern = re.compile(
        r'(?m)^(?P<indent>\s*)'
        r'pub static ref PROD_RENDEZVOUS_SERVER:\s*RwLock<String>\s*=\s*'
        r'RwLock::new\(""\.to_owned\(\)\);\s*$'
    )

    pubkey_pattern = re.compile(
        r'(?m)^(?P<indent>\s*)'
        r'pub const RS_PUB_KEY:\s*&str\s*=\s*'
        r'"(?:[^"\\]|\\.)*";\s*$'
    )

    rendezvous_matches = list(rendezvous_pattern.finditer(text))
    pubkey_matches = list(pubkey_pattern.finditer(text))

    if len(rendezvous_matches) != 1:
        fail(
            "Expected exactly one unmodified PROD_RENDEZVOUS_SERVER definition, "
            f"but found {len(rendezvous_matches)}. "
            "Upstream hbb_common may have changed."
        )

    if len(pubkey_matches) != 1:
        fail(
            "Expected exactly one RS_PUB_KEY definition, "
            f"but found {len(pubkey_matches)}. "
            "Upstream hbb_common may have changed."
        )

    escaped_server = rust_string(rendezvous_server)
    escaped_pubkey = rust_string(rs_pub_key)

    text, rendezvous_count = rendezvous_pattern.subn(
        lambda m: (
            f'{m.group("indent")}pub static ref PROD_RENDEZVOUS_SERVER: '
            f'RwLock<String> = RwLock::new("{escaped_server}".to_owned()); '
            f'{RENDEZVOUS_MARKER}'
        ),
        text,
        count=1,
    )

    text, pubkey_count = pubkey_pattern.subn(
        lambda m: (
            f'{m.group("indent")}pub const RS_PUB_KEY: &str = '
            f'"{escaped_pubkey}"; {PUBKEY_MARKER}'
        ),
        text,
        count=1,
    )

    if rendezvous_count != 1 or pubkey_count != 1:
        fail("Unexpected patch count. config.rs was not written.")

    CONFIG_PATH.write_text(text, encoding="utf-8")

    # Deliberately do NOT print secret values.
    print("Personal RustDesk server configuration patched successfully.")


if __name__ == "__main__":
    main()
