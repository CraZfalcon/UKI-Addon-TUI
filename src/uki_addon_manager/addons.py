"""Addon definitions, the user's selection, and the dependency tree."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class AddonError(Exception):
    """Something is wrong with the definitions or with what was asked for."""


class Missing(Exception):
    """Part of a dependency tree isn't available on this board."""


# The first line of the selection file.
HEADER = '# Written by uki-addon-manager. Lines marked "dependency" were added automatically.\n'


class Paths:
    """Where everything lives. Tests point root at a temporary directory."""

    def __init__(self, root: Path = Path("/")):
        self.root = root
        self.conf_dir = root / "etc/uki-addon-manager/conf.d"  # one .yaml file per addon
        self.selection_file = root / "etc/kernel/uki-optional-addons"  # the files to install, in load order
        self.compatible_file = root / "proc/device-tree/compatible"
        self.modules_dir = root / "usr/lib/modules"  # <version>/$KERNEL_IMAGE.extra.optional.d/: each kernel's addons


@dataclass
class Addon:
    """One signed .addon.efi file, described by its own .yaml file."""

    name: str
    filename: str
    provides: list[str]
    desc: str = ""
    depends_on: list[str] = field(default_factory=list)
    compatibility: list[str] = field(default_factory=list)

    def __post_init__(self):
        if "/" in self.filename or not self.filename.endswith(".addon.efi"):
            raise AddonError("filename must be a plain file name ending in .addon.efi")
        for key in ("provides", "depends_on", "compatibility"):
            if not isinstance(getattr(self, key), list):
                raise AddonError(f'{key} must be a list, e.g. ["media_carrier"]')

    def fits(self, board: list[str] | None) -> bool:
        """True if the board is unknown, the addon fits any board, or a compatible string matches."""
        return board is None or not self.compatibility or any(c in board for c in self.compatibility)


def load_addons(conf_dir: Path) -> list[Addon]:
    """Read every .yaml file in conf_dir. Each file describes one addon."""
    addons = []
    for path in sorted([*conf_dir.glob("*.yaml"), *conf_dir.glob("*.yml")]):
        try:
            addons.append(Addon(**yaml.safe_load(path.read_text(encoding="utf-8"))))
        except (yaml.YAMLError, TypeError, AddonError) as e:
            raise AddonError(f"{path}: {e}") from e
    return addons


def read_board(path: Path) -> list[str] | None:
    """The board's compatible strings (NUL-separated), or None if unknown, e.g. on a PC."""
    if not path.exists():
        return None
    return [s for s in path.read_bytes().decode().split("\0") if s]


def available_files(modules_dir: Path) -> set[str] | None:
    """The addon files that every installed kernel has in its $KERNEL_IMAGE.extra.optional.d/.

    None if no kernel has a store yet (e.g. on a PC), so there's nothing to check against.
    """
    stores = [s for s in modules_dir.glob("*/*.extra.optional.d") if s.is_dir()]
    if not stores:
        return None
    return set.intersection(*({p.name for p in store.iterdir()} for store in stores))


def read_selection(path: Path) -> list[str]:
    """The file names the user selected. Lines marked "# dependency" were added by this tool and are skipped."""
    if not path.exists():
        return []
    selected = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, comment = line.partition("#")
        if name.strip() and comment.strip() != "dependency":
            selected.append(name.strip())
    return selected


def write_selection(path: Path, install: list[Addon], selected: list[str]) -> None:
    """Write every file to install, in load order. Files the user didn't select are marked as dependencies."""
    lines = [a.filename if a.filename in selected else f"{a.filename}    # dependency" for a in install]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "".join(f"{line}\n" for line in lines), encoding="utf-8")


def providers_for(addons: list[Addon], board: list[str] | None) -> dict[str, Addon]:
    """Map each ID to the addon that provides it on this board."""
    providers: dict[str, Addon] = {}
    for addon in addons:
        if not addon.fits(board):
            continue
        for addon_id in addon.provides:
            if addon_id in providers:
                raise AddonError(f"'{addon_id}' is provided by both {providers[addon_id].filename} and {addon.filename}")
            providers[addon_id] = addon
    return providers


def tree(addon: Addon, providers: dict[str, Addon], available: set[str], chain: tuple = ()) -> list[Addon]:
    """addon plus everything it depends on, dependencies first.

    Raises Missing if part of the tree has no addon on this board, or its file isn't available.
    """
    if addon in chain:
        raise AddonError("dependency loop: " + " -> ".join(a.filename for a in (*chain, addon)))
    if addon.filename not in available:
        raise Missing(f"{addon.filename} isn't in every installed kernel's addon store")
    result = []
    for addon_id in addon.depends_on:
        if addon_id not in providers:
            raise Missing(f"no addon for this board provides '{addon_id}'")
        result += tree(providers[addon_id], providers, available, (*chain, addon))
    return result + [addon]


def resolve(selected: list[str], addons: list[Addon], available: set[str], board: list[str] | None) -> tuple[list[Addon], list[str]]:
    """Which addons to install, in load order, plus a warning for each tree that was left out.

    selected: the file names the user chose
    available: the file names every installed kernel has (see available_files)

    Each selected file's whole tree is installed, or none of it. A dependency
    that several trees share is installed once.
    """
    providers = providers_for(addons, board)
    by_filename = {a.filename: a for a in addons if a.fits(board)}
    install: list[Addon] = []
    warnings: list[str] = []
    for filename in selected:
        try:
            if filename not in by_filename:
                raise Missing("not an addon for this board")
            for addon in tree(by_filename[filename], providers, available):
                if addon not in install:
                    install.append(addon)
        except Missing as e:
            warnings.append(f"{filename} not installed: {e}")
    return install, warnings
