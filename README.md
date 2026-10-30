# UKI-Addon-TUI
CLI/TUI for managing DeviceTree overlays on the Arduino UNO Q, using signed UKI addons and Secure Boot.

`uki-addon-manager` lets you choose overlays in a whiptail menu, or with the command line underneath it. Each addon is described by its own `.yaml` file in `/etc/uki-addon-manager/conf.d/`.

When you save, the tool builds the dependency tree of each selected overlay. A tree is listed only if all of it fits this board and every installed kernel has all of its files. Dependencies that several trees share are listed once. The result is written to `/etc/kernel/uki-optional-addons`: every `.addon.efi` file to install, in load order. A separate kernel-install plugin, [PyInstallScriptDTBO](https://github.com/lenn219/PyInstallScriptDTBO), copies those files to the boot partition for each kernel.

## Installation

On the board (Debian):

```sh
sudo apt install python3-pip python3-yaml whiptail
git clone https://github.com/CraZfalcon/UKI-Addon-TUI.git
sudo pip install --break-system-packages --no-deps ./UKI-Addon-TUI
```

- `--break-system-packages` is needed because Debian blocks system-wide pip installs by default.
- `--no-deps` keeps the PyYAML from apt instead of installing a second copy.

The addon definitions go in `/etc/uki-addon-manager/conf.d/`. To try it with the examples:

```sh
sudo mkdir -p /etc/uki-addon-manager/conf.d
sudo cp UKI-Addon-TUI/examples/conf.d/*.yaml /etc/uki-addon-manager/conf.d/
```

To get the selection onto the boot partition, also install the kernel-install plugin from [PyInstallScriptDTBO](https://github.com/lenn219/PyInstallScriptDTBO).

### Updating and uninstalling

```sh
git -C UKI-Addon-TUI pull                                                          # update
sudo pip install --break-system-packages --no-deps --force-reinstall ./UKI-Addon-TUI

sudo pip uninstall --break-system-packages uki-addon-manager                       # uninstall
```

Uninstalling leaves your addon definitions and selection in `/etc` alone. To remove those too, run `sudo rm -r /etc/uki-addon-manager /etc/kernel/uki-optional-addons`.

## Usage

Changing overlays takes two steps:

1. **Select** the overlays you want. The selection is saved in `/etc/kernel/uki-optional-addons`. You can do this in the menu (TUI) or with commands (CLI); both do exactly the same.
2. **Apply** the selection: `kernel-install add` copies it to the boot partition. Then reboot. You can apply straight after selecting, or later.

Selecting and applying need `sudo`, because they write to `/etc` and the boot partition. Only `list` works without it.

### The menu (TUI)

```sh
sudo uki-addon-manager
```

```
┌─────────────────┤ uki-addon-manager ├─────────────────┐
│ Select the overlays to load at boot.                  │
│ Space ticks a box, Tab moves to the buttons.          │
│                                                       │
│    [ ] dsi_display    DSI Display on Media Carrier (… │
│    [*] imx219_cs0     IMX219 Camera Slot 0 on Media … │
│    [ ] media_carrier  Media Carrier Board             │
│                                                       │
│             <Ok>                  <Cancel>            │
└───────────────────────────────────────────────────────┘
```

1. Tick the overlays you want with Space. You don't need to tick dependencies such as `media_carrier`. They're added automatically and stay unticked.
2. Press Tab to reach Ok, then Enter. Cancel or Esc closes the menu without changing anything.
3. A second screen shows what was saved and asks whether to put it on the boot partition now:
   - **Apply** runs `kernel-install add` (this takes a minute). Reboot afterwards.
   - **Later** only saves. Before you reboot, run `sudo kernel-install add` yourself.

### The commands (CLI)

The same actions as the menu, for scripts, SSH sessions or quick changes:

| Command | What it does |
|---|---|
| `uki-addon-manager list` | Shows the addons for this board. The first column is the ID you use in the other commands, and `[x]` marks the ones you selected. |
| `sudo uki-addon-manager enable ID…` | Selects one or more overlays. Their dependencies are added automatically. |
| `sudo uki-addon-manager disable ID…` | Deselects them. Dependencies that nothing needs any more are removed too. |

Add `--apply` to `enable` or `disable` to also put the selection on the boot partition right away. That's the same as Apply in the menu.

```
$ uki-addon-manager list
[ ] dsi_display     DSI Display on Media Carrier  (needs media_carrier)
[ ] imx219_cs0      IMX219 Camera Slot 0 on Media Carrier  (needs media_carrier)
[ ] media_carrier   Media Carrier Board

$ sudo uki-addon-manager enable imx219_cs0 --apply
Selected: imx219_cs0
Also included, as dependencies: media_carrier
Running kernel-install. It rebuilds the UKI, so this can take a minute ...
Done. Reboot to load the overlays.

$ sudo reboot
```

### When to apply

`--apply` (or Apply in the menu) always puts the *whole* current selection on the boot partition, not just the change you made in that command. It runs `kernel-install add` for the running kernel, which also rebuilds the UKI, so each apply takes a minute.

- **One change:** add `--apply`, then reboot.
- **Several changes in a row:** apply only once, at the end. Add `--apply` to the last command only, or run `sudo kernel-install add` after the last one:

  ```sh
  sudo uki-addon-manager enable imx219_cs0
  sudo uki-addon-manager enable dsi_display --apply
  ```

A selection that hasn't been applied changes nothing on the board, even after a reboot.

## Directories

On the board, in the order the data moves through them:

| Path | Written by | Contents |
|---|---|---|
| `/etc/uki-addon-manager/conf.d/` | you, or a package | One `.yaml` file per addon (see [Addon definitions](#addon-definitions)) |
| `/proc/device-tree/compatible` | the kernel | The board's compatible strings. Only addons that match are shown. |
| `/usr/lib/modules/<version>/vmlinuz.extra.optional.d/` | whoever ships the addons | The signed `.addon.efi` files for that kernel (`$KERNEL_IMAGE.extra.optional.d/`). This tool checks that every installed kernel has the files it lists. |
| `/etc/kernel/uki-optional-addons` | this tool | Every `.addon.efi` file to install, in load order. Dependencies are marked `# dependency`. |
| `<boot partition>/EFI/Linux/<entry-token>-<version>.efi.extra.d/` | the kernel-install plugin | Numbered copies (`00-…`, `01-…`) that systemd-stub loads at boot. The boot partition is usually `/boot/efi`. |

In this repository:

| Path | Contents |
|---|---|
| `src/uki_addon_manager/` | The tool. `addons.py` reads the YAML files and builds the dependency tree. `cli.py` has the menu and the commands. |
| `examples/conf.d/` | Example addon definitions |
| `pyproject.toml` | Makes it installable as the `uki-addon-manager` command |

## Addon definitions

Each addon gets its own `.yaml` file in `/etc/uki-addon-manager/conf.d/`:

```yaml
name: "IMX219 Camera Slot 0 on Media Carrier"   # required: shown in the menu
desc: "Example description"                     # optional
filename: "imx219-csi0-mc.addon.efi"            # required: the signed file in $KERNEL_IMAGE.extra.optional.d/
provides: ["imx219_cs0"]                        # required: the ID(s) used to select it
depends_on: ["media_carrier"]                   # optional: IDs of addons that must be loaded first
compatibility: ["arduino,imola"]                # optional: compatible strings it fits; empty = any board
```

- `provides`, `depends_on` and `compatibility` are lists, even with one item.
- A missing required field or an unknown field (a typo like `depends:`) is an error, and the error names the file.
- Two addons for the same board can't provide the same ID.
- The board's compatible strings are in `/proc/device-tree/compatible`. On the UNO Q, use `arduino,imola`.

See [examples/conf.d/](examples/conf.d/) for a carrier board with two add-ons that depend on it.
