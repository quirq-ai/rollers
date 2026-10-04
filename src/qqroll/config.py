"""Read roller settings from a checkout of quirq-ai/infra-config, through its own loader (qqcfg)."""
from __future__ import annotations

import importlib.util
from pathlib import Path


class ConfigError(Exception):
    pass


def load_rollers(infra_config: str | Path) -> list[dict]:
    """The `[[roller]]` entries of infra-config's `config/rollers.toml`, read with `qqcfg.load`."""
    root = Path(infra_config)
    tool = root / "tools" / "qqcfg.py"
    if not tool.is_file():
        raise ConfigError(f"{root}: not an infra-config checkout (no tools/qqcfg.py)")
    spec = importlib.util.spec_from_file_location("qqcfg", tool)
    qqcfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qqcfg)
    try:
        cfg = qqcfg.load(root)
    except qqcfg.ConfigError as e:
        raise ConfigError(str(e)) from None
    rollers = cfg.get("rollers", {}).get("roller")
    if not rollers:
        raise ConfigError(f"{root}: config/rollers.toml has no [[roller]] entries")
    return rollers


def rollers_for(rollers: list[dict], repo: str, tool: str) -> list[dict]:
    """The rollers using `tool` that cover `repo`, in config order."""
    return [r for r in rollers if r["tool"] == tool and repo in r["repos"]]
