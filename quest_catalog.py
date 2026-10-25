"""Validated, offline Top 300 snapshots and stable Medium quest cycles.

Build: python quest_catalog.py input.json --source-url URL --output top300.json
Input: 300 objects with id, slug, title, difficulty, publicly_available (boolean).
Input order is the source ranking. No problem statements or credentials are stored.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import random
import re
from urllib.parse import urlparse

EPOCH = date(2026, 1, 1)


def checksum(problems: list[dict]) -> str:
    return hashlib.sha256(json.dumps(problems, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def eligible(problem: dict) -> bool:
    """Easy variants require an explicit reviewed challenge, never a relabel alone."""
    if not problem.get("publicly_available"):
        return False
    if problem["difficulty"] == "Medium":
        return True
    return (problem["difficulty"] == "Easy" and problem.get("practice_difficulty") == "Medium"
            and all(isinstance(problem.get(key), str) and problem[key].strip()
                    for key in ("constraint", "constraint_reason", "constraint_source_url")))


def validate_catalog(snapshot: dict) -> list[dict]:
    source = urlparse(snapshot.get("source_url", ""))
    if source.scheme != "https" or not source.hostname:
        raise ValueError("Top 300 catalog needs an HTTPS source_url.")
    date.fromisoformat(snapshot["retrieved_at"])
    if not snapshot.get("version"):
        raise ValueError("Top 300 catalog needs a version.")
    problems = snapshot.get("problems", [])
    if len(problems) != 300:
        raise ValueError("Top 300 snapshot must contain exactly 300 distinct source problems.")
    slugs, ids, ranks = set(), set(), set()
    for problem in problems:
        slug = problem.get("slug", "")
        ident = str(problem.get("id", ""))
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or not ident.isdigit():
            raise ValueError("Each catalog problem needs a valid slug and numeric ID.")
        if not problem.get("title") or problem.get("difficulty") not in {"Easy", "Medium", "Hard"}:
            raise ValueError("Each catalog problem needs a title and difficulty.")
        if type(problem.get("publicly_available")) is not bool:
            raise ValueError("Each catalog problem needs publicly_available: true or false.")
        if slug in slugs or ident in ids or problem.get("rank") in ranks:
            raise ValueError("Duplicate catalog slug, ID, or rank.")
        if problem.get("url") != f"https://leetcode.com/problems/{slug}/":
            raise ValueError("Catalog problem URL must match its slug.")
        if problem.get("practice_difficulty") is not None:
            if problem["difficulty"] != "Easy" or problem["practice_difficulty"] != "Medium" or not eligible(problem):
                raise ValueError("Easy practice variants need a Medium target, explicit constraint, rationale, and source.")
            constraint_source = urlparse(problem["constraint_source_url"])
            if constraint_source.scheme != "https" or constraint_source.hostname not in {"leetcode.com", "www.leetcode.com"}:
                raise ValueError("Easy variant constraint must cite its LeetCode problem page.")
        slugs.add(slug)
        ids.add(ident)
        ranks.add(problem.get("rank"))
    if ranks != set(range(1, 301)):
        raise ValueError("Catalog ranks must cover 1 through 300.")
    if snapshot.get("checksum") != checksum(problems):
        raise ValueError("Top 300 catalog checksum mismatch.")
    pool = sorted((p for p in problems if eligible(p)), key=lambda p: p["slug"])
    if not pool:
        raise ValueError("Top 300 catalog contains no publicly accessible Medium-level questions.")
    return pool


def load_catalog(path: Path | None = None) -> tuple[dict, list[dict]]:
    path = path or Path(os.getenv("TOP300_CATALOG", Path(__file__).with_name("top300.json")))
    if not path.exists():
        raise ValueError("Daily quest catalog is not configured. Add the intended Top 300 snapshot (TOP300_CATALOG); submissions still work.")
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        return snapshot, validate_catalog(snapshot)
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Top 300 catalog structure.") from exc


def select_quest(guild_id: int, day: date) -> dict:
    snapshot, pool = load_catalog()
    offset = (day - EPOCH).days
    cycle, position = divmod(offset, len(pool))
    # Rotate one seeded permutation per cycle. This exhausts the pool and also
    # prevents adjacent cycle-boundary repeats without recursive computation.
    seed = hashlib.sha256(f"{guild_id}:{snapshot['checksum']}".encode()).digest()
    shuffled = list(pool)
    random.Random(int.from_bytes(seed, "big")).shuffle(shuffled)
    rotation = cycle if len(shuffled) > 2 else 0
    index = (position + rotation) % len(shuffled)
    problem = shuffled[index]
    result = {"slug": problem["slug"], "name": problem["title"], "difficulty": problem["difficulty"],
            "url": problem["url"], "catalog_version": snapshot["version"],
            "catalog_checksum": snapshot["checksum"]}
    for key in ("practice_difficulty", "constraint", "constraint_reason", "constraint_source_url"):
        if key in problem:
            result[key] = problem[key]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--version", default=date.today().isoformat())
    parser.add_argument("--output", type=Path, default=Path("top300.json"))
    args = parser.parse_args()
    problems = json.loads(args.input.read_text(encoding="utf-8"))
    for rank, problem in enumerate(problems, 1):
        problem["rank"] = rank
        problem["url"] = f"https://leetcode.com/problems/{problem['slug']}/"
    snapshot = {"source_url": args.source_url, "retrieved_at": date.today().isoformat(),
                "version": args.version, "problems": problems, "checksum": checksum(problems)}
    pool = validate_catalog(snapshot)
    args.output.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(f"Validated 300 source questions; {len(pool)} publicly accessible Medium-level quests. Wrote {args.output}.")


if __name__ == "__main__":
    main()
