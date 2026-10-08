#!/usr/bin/env python3

import sys
from pathlib import Path


WORKFLOW_PATH = Path(".github/workflows/flutter-build.yml")

PATCH_MARKER = "# PERSONAL_BUILD_SECRETS_PATCH"
RELEASE_MARKER = "# PERSONAL_BUILD_PRIVATE_RELEASE"

CHECKOUT_NEEDLE = "actions/checkout@"
RELEASE_NEEDLE = "softprops/action-gh-release@"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def is_active_line(line: str) -> bool:
    stripped = line.lstrip()
    return bool(stripped) and not stripped.startswith("#")


def is_step_start(line: str, indent: int) -> bool:
    return (
        indent_of(line) == indent
        and line.lstrip().startswith("- ")
    )


def find_step_start(lines: list[str], uses_index: int) -> tuple[int, int]:
    """
    Find the beginning of the GitHub Actions step containing a `uses:` line.
    Supports both:

      - uses: actions/checkout@...

    and:

      - name: Checkout source code
        uses: actions/checkout@...
    """

    line = lines[uses_index]
    stripped = line.lstrip()
    uses_indent = indent_of(line)

    if stripped.startswith("- uses:"):
        return uses_index, uses_indent

    expected_step_indent = uses_indent - 2

    if expected_step_indent < 0:
        fail(
            f"Unable to determine step indentation near line "
            f"{uses_index + 1}."
        )

    for index in range(uses_index - 1, -1, -1):
        candidate = lines[index]

        if is_step_start(candidate, expected_step_indent):
            return index, expected_step_indent

        if (
            candidate.strip()
            and indent_of(candidate) < expected_step_indent
        ):
            break

    fail(
        f"Unable to locate the start of the workflow step near "
        f"line {uses_index + 1}."
    )


def find_step_end(
    lines: list[str],
    step_start: int,
    step_indent: int,
) -> int:
    """
    Return the index at which the current step ends.
    The returned position is suitable for list.insert().
    """

    for index in range(step_start + 1, len(lines)):
        line = lines[index]

        if not line.strip():
            continue

        current_indent = indent_of(line)

        if is_step_start(line, step_indent):
            return index

        if (
            current_indent < step_indent
            and not line.lstrip().startswith("#")
        ):
            return index

    return len(lines)


def find_active_occurrences(
    lines: list[str],
    needle: str,
) -> list[int]:
    return [
        index
        for index, line in enumerate(lines)
        if needle in line and is_active_line(line)
    ]


def inject_secret_patch_steps(lines: list[str]) -> tuple[list[str], int]:
    checkout_indexes = find_active_occurrences(
        lines,
        CHECKOUT_NEEDLE,
    )

    if not checkout_indexes:
        fail(
            "No active actions/checkout step was found in "
            "flutter-build.yml. Upstream structure may have changed."
        )

    marker_count = sum(PATCH_MARKER in line for line in lines)

    if marker_count:
        if marker_count == len(checkout_indexes):
            print(
                f"Secret patch steps already present "
                f"({marker_count} checkout step(s))."
            )
            return lines, marker_count

        fail(
            "A partially injected secret-patch configuration was found: "
            f"{marker_count} marker(s) for "
            f"{len(checkout_indexes)} checkout step(s). "
            "Refusing to modify the workflow automatically."
        )

    # Work backwards so insertion does not invalidate earlier indexes.
    for checkout_index in reversed(checkout_indexes):
        step_start, step_indent = find_step_start(
            lines,
            checkout_index,
        )

        step_end = find_step_end(
            lines,
            step_start,
            step_indent,
        )

        base = " " * step_indent
        child = " " * (step_indent + 2)
        grandchild = " " * (step_indent + 4)

        patch_step = [
            f"{base}- name: Apply personal RustDesk server configuration\n",
            f"{child}{PATCH_MARKER}\n",
            f"{child}shell: bash\n",
            f"{child}env:\n",
            f"{grandchild}RENDEZVOUS_SERVER: "
            "${{ secrets.RENDEZVOUS_SERVER }}\n",
            f"{grandchild}RS_PUB_KEY: "
            "${{ secrets.RS_PUB_KEY }}\n",
            f"{child}run: |\n",
            f"{grandchild}git submodule update --init --recursive "
            "libs/hbb_common\n",
            f"{grandchild}python3 "
            ".github/scripts/patch-secrets.py\n",
        ]

        lines[step_end:step_end] = patch_step

    print(
        f"Injected secret patch after "
        f"{len(checkout_indexes)} checkout step(s)."
    )

    return lines, len(checkout_indexes)


