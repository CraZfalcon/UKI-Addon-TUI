"""
uki-addon-manager: a menu (curses, with a whiptail box after it), and the command line underneath it.

Without a command it opens the menu. Commands: list, enable, disable.
"""

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import curses
except ImportError:  # Windows Python has no curses. Only the menu needs it.
    curses = None

from .addons import (
    Addon, AddonError, Paths, available_files, load_addons, providers_for, read_board, read_selection, resolve,
    write_selection,
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
    """A checklist of the addons for this board. Saving works like enable/disable."""
    board = read_board(paths.compatible_file)
    shown = [a for a in load_addons(paths.conf_dir) if a.fits(board)]
    if not shown:
        raise AddonError("no addons for this board")
    selected = read_selection(paths.selection_file)

    try:
        ticked = curses.wrapper(checklist, shown, selected, board)
    except KeyboardInterrupt:  # Ctrl+C: change nothing
        return
    if ticked is None:  # Esc: change nothing
        return

    summary = save(paths, ticked)
    question = "\n\nPut this on the boot partition now? This runs kernel-install, which rebuilds the UKI."
    if whiptail("--yes-button", "Apply", "--no-button", "Later", "--yesno", summary + question, "16", "78").returncode:
        print(LATER)
    else:
        apply()


def checklist(screen, shown: list[Addon], chosen: list[str], board: list[str] | None) -> list[str] | None:
    """The menu's list, drawn with curses. Returns the ticked file names, or None for Esc.

    chosen: the file names the user ticked. Dependencies are ticked automatically, and shown as [+].
    Typing filters the list on ID and name. Addons that are filtered out keep their tick.
    """

    # TODO: searching descriptions instead of just names and IDs, add info field about selected addon, mark incompatible addons(?) <- kernel version specific folders may invalidate this.

    curses.set_escdelay(25)  # otherwise Esc takes a second, while curses waits for an arrow key's code
    files = {a.filename for a in shown}
    id_width = max(len(a.provides[0]) for a in shown)
    search, pos, top = "", 0, 0
    while True:
        found = [a for a in shown if search.lower() in f"{a.provides[0]} {a.name}".lower()]
        needed = {a.filename for a in resolve(chosen, shown, files, board)[0]}  # the same tree that saving builds
        height, width = screen.getmaxyx()
        rows = max(height - 6, 1)
        pos = min(pos, max(len(found) - 1, 0))
        top = min(max(top, pos - rows + 1), pos)  # scroll so the highlighted line stays on screen

        screen.erase()
        try:  # a very small terminal has no room for some lines; skip them
            screen.addnstr(0, 1, "Select the overlays to load at boot.", width - 2)
            screen.addnstr(1, 1, f"Search: {search}", width - 2)
            if not found:
                screen.addnstr(3, 1, "No addon matches the search.", width - 2)
            for row, addon in enumerate(found[top:top + rows]):
                mark = "[*]" if addon.filename in chosen else "[+]" if addon.filename in needed else "[ ]"
                line = f"{mark} {addon.provides[0]:<{id_width}}  {addon.name}"
                line = line if len(line) <= width - 2 else line[:width - 5] + "..."  # never past the edge
                screen.addnstr(3 + row, 1, line, width - 2, curses.A_REVERSE if top + row == pos else 0)
            screen.addnstr(height - 2, 1, "[+] = needed by something you ticked", width - 2)
            screen.addnstr(height - 1, 1, "Type to search, Space ticks, Enter saves, Esc cancels", width - 2)
            screen.move(1, 9 + len(search))  # the cursor shows where typing goes
        except curses.error:
            pass

        key = screen.get_wch()
        if key == "\x1b":
            return None
        if key in ("\n", "\r", curses.KEY_ENTER):
            return [a.filename for a in shown if a.filename in chosen]
        if key == curses.KEY_UP:
            pos = max(pos - 1, 0)
        elif key == curses.KEY_DOWN:
            pos += 1
        elif key == " " and found:
            # Space on [+] makes it your own tick [*]. Unticking [*] leaves [+] while something else needs it.
            name = found[pos].filename
            chosen = [f for f in chosen if f != name] if name in chosen else [*chosen, name]
        elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            search, pos = search[:-1], 0
        elif isinstance(key, str) and key.isprintable():
            search, pos = search + key, 0


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
