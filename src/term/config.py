"""TOML-backed configuration: agents, roles, and a pipeline.

Three layers, merged in order (later wins):
  1. Bundled `defaults.toml`.
  2. User-level `~/.config/term/config.toml` (or `$XDG_CONFIG_HOME/term/...`).
  3. Project-level `<workspace>/.term/pipeline.toml`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentRecipe:
    name: str
    command: tuple[str, ...]
    one_shot: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Role:
    name: str
    agent: str
    prompt_template: str = ""


@dataclass(frozen=True)
class NodeSpec:
    id: str
    role: str
    mode: str = "persistent"  # "persistent" | "one-shot"


@dataclass(frozen=True)
class Pipeline:
    name: str
    nodes: tuple[NodeSpec, ...]


@dataclass(frozen=True)
class Config:
    agents: dict[str, AgentRecipe]
    roles: dict[str, Role]
    pipeline: Pipeline

    def agent_for_role(self, role_name: str) -> AgentRecipe:
        role = self.roles[role_name]
        return self.agents[role.agent]


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "term" / "config.toml"


def bundled_defaults_path() -> Path:
    return Path(__file__).parent / "defaults.toml"


def load_config(workspace_root: Path) -> Config:
    raw = _read_toml(bundled_defaults_path())
    upath = user_config_path()
    if upath.exists():
        _deep_merge(raw, _read_toml(upath))
    ppath = workspace_root / ".term" / "pipeline.toml"
    if ppath.exists():
        _deep_merge(raw, _read_toml(ppath))
    return _build(raw)


def _read_toml(p: Path) -> dict:
    with p.open("rb") as f:
        return tomllib.load(f)


def _deep_merge(into: dict, src: dict) -> None:
    for k, v in src.items():
        if k in into and isinstance(into[k], dict) and isinstance(v, dict):
            _deep_merge(into[k], v)
        else:
            into[k] = v


def _build(raw: dict) -> Config:
    agents: dict[str, AgentRecipe] = {}
    for name, body in raw.get("agents", {}).items():
        agents[name] = AgentRecipe(
            name=name,
            command=tuple(body["command"]),
            one_shot=tuple(body["one_shot"]) if "one_shot" in body else None,
        )

    roles: dict[str, Role] = {}
    for name, body in raw.get("roles", {}).items():
        roles[name] = Role(
            name=name,
            agent=body["agent"],
            prompt_template=body.get("prompt_template", ""),
        )

    pipe = raw.get("pipeline", {})
    nodes: list[NodeSpec] = []
    used_ids: set[str] = set()
    for n in pipe.get("nodes", []):
        role_name = n["role"]
        if role_name not in roles:
            raise ValueError(f"pipeline references unknown role: {role_name!r}")
        base_id = n.get("id", role_name)
        node_id = base_id
        suffix = 2
        while node_id in used_ids:
            node_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(node_id)
        mode = n.get("mode", "persistent")
        if mode not in ("persistent", "one-shot"):
            raise ValueError(f"node {node_id!r} has invalid mode {mode!r}")
        nodes.append(NodeSpec(id=node_id, role=role_name, mode=mode))

    for role in roles.values():
        if role.agent not in agents:
            raise ValueError(f"role {role.name!r} references unknown agent {role.agent!r}")

    return Config(
        agents=agents,
        roles=roles,
        pipeline=Pipeline(name=pipe.get("name", "default"), nodes=tuple(nodes)),
    )
