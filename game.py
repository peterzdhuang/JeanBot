"""Jeancoins, Jean-Luc's suspicious shop, pets, and bounded roguelite runs."""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime

SHOPKEEPER = "🧌 Jean-Luc the Tax Goblin"
SHOP_LINES = (
    "Welcome to my legally ambiguous boutique. Receipts are a state of mind.",
    "All sales final. All bugs emotional. Your Jeancoins smell spendable.",
    "I accept Jeancoins, not excuses. Your pet accepts both.",
    "These relics fell off a compiler. Do not ask which compiler.",
)
# Static catalog stays in code, not in the database.
ITEMS = {
    "biscuit": {"name": "🍪 Byte Biscuit", "price": 20, "kind": "food", "description": "+50 pet fullness; feeding grants pet XP."},
    "juice": {"name": "🧃 Debug Juice", "price": 40, "kind": "potion", "description": "Restore 4 HP during a dungeon run."},
    "egg": {"name": "🥚 Suspicious Egg", "price": 80, "kind": "pet", "description": "Hatch your permanent coding companion."},
    "chef": {"name": "🧑‍🍳 Certified Chef", "price": 100, "kind": "title", "description": "A profile title for those who cook."},
    "offbyone": {"name": "👑 Off-by-One Royalty", "price": 300, "kind": "title", "description": "Rule your kingdom of almost-correct answers."},
    "compiler": {"name": "💜 Compiler Glow", "price": 150, "kind": "aura", "color": 0x8B5CF6, "description": "Purple profile aura."},
    "segfault": {"name": "💖 Segfault Sparkles", "price": 300, "kind": "aura", "color": 0xF472B6, "description": "Pink profile aura. Glamorous crash not included."},
    "goblin": {"name": "💚 Goblin Mode", "price": 400, "kind": "aura", "color": 0x2DD4BF, "description": "Mint-green profile aura."},
    "magnet": {"name": "🧲 Coin Magnet", "price": 200, "kind": "relic", "description": "+10% base solve Jeancoins while equipped."},
    "crystal": {"name": "🔮 Stack Crystal", "price": 350, "kind": "relic", "description": "+3 maximum HP on new dungeon runs."},
    "duck": {"name": "🦆 Rubber Duck Totem", "price": 220, "kind": "relic", "description": "-1 damage per dungeon encounter; minimum 0."},
}
PETS = (
    ("slime", "🟢", "Byte Slime"),
    ("duck", "🦆", "Rubber Duck"),
    ("bat", "🦇", "Bug Bat"),
    ("fox", "🦊", "Syntax Fox"),
)
PERKS = {
    "ward": ("🛡️ Stack Shield", "-1 incoming damage."),
    "greed": ("💰 Greedy Goblin", "+8 room loot, but +1 incoming damage."),
    "vampire": ("🧛 Garbage Collector", "Recover 1 HP after surviving a room."),
    "lucky": ("🎲 RNG Blessing", "30% chance to dodge; +5 room loot."),
    "scholar": ("📚 Big-O Brain", "Medium/Hard rooms deal 1 less damage."),
}
CLASSES = {"Warden": "Start with +3 HP.", "Scavenger": "+4 room loot.", "Mage": "-1 damage on Medium/Hard rooms."}
MONSTERS = ("Null Pointer Imp", "Merge Conflict Mimic", "Cache Miss Goblin", "Infinite Loop Lich")
FLOORS = 10
MAX_STACK = 99


def seeded(value: str) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big"))