def inject_private_release_targets(
    lines: list[str],
) -> tuple[list[str], int]:
    release_indexes = find_active_occurrences(
        lines,
        RELEASE_NEEDLE,
    )

    if not release_indexes:
        fail(
            "No active softprops/action-gh-release step was found. "
            "Upstream release workflow may have changed."
        )

    already_patched = 0

    # Work backwards so insertions do not invalidate earlier indexes.
    for release_index in reversed(release_indexes):
        step_start, step_indent = find_step_start(
            lines,
            release_index,
        )

        step_end = find_step_end(
            lines,
            step_start,
            step_indent,
        )

        block = lines[step_start:step_end]

        if any(RELEASE_MARKER in line for line in block):
            already_patched += 1
            continue

        with_index = None

        for index in range(release_index + 1, step_end):
            stripped = lines[index].strip()

            if stripped == "with:":
                with_index = index
                break

        if with_index is None:
            fail(
                "A softprops/action-gh-release step without a `with:` "
                f"section was found near line {release_index + 1}."
            )

        with_indent = indent_of(lines[with_index])
        key_indent = with_indent + 2

        # Fail safely if upstream starts defining its own target
        # repository or token. We do not want to overwrite a future
        # upstream security decision silently.
        for line in lines[with_index + 1:step_end]:
            if not is_active_line(line):
                continue

            if indent_of(line) != key_indent:
                continue

            stripped = line.strip()

            if stripped.startswith("repository:"):
                fail(
                    "Upstream now defines a repository target in a "
                    "release step. Manual review is required."
                )

            if stripped.startswith("token:"):
                fail(
                    "Upstream now defines a token in a release step. "
                    "Manual review is required."
                )

        prefix = " " * key_indent

        release_config = [
            f"{prefix}{RELEASE_MARKER}\n",
            f"{prefix}repository: "
            "${{ vars.PRIVATE_RELEASE_REPO }}\n",
            f"{prefix}token: "
            "${{ secrets.RELEASE_TOKEN }}\n",
        ]

        lines[with_index + 1:with_index + 1] = release_config

    patched_count = len(release_indexes)

    if already_patched:
        print(
            f"{already_patched} private release target(s) "
            "were already configured."
        )

    print(
        f"Private release target configured for "
        f"{patched_count} active release step(s)."
    )

    return lines, patched_count


def validate_result(
    lines: list[str],
    checkout_count: int,
    release_count: int,
) -> None:
    patch_markers = sum(
        PATCH_MARKER in line
        for line in lines
    )

    release_markers = sum(
        RELEASE_MARKER in line
        for line in lines
    )

    private_repo_refs = sum(
        "${{ vars.PRIVATE_RELEASE_REPO }}" in line
        for line in lines
    )

    release_token_refs = sum(
        "${{ secrets.RELEASE_TOKEN }}" in line
        for line in lines
    )

    if patch_markers != checkout_count:
        fail(
            "Validation failed: secret patch marker count does not "
            "match checkout count."
        )

    if release_markers != release_count:
        fail(
            "Validation failed: private release marker count does not "
            "match release action count."
        )

    if private_repo_refs != release_count:
        fail(
            "Validation failed: PRIVATE_RELEASE_REPO reference count "
            "does not match release action count."
        )

    if release_token_refs != release_count:
        fail(
            "Validation failed: RELEASE_TOKEN reference count does not "
            "match release action count."
        )


def main() -> None:
    if not WORKFLOW_PATH.is_file():
        fail(
            f"{WORKFLOW_PATH} was not found. "
            "Run this script from the RustDesk repository root."
        )

    original_text = WORKFLOW_PATH.read_text(
        encoding="utf-8"
    )

    lines = original_text.splitlines(keepends=True)

    lines, checkout_count = inject_secret_patch_steps(lines)

    lines, release_count = inject_private_release_targets(lines)

    validate_result(
        lines,
        checkout_count,
        release_count,
    )

    new_text = "".join(lines)

    if new_text == original_text:
        print("flutter-build.yml is already fully configured.")
        return

    WORKFLOW_PATH.write_text(
        new_text,
        encoding="utf-8",
    )

    print(
        "flutter-build.yml personal build injection completed "
        "successfully."
    )
    print(
        f"Checkout steps configured: {checkout_count}"
    )
    print(
        f"Private release steps configured: {release_count}"
    )


if __name__ == "__main__":
    main()
