"""Read roller settings from a checkout of quirq-ai/infra-config, through its own loader (qqcfg)."""
from __future__ import annotations

import importlib.util
from pathlib import Path


class ConfigError(Exception):
    pass


def load(infra_config: str | Path) -> dict:
    """Every infra-config area, read with infra-config's `qqcfg.load`."""
    root = Path(infra_config)
    tool = root / "tools" / "qqcfg.py"
    if not tool.is_file():
        raise ConfigError(f"{root}: not an infra-config checkout (no tools/qqcfg.py)")
    spec = importlib.util.spec_from_file_location("qqcfg", tool)
    qqcfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qqcfg)
    try:
        return qqcfg.load(root)
    except qqcfg.ConfigError as e:
        raise ConfigError(str(e)) from None


def rollers(cfg: dict, source: str = "infra-config") -> list[dict]:
    """The `[[roller]]` entries of config/rollers.toml."""
    entries = cfg.get("rollers", {}).get("roller")
    if not entries:
        raise ConfigError(f"{source}: config/rollers.toml has no [[roller]] entries")
    return entries


def load_rollers(infra_config: str | Path) -> list[dict]:
    return rollers(load(infra_config), str(infra_config))


def toolchain_pins(cfg: dict) -> dict[str, str]:
    """The org-wide pin of each toolchain in config/kinds.toml, e.g. {"python": "3.14"}."""
    return {t["name"]: str(t["pin"]) for t in cfg.get("kinds", {}).get("toolchain", []) if "pin" in t}


def rollers_for(rollers: list[dict], repo: str, tool: str) -> list[dict]:
    """The rollers using `tool` that cover `repo`, in config order."""
    return [r for r in rollers if r["tool"] == tool and repo in r["repos"]]
