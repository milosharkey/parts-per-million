"""The prompt bank: every prompt the pipeline can build, read from data/.

Each prompt is a folder, data/<id>/, holding everything about it:
  prompt.toml      its settings (what the player sees, where answers come
                   from, floors, status): the field reference is in README.md,
                   "Prompt settings", and templates/ has a starting point for
                   each kind of source
  curriculum.csv   how early people meet each answer at school
  aliases.csv      extra ways people type an answer
  extras.csv       Wikipedia titles to add that the source misses
  exclude.csv      Wikipedia titles the source returns that don't belong
  flags.csv        hand-set tiers, the gem and Fool's gold
  blurbs.csv       short descriptions shown when a player finds an answer
  must_accept.csv  answers players will definitely say, checked every build

The folder name is the prompt's id, which also names its cache files, so
renaming a folder means its next build starts from scratch.

A new prompt starts as a copy of a template: python new_prompt.py (see
README.md, "Designing a prompt"). Prompts are data, not code, so nothing
here needs changing to add one (DECISIONS.md, 40).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import config

STATUSES = ("ready", "draft")   # ready: reviewed, in the bank. draft: not yet


def load_prompt(folder: Path) -> dict:
    """One prompt's settings, as the build expects them."""
    with (folder / "prompt.toml").open("rb") as f:
        spec = tomllib.load(f)
    spec.setdefault("data_dir", folder.name)
    spec.setdefault("status", "draft")
    if spec["status"] not in STATUSES:
        raise ValueError(f"{folder.name}/prompt.toml: status must be one of {', '.join(STATUSES)}")
    if "strip_descriptors" in spec:
        spec["strip_descriptors"] = frozenset(spec["strip_descriptors"])
    sources = [k for k in ("query", "category", "templates") if k in spec]
    if len(sources) != 1:
        raise ValueError(f"{folder.name}/prompt.toml needs exactly one of query, category "
                         f"or templates (it has {', '.join(sources) or 'none'})")
    return spec


def load_prompts(data_dir: Path = config.DATA_DIR) -> dict[str, dict]:
    """Every prompt in data/, by id, in alphabetical order."""
    return {folder.name: load_prompt(folder)
            for folder in sorted(data_dir.iterdir())
            if (folder / "prompt.toml").exists()}


def ids_with_status(status: str) -> list[str]:
    return [pid for pid, spec in PROMPTS.items() if spec["status"] == status]


PROMPTS = load_prompts()