def buy(member: dict, item_id: str, quantity: int, now: datetime, user_id: int) -> str:
    item = ITEMS.get(item_id)
    if not item:
        raise ValueError("That item is not in Jean-Luc's shop. Use /shop.")
    if not 1 <= quantity <= 20:
        raise ValueError("Buy between 1 and 20 at a time.")
    consumable = item["kind"] in ("food", "potion")
    if not consumable and quantity != 1:
        raise ValueError("One of these is plenty. Even for a goblin.")
    if item["kind"] == "pet" and member["pet"]:
        raise ValueError("You already have a pet. Feed the tiny menace instead.")
    owned = member["inventory"].get(item_id, 0)
    if not consumable and owned:
        raise ValueError("You already own this. Equip it with /equip.")
    if owned + quantity > MAX_STACK:
        raise ValueError("Inventory stacks stop at 99. Use some first.")
    cost = item["price"] * quantity
    if member["wallet"] < cost:
        raise ValueError(f"Need {cost} Jeancoins; you have {member['wallet']}. The goblin does not offer credit.")
    member["wallet"] -= cost
    if item["kind"] == "pet":
        species, emoji, name = seeded(f"pet:{user_id}").choice(PETS)
        member["pet"] = {"species": species, "name": name, "xp": 0, "fullness": 100,
                         "fed_at": now.timestamp()}
        return f"{emoji} **{name}** hatched! It has already judged your variable names."
    member["inventory"][item_id] = owned + quantity
    return f"Bought **{quantity} × {item['name']}** for **{cost} Jeancoins**."


def equip(member: dict, item_id: str) -> str:
    item = ITEMS.get(item_id)
    if not item or item["kind"] not in ("title", "aura", "relic"):
        raise ValueError("Equip a title, aura, or relic from /inventory.")
    if not member["inventory"].get(item_id, 0):
        raise ValueError("You do not own that. Jean-Luc has a strict no-imaginary-drip policy.")
    member["equipped"][item["kind"]] = item_id
    return f"Equipped **{item['name']}**."


def pet_status(member: dict, now: datetime) -> dict | None:
    pet = member["pet"]
    if not pet:
        return None
    fullness = max(0, pet["fullness"] - int(max(0, now.timestamp() - pet["fed_at"]) / 1800))
    level = pet["xp"] // 30 + 1
    stage = "Overlord" if level >= 7 else "Gremlin" if level >= 3 else "Hatchling"
    emoji = next(emoji for species, emoji, _ in PETS if species == pet["species"])
    return {**pet, "fullness": fullness, "level": level, "stage": stage, "emoji": emoji,
            "mood": "😴 Sleepy (no solve bonus)" if fullness < 20 else "😈 Ready to cause problems"}


def feed(member: dict, now: datetime) -> str:
    pet = pet_status(member, now)
    if not pet:
        raise ValueError("Buy a Suspicious Egg first: /buy item:egg.")
    if pet["fullness"] >= 100:
        raise ValueError("Your pet is full. It is not a bottomless array.")
    if not member["inventory"].get("biscuit", 0):
        raise ValueError("No Byte Biscuits left. /buy item:biscuit")
    member["inventory"]["biscuit"] -= 1
    member["pet"].update(fullness=min(100, pet["fullness"] + 50), fed_at=now.timestamp())
    member["pet"]["xp"] += 10
    return f"{pet['emoji']} **{pet['name']}** demolished a biscuit. +10 pet XP. Outstanding table manners: none."


def pet_solve_bonus(member: dict, difficulty: str, now: datetime) -> int:
    pet = pet_status(member, now)
    if not pet or pet["fullness"] < 20:
        return 0
    member["pet"]["xp"] += {"Easy": 10, "Medium": 20, "Hard": 35}[difficulty]
    return 2


def rename_pet(member: dict, name: str) -> None:
    if not member["pet"]:
        raise ValueError("Hatch a pet first with /buy item:egg.")
    name = name.strip()
    if not 1 <= len(name) <= 32 or any(ord(char) < 32 for char in name):
        raise ValueError("Pet names need 1–32 printable characters.")
    member["pet"]["name"] = name


def start_run(member: dict, user_id: int, day: date, role: str = "Warden") -> dict:
    if role not in CLASSES:
        raise ValueError("Pick Warden, Scavenger, or Mage.")
    if member["run"] and member["run"]["status"] == "active":
        raise ValueError("Finish or retreat from your current run first.")
    if member["last_run_day"] and member["last_run_day"] >= day.isoformat():
        raise ValueError("One fresh run per challenge day. Your next ticket drops at 3:15 AM.")
    relic = member["equipped"].get("relic")
    maximum = 10 + (3 if role == "Warden" else 0) + (3 if relic == "crystal" else 0)
    member["run"] = {"day": day.isoformat(), "role": role, "floor": 0, "hp": maximum,
                     "max_hp": maximum, "loot": 0, "status": "active", "perks": [],
                     "choices": [], "seed": f"{user_id}:{day}", "relic": relic}
    member["last_run_day"] = day.isoformat()
    return member["run"]


