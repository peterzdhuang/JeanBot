import copy
import json
import unittest
from datetime import date, datetime, timedelta, timezone

from game import (
    ITEMS, advance_run, buy, choose_perk, drink, equip, feed, pet_solve_bonus,
    pet_status, rename_pet, retreat, start_run,
)
from storage import encode_player
from tracker import new_member

NOW = datetime(2026, 10, 10, 18, tzinfo=timezone.utc)
DAY = date(2026, 10, 10)


class GameTests(unittest.TestCase):
    def setUp(self):
        self.member = new_member(DAY, "PST")
        self.member["wallet"] = 10000

    def test_buy_and_equip_cosmetics(self):
        buy(self.member, "segfault", 1, NOW, 1)
        self.assertEqual(self.member["wallet"], 9700)
        equip(self.member, "segfault")
        self.assertEqual(self.member["equipped"], {"aura": "segfault"})
        with self.assertRaises(ValueError):
            buy(self.member, "segfault", 1, NOW, 1)
        with self.assertRaises(ValueError):
            equip(self.member, "compiler")

    def test_rejected_purchases_do_not_spend_money(self):
        self.member["wallet"] = 10
        before = copy.deepcopy(self.member)
        for item, quantity in (("egg", 1), ("biscuit", -2), ("egg", 2), ("fake", 1)):
            with self.assertRaises(ValueError):
                buy(self.member, item, quantity, NOW, 1)
            self.assertEqual(self.member, before)

    def test_consumable_quantity_limit(self):
        for _ in range(4):
            buy(self.member, "biscuit", 20, NOW, 1)
        buy(self.member, "biscuit", 19, NOW, 1)
        before = copy.deepcopy(self.member)
        with self.assertRaises(ValueError):
            buy(self.member, "biscuit", 1, NOW, 1)
        self.assertEqual(self.member, before)

    def test_egg_unique_and_persistent(self):
        buy(self.member, "egg", 1, NOW, 1)
        self.assertIsNotNone(self.member["pet"])
        saved = json.loads(encode_player(self.member))
        self.assertEqual(saved["pet"], self.member["pet"])
        with self.assertRaises(ValueError):
            buy(self.member, "egg", 1, NOW, 1)

    def test_hunger_lazy_and_survives_restart(self):
        buy(self.member, "egg", 1, NOW, 1)
        saved = json.loads(encode_player(self.member))
        self.assertEqual(pet_status(saved, NOW + timedelta(hours=8))["fullness"], 84)
        self.assertEqual(pet_status(saved, NOW + timedelta(days=100))["fullness"], 0)
        self.assertEqual(pet_status(saved, NOW - timedelta(hours=1))["fullness"], 100)
        self.assertIsNotNone(saved["pet"])

    def test_feeding_consumes_one_and_caps_hunger(self):
        buy(self.member, "egg", 1, NOW, 1)
        buy(self.member, "biscuit", 2, NOW, 1)
        with self.assertRaises(ValueError):
            feed(self.member, NOW)
        feed(self.member, NOW + timedelta(hours=30))
        self.assertEqual(self.member["inventory"]["biscuit"], 1)
        status = pet_status(self.member, NOW + timedelta(hours=30))
        self.assertEqual((status["fullness"], status["xp"]), (90, 10))
        feed(self.member, NOW + timedelta(hours=30))
        self.assertEqual(pet_status(self.member, NOW + timedelta(hours=30))["fullness"], 100)

    def test_feeding_requires_pet_and_food(self):
        with self.assertRaises(ValueError):
            feed(self.member, NOW)
        buy(self.member, "egg", 1, NOW, 1)
        with self.assertRaises(ValueError):
            feed(self.member, NOW + timedelta(hours=1))

    def test_pet_solve_xp_evolution_and_bonus(self):
        buy(self.member, "egg", 1, NOW, 1)
        for difficulty in ("Easy", "Medium", "Hard"):
            self.assertEqual(pet_solve_bonus(self.member, difficulty, NOW), 2)
        self.assertEqual(self.member["pet"]["xp"], 65)
        self.assertEqual(pet_status(self.member, NOW)["stage"], "Gremlin")
        self.assertEqual(pet_solve_bonus(self.member, "Hard", NOW + timedelta(hours=50)), 0)
        self.assertEqual(self.member["pet"]["xp"], 65)
        self.member["pet"]["xp"] = 180
        self.assertEqual(pet_status(self.member, NOW)["stage"], "Overlord")

    def test_rename_validation(self):
        buy(self.member, "egg", 1, NOW, 1)
        rename_pet(self.member, "Sir Byte")
        self.assertEqual(self.member["pet"]["name"], "Sir Byte")
        for invalid in ("", "a" * 33, "bad\nname"):
            with self.assertRaises(ValueError):
                rename_pet(self.member, invalid)

    def test_run_class_and_relic_captured_at_start(self):
        buy(self.member, "crystal", 1, NOW, 1)
        equip(self.member, "crystal")
        run = start_run(self.member, 1, DAY, "Warden")
        self.assertEqual(run["max_hp"], 16)
        self.member["equipped"]["relic"] = "magnet"
        self.assertEqual(run["relic"], "crystal")
        with self.assertRaises(ValueError):
            start_run(self.member, 1, DAY)

    def test_draft_pauses_encounters_and_rejects_invalid_choices(self):
        run = start_run(self.member, 1, DAY, "Warden")
        for _ in range(3):
            advance_run(self.member, "Hard")
        self.assertEqual(run["floor"], 3)
        self.assertEqual(len(run["choices"]), 3)
        self.assertTrue(advance_run(self.member, "Hard")["waiting"])
        self.assertEqual(run["floor"], 3)
        with self.assertRaises(ValueError):
            choose_perk(self.member, "made-up")
        offered = run["choices"][0]
        choose_perk(self.member, offered)
        self.assertIn(offered, run["perks"])
        self.assertEqual(run["choices"], [])

    def test_death_banks_half_once(self):
        run = start_run(self.member, 1, DAY)
        run.update(hp=1, loot=100)
        before = self.member["wallet"]
        event = advance_run(self.member, "Easy")
        self.assertEqual(event["status"], "fallen")
        self.assertEqual(self.member["wallet"] - before, 107 // 2)
        self.assertEqual(run["loot"], 0)
        self.assertIsNone(advance_run(self.member, "Easy"))
        with self.assertRaises(ValueError):
            retreat(self.member)

    def test_retreat_banks_all_and_one_run_per_day(self):
        run = start_run(self.member, 1, DAY)
        advance_run(self.member, "Easy")
        loot, wallet = run["loot"], self.member["wallet"]
        self.assertEqual(retreat(self.member), loot)
        self.assertEqual(self.member["wallet"], wallet + loot)
        with self.assertRaises(ValueError):
            start_run(self.member, 1, DAY)
        start_run(self.member, 1, DAY + timedelta(days=1), "Mage")

    def test_heal_uses_potion_and_caps_health(self):
        run = start_run(self.member, 1, DAY)
        buy(self.member, "juice", 2, NOW, 1)
        with self.assertRaises(ValueError):
            drink(self.member)
        run["hp"] -= 2
        drink(self.member)
        self.assertEqual(run["hp"], run["max_hp"])
        self.assertEqual(self.member["inventory"]["juice"], 1)

    def test_clear_run_bosses_perks_and_persistent_small_state(self):
        run = start_run(self.member, 1, DAY, "Mage")
        run["relic"] = "duck"
        last = None
        for _ in range(10):
            if run["choices"]:
                priority = next((key for key in ("ward", "scholar", "vampire", "lucky", "greed")
                                 if key in run["choices"]), None)
                choose_perk(self.member, priority)
            last = advance_run(self.member, "Hard")
        self.assertEqual(last["status"], "cleared")
        self.assertEqual((run["floor"], self.member["dungeon_wins"], self.member["best_floor"]), (10, 1, 10))
        self.assertLess(len(encode_player(self.member)), 2000)
        self.assertEqual(run["loot"], 0)

    def test_deterministic_run_across_restart(self):
        start_run(self.member, 1, DAY)
        restored = json.loads(encode_player(self.member))
        self.assertEqual(advance_run(self.member, "Medium"), advance_run(restored, "Medium"))
        self.assertEqual(self.member["run"], restored["run"])

    def test_all_catalog_items_fit_state_budget(self):
        for key in ITEMS:
            buy(self.member, key, 1, NOW, 1)
        for kind in ("title", "aura", "relic"):
            equip(self.member, next(key for key, item in ITEMS.items() if item["kind"] == kind))
        start_run(self.member, 1, DAY)
        self.assertLess(len(encode_player(self.member)), 3000)


if __name__ == "__main__":
    unittest.main()
