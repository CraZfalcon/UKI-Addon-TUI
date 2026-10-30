"""
uki-addon-manager: a whiptail menu, and the command line underneath it.

Without a command it opens the menu. Commands: list, enable, disable.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from .addons import (
    AddonError, Paths, available_files, load_addons, providers_for, read_board, read_selection, resolve, write_selection,
)

# Without a version and image, kernel-install uses the running kernel and /usr/lib/modules/<version>/vmlinuz.
KERNEL_INSTALL = ["kernel-install", "add"]
LATER = "To put this on the boot partition, run the following command and then reboot:\n  sudo kernel-install add"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="uki-addon-manager",
        description="Choose which DeviceTree overlay addons are installed. Without a command, a menu opens.",
    )
    parser.add_argument("--root", type=Path, default=Path("/"), help="work on another root directory, e.g. for testing")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("list", help="show the addons for this board")
    for command, verb in [("enable", "select"), ("disable", "deselect")]:
        p = commands.add_parser(command, help=f"{verb} addons")
        p.add_argument("ids", nargs="+", metavar="ID", help="an ID from 'uki-addon-manager list'")
        p.add_argument("--apply", action="store_true", help="also put it on the boot partition now (runs kernel-install)")
    args = parser.parse_args(argv)

    paths = Paths(args.root)
    try:
        if args.command is None:
            menu(paths)
        elif args.command == "list":
            show_list(paths)
        else:
            change = enable if args.command == "enable" else disable
            print(change(paths, args.ids))
            if args.apply:
                apply()
            else:
                print(LATER)
    except PermissionError as e:
        print(f"error: {e} (try sudo)", file=sys.stderr)
        return 1
    except (AddonError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def menu(paths: Paths) -> None:
    """A whiptail checklist of the addons for this board. Saving works like enable/disable."""
    board = read_board(paths.compatible_file)
    shown = [a for a in load_addons(paths.conf_dir) if a.fits(board)]
    if not shown:
        raise AddonError("no addons for this board")
    selected = read_selection(paths.selection_file)

    items = []
    for addon in shown:
        needs = f" (needs {', '.join(addon.depends_on)})" if addon.depends_on else ""
        items += [addon.provides[0], addon.name + needs, "ON" if addon.filename in selected else "OFF"]
    rows = min(len(shown), 12)
    answer = whiptail(
        "--separate-output", "--checklist",
        "Select the overlays to load at boot.\nSpace ticks a box, Tab moves to the buttons.",
        str(rows + 9), "78", str(rows), *items,
    )
    if answer.returncode != 0:  # Cancel or Esc: change nothing
        return

    # The menu shows IDs; the selection file holds file names.
    ticked = answer.stderr.split()
    summary = save(paths, [a.filename for a in shown if a.provides[0] in ticked])
    question = "\n\nPut this on the boot partition now? This runs kernel-install, which rebuilds the UKI."
    if whiptail("--yes-button", "Apply", "--no-button", "Later", "--yesno", summary + question, "16", "78").returncode:
        print(LATER)
    else:
        apply()


def whiptail(*args: str) -> subprocess.CompletedProcess:
    """Run whiptail. It draws on the terminal and writes its answer to stderr."""
    try:
        return subprocess.run(["whiptail", "--title", "uki-addon-manager", *args], stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise AddonError("whiptail isn't installed (sudo apt install whiptail)") from None


def apply() -> None:
    """Run kernel-install for the running kernel. It rebuilds the UKI, and the plugin copies the listed addons."""
    print("Running kernel-install. It rebuilds the UKI, so this can take a minute ...", flush=True)
    try:
        result = subprocess.run(KERNEL_INSTALL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        raise AddonError("kernel-install isn't installed") from None
    if result.returncode != 0:
        raise AddonError(f"kernel-install failed, so the selection is saved but not applied:\n{result.stdout}")
    print("Done. Reboot to load the overlays.")


def show_list(paths: Paths) -> None:
    board = read_board(paths.compatible_file)
    selected = read_selection(paths.selection_file)
    for addon in load_addons(paths.conf_dir):
        if addon.fits(board):
            mark = "[x]" if addon.filename in selected else "[ ]"
            needs = f"  (needs {', '.join(addon.depends_on)})" if addon.depends_on else ""
            print(f"{mark} {', '.join(addon.provides):<15} {addon.name}{needs}")


def enable(paths: Paths, ids: list[str]) -> str:
    providers = providers_for(load_addons(paths.conf_dir), read_board(paths.compatible_file))
    unknown = [i for i in ids if i not in providers]
    if unknown:
        raise AddonError(f"no addon for this board provides {', '.join(unknown)} (see 'uki-addon-manager list')")
    files = [providers[i].filename for i in ids]
    return save(paths, list(dict.fromkeys(read_selection(paths.selection_file) + files)))


def disable(paths: Paths, ids: list[str]) -> str:
    providers = providers_for(load_addons(paths.conf_dir), read_board(paths.compatible_file))
    selected = read_selection(paths.selection_file)
    unknown = [i for i in ids if i not in providers or providers[i].filename not in selected]
    if unknown:
        raise AddonError(f"not selected: {', '.join(unknown)}")
    files = {providers[i].filename for i in ids}
    return save(paths, [f for f in selected if f not in files])


def save(paths: Paths, selected: list[str]) -> str:
    """Write the selected files plus their dependencies, and describe what that means."""
    addons = load_addons(paths.conf_dir)
    # Only list trees that every installed kernel has all files for. Without any store there's nothing to check.
    available = available_files(paths.modules_dir)
    files = {a.filename for a in addons} if available is None else available
    install, warnings = resolve(selected, addons, files, read_board(paths.compatible_file))
    write_selection(paths.selection_file, install, selected)
    id_of = {a.filename: a.provides[0] for a in addons}
    extra = [a.provides[0] for a in install if a.filename not in selected]

    lines = [f"Selected: {', '.join(id_of.get(f, f) for f in selected) or '(nothing)'}"]
    if extra:
        lines.append(f"Also included, as dependencies: {', '.join(extra)}")
    lines += [f"warning: {w}" for w in warnings]
    if available is None:
        lines.append("note: no kernel has an addon store (*.extra.optional.d) yet, so the files weren't checked")
    return "\n".join(lines)