def finish_run(member: dict, status: str) -> int:
    run = member["run"]
    payout = run["loot"] // 2 if status == "fallen" else run["loot"]
    member["wallet"] += payout
    run["status"] = status
    run["loot"] = 0
    run["choices"] = []
    if status == "cleared":
        member["dungeon_wins"] += 1
    return payout


def advance_run(member: dict, difficulty: str) -> dict | None:
    run = member["run"]
    if not run or run["status"] != "active":
        return None
    if run["choices"]:
        return {"text": "Your dungeon is waiting for a perk. Use /dungeon choose before the next room.",
                "waiting": True}
    floor = run["floor"] + 1
    rng = seeded(f"{run['seed']}:{floor}")
    monster = "Off-by-One Overlord" if floor == 10 else "Timeout Hydra" if floor == 5 else rng.choice(MONSTERS)
    damage = rng.randint(1, 3) + (1 if floor in (5, 10) else 0)
    perks = run["perks"]
    damage += int("greed" in perks)
    damage -= int("ward" in perks) + int(run["relic"] == "duck")
    if difficulty != "Easy":
        damage -= int("scholar" in perks) + int(run["role"] == "Mage")
    if "lucky" in perks and rng.random() < 0.3:
        damage = 0
    damage = max(0, damage)
    loot = 5 + floor * 2 + (40 if floor == 5 else 100 if floor == 10 else 0)
    loot += 4 * int(run["role"] == "Scavenger") + 8 * int("greed" in perks) + 5 * int("lucky" in perks)
    run["hp"] = max(0, run["hp"] - damage)
    run["floor"] = floor
    run["loot"] += loot
    member["best_floor"] = max(member["best_floor"], floor)
    text = f"Floor **{floor}/{FLOORS}** · {monster}: −{damage} HP, +{loot} unbanked Jeancoins."
    if run["hp"] <= 0:
        payout = finish_run(member, "fallen")
        return {"text": text + f"\n💀 Run ended. Jean-Luc salvaged **{payout} Jeancoins** (50% of loot).",
                "status": "fallen", "payout": payout}
    if "vampire" in perks:
        run["hp"] = min(run["max_hp"], run["hp"] + 1)
    if floor == FLOORS:
        payout = finish_run(member, "cleared")
        return {"text": text + f"\n🏆 Dungeon cleared! Banked **{payout} Jeancoins**.",
                "status": "cleared", "payout": payout}
    if floor in (3, 6, 9):
        available = [key for key in PERKS if key not in perks]
        run["choices"] = rng.sample(available, min(3, len(available)))
        text += "\n🎲 Perk draft unlocked! Use /dungeon choose."
    return {"text": text, "status": "active"}


def choose_perk(member: dict, perk: str) -> str:
    run = member["run"]
    if not run or run["status"] != "active" or perk not in run["choices"]:
        raise ValueError("Choose one of the perks offered by /dungeon status.")
    run["perks"].append(perk)
    run["choices"] = []
    return f"Drafted **{PERKS[perk][0]}** — {PERKS[perk][1]}"


def retreat(member: dict) -> int:
    if not member["run"] or member["run"]["status"] != "active":
        raise ValueError("There is no active run to retreat from.")
    return finish_run(member, "retreated")


def drink(member: dict) -> str:
    run = member["run"]
    if not run or run["status"] != "active":
        raise ValueError("Debug Juice only works during an active dungeon run.")
    if run["hp"] >= run["max_hp"]:
        raise ValueError("Already at full health. Save your juice.")
    if not member["inventory"].get("juice", 0):
        raise ValueError("No Debug Juice left. /buy item:juice")
    member["inventory"]["juice"] -= 1
    restored = min(4, run["max_hp"] - run["hp"])
    run["hp"] += restored
    return f"🧃 Restored **{restored} HP**. The potion tastes like a resolved merge conflict."
