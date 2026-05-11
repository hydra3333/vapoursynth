from __future__ import annotations

import os
import re
import sys
from enum import StrEnum, auto, unique
from pathlib import Path, PurePath


@unique
class Shell(StrEnum):
    BASH = auto()
    FISH = auto()
    ZSH = auto()
    CSH = auto()
    KSH = auto()

    @classmethod
    def from_env(cls) -> Shell | None:
        """Determine the user's current shell from the environment.

        First checks shell-specific environment variables which are set by the respective shells. This takes priority
        over `SHELL` because on Unix, `SHELL` refers to the user's login shell, not the currently running shell.

        Falls back to parsing the `SHELL` environment variable if no shell-specific variables are found.

        Returns `None` if the shell cannot be determined.
        """
        if os.getenv("FISH_VERSION"):
            return cls.FISH
        elif os.getenv("BASH_VERSION"):
            return cls.BASH
        elif os.getenv("ZSH_VERSION"):
            return cls.ZSH
        elif os.getenv("KSH_VERSION"):
            return cls.KSH
        elif env_shell := os.getenv("SHELL"):
            return cls.from_shell_path(env_shell)
        else:
            return cls.from_parent_process()

    @classmethod
    def from_parent_process(cls) -> Shell | None:
        """Attempt to determine the shell from the parent process.

        This is a fallback method for when environment variables don't provide enough information about the current
        shell. It looks at the parent process to try to identify which shell is running.

        This method currently only works on Unix-like systems. On other platforms, it returns `None`.
        """
        if sys.platform == "linux":
            ppid = os.getppid()

            try:
                exe_path = Path(f"/proc/{ppid}/exe").readlink()
                if shell := cls.from_shell_path(exe_path):
                    return shell
            except Exception:
                pass

            try:
                comm = Path(f"/proc/{ppid}/comm").read_text().strip()
                if shell := cls.from_shell_path(comm):
                    return shell
            except Exception:
                pass

        return None

    @classmethod
    def from_shell_path(cls, path: str) -> Shell | None:
        """Parse a shell from a path to the executable for the shell."""
        match PurePath(path).stem:
            case "bash" | "sh":
                return cls.BASH
            case "zsh":
                return cls.ZSH
            case "fish":
                return cls.FISH
            case "csh":
                return cls.CSH
            case "ksh":
                return cls.KSH
            case _:
                return None

    def configuration_files(self) -> list[Path]:
        """Return the configuration files that should be modified."""
        home = Path.home()

        match self:
            case Shell.BASH:
                """On Bash, we need to update both `.bashrc` and `.bash_profile`. The former is sourced for non-login
                shells, and the latter is sourced for login shells.

                In lieu of `.bash_profile`, shells will also respect `.bash_login` and `.profile`, if they exist. So we
                respect those too.
                """
                login = next(
                    (home / rc for rc in [".bash_profile", ".bash_login", ".profile"] if (home / rc).is_file()),
                    home / ".bash_profile",
                )
                return [login, home / ".bashrc"]
            case Shell.KSH:
                """On Ksh it's standard POSIX `.profile` for login shells, and `.kshrc` for non-login."""
                return [home / ".profile", home / ".kshrc"]
            case Shell.ZSH:
                """On Zsh, we only need to update `.zshenv`. This file is sourced for both login and non-login shells."""
                zdotdir = os.getenv("ZDOTDIR")

                if zdotdir:
                    zshenv = Path(zdotdir) / ".zshenv"
                    if zshenv.is_file():
                        return [zshenv]

                zshenv = home / ".zshenv"
                if zshenv.is_file():
                    return [zshenv]

                if zdotdir:
                    return [Path(zdotdir) / ".zshenv"]
                else:
                    return [home / ".zshenv"]
            case Shell.FISH:
                """On Fish, we only need to update `config.fish`. This file is sourced for both login and non-login
                shells. However, we must respect Fish's logic, which reads from `$XDG_CONFIG_HOME/fish/config.fish` if
                set, and `~/.config/fish/config.fish` otherwise.
                """
                if xdg_config_home := os.getenv("XDG_CONFIG_HOME"):
                    return [Path(xdg_config_home) / "fish/config.fish"]
                else:
                    return [home / ".config/fish/config.fish"]
            case Shell.CSH:
                """On Csh, we need to update both `.cshrc` and `.login`, like Bash."""
                return [home / ".cshrc", home / ".login"]

    def export_path(self, path: str) -> str:
        """Return the command necessary to export VSSCRIPT_PATH in this shell."""
        match self:
            case Shell.BASH | Shell.ZSH | Shell.KSH:
                return f'export VSSCRIPT_PATH="{path}"'
            case Shell.FISH:
                return f'set -gx VSSCRIPT_PATH "{path}"'
            case Shell.CSH:
                return f'setenv VSSCRIPT_PATH "{path}"'

    def match_pattern(self) -> re.Pattern:
        """Return the regular expression pattern to match existing command in the configuration files."""
        match self:
            case Shell.BASH | Shell.ZSH | Shell.KSH:
                return re.compile(r"^export\s+VSSCRIPT_PATH\s*=")
            case Shell.FISH:
                return re.compile(r"^set\s+(?:-\S+\s+)*VSSCRIPT_PATH(?:\s|$)")
            case Shell.CSH:
                return re.compile(r"^setenv\s+VSSCRIPT_PATH(?:\s|$)")


def _update_contents(contents: str, command: str, pattern: re.Pattern) -> str | None:
    lines = contents.splitlines(keepends=True)
    new_lines: list[str] = []
    replaced = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("#"):
            new_lines.append(line)
            continue

        if command in stripped:
            return None

        if pattern.match(stripped):
            if not replaced:
                new_lines.append(command + "\n")
                replaced = True
        else:
            new_lines.append(line)

    if replaced:
        return "".join(new_lines)
    else:
        return f"{contents}\n# VapourSynth\n{command}\n"


def update_shell(path: str) -> None:
    shell = Shell.from_env()
    if not shell:
        sys.exit("The current shell could not be determined")

    files = shell.configuration_files()
    command = shell.export_path(path)
    pattern = shell.match_pattern()
    updated = False

    for file in files:
        if file.is_file():
            try:
                contents = file.read_text()
            except Exception:
                sys.exit(f"Failed to read configuration file: {file}")

            new_contents = _update_contents(contents, command, pattern)
            if not new_contents:
                continue

            try:
                file.write_text(new_contents)
            except Exception:
                sys.exit(f"Failed to write configuration file: {file}")

            print(f"Updated configuration file: {file}")
            updated = True
        else:
            file.parent.mkdir(parents=True, exist_ok=True)

            try:
                file.write_text(f"# VapourSynth\n{command}\n")
            except Exception:
                sys.exit(f"Failed to write configuration file: {file}")

            print(f"Created configuration file: {file}")
            updated = True

    if updated:
        print("Restart your shell to apply changes")
    else:
        print(f"The {shell.value} configuration files are already up-to-date")
