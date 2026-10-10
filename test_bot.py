"""Exercise command callbacks offline without importing the Discord client or logging in."""
import ast
import asyncio
import logging
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import patch

from storage import PostgresStore
from test_storage import FakePool


class FakeHTTPException(Exception):
    pass


class FakeEmbed:
    def __init__(self, **kwargs):
        self.values = kwargs
        self.fields = []

    def set_footer(self, **kwargs):
        self.footer = kwargs

    def set_author(self, **kwargs):
        self.author = kwargs

    def set_thumbnail(self, **kwargs):
        self.thumbnail = kwargs

    def add_field(self, **kwargs):
        self.fields.append(kwargs)


class FakeResponse:
    def __init__(self):
        self.done = False
        self.defers = 0
        self.messages = []

    def is_done(self):
        return self.done

    async def defer(self, **kwargs):
        self.done = True
        self.defers += 1

    async def send_message(self, text, **kwargs):
        self.done = True
        self.messages.append(text)


def command_namespace():
    """Use the actual bot imports and callback bodies; only Discord transport is faked."""
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    blocked_imports = {"discord", "dotenv"}
    functions = {"card", "execute", "submit", "command_error", "log_failure"}
    constants = {"PURPLE", "Difficulty", "HYPE", "log"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            if not any(alias.name.split(".")[0] in blocked_imports for alias in node.names):
                body.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module.split(".")[0] not in blocked_imports:
            body.append(node)
        elif isinstance(node, ast.Assign):
            names = {part.id for target in node.targets for part in ast.walk(target) if isinstance(part, ast.Name)}
            if names & constants:
                body.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            node.decorator_list = []
            body.append(node)
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    namespace = {
        "discord": types.SimpleNamespace(
            Embed=FakeEmbed, HTTPException=FakeHTTPException,
            AllowedMentions=types.SimpleNamespace(none=lambda: None),
        ),
        "DashboardView": lambda *args, **kwargs: object(),
    }
    exec(compile(module, str(Path(__file__).with_name("bot.py")), "exec"), namespace)
    return namespace


class SubmitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.namespace = command_namespace()
        self.store = PostgresStore("postgresql://test:fake@localhost/test")
        self.store.pool = FakePool()
        self.namespace["bot"] = types.SimpleNamespace(store=self.store, submit_lock=asyncio.Lock(), seen={})
        self.namespace["utcnow"] = lambda: datetime(2026, 10, 10, 18, tzinfo=timezone.utc)
        self.interaction = types.SimpleNamespace(
            guild_id=7, user=types.SimpleNamespace(id=1, display_name="Friend",
                display_avatar=types.SimpleNamespace(url="https://example.com/avatar.png")),
            response=FakeResponse(), followup=types.SimpleNamespace(send=AsyncMock()),
        )

    async def submit(self, url="https://leetcode.com/problems/two-sum/", **kwargs):
        await self.namespace["submit"](self.interaction, url, **kwargs)

    async def test_submit_callback_imports_parser_and_saves_counts(self):
        await self.submit()
        saved = await self.store.guild(7)
        self.assertEqual(saved["members"]["1"]["solved"], 1)
        self.assertEqual(saved["members"]["1"]["counts"], [1, 0, 0])
        self.assertEqual(self.interaction.response.defers, 1)
        message = self.interaction.followup.send.call_args.kwargs["embed"]
        self.assertEqual(message.values["title"], "⚔️ Solve secured")
        self.assertNotIn("two-sum", self.store.pool.connection.players[(7, 1)])

    async def test_submission_url_and_explicit_difficulty(self):
        await self.submit("https://leetcode.com/problems/add-two-numbers/submissions/12345/", difficulty="Medium")
        saved = await self.store.guild(7)
        self.assertEqual(saved["members"]["1"]["counts"], [0, 1, 0])
        self.assertEqual(saved["members"]["1"]["wallet"], 60)

    async def test_invalid_link_is_a_private_error_not_a_crash(self):
        await self.submit("https://example.com/problems/two-sum/")
        self.assertEqual(self.store.pool.connection.players, {})
        self.assertEqual(self.interaction.response.defers, 0)
        self.assertEqual(len(self.interaction.response.messages), 1)

    async def test_duplicate_keeps_one_count_and_one_reward(self):
        await self.submit()
        self.interaction.response = FakeResponse()
        await self.submit()
        saved = await self.store.guild(7)
        self.assertEqual(saved["members"]["1"]["solved"], 1)
        self.assertEqual(saved["members"]["1"]["wallet"], 30)
        self.assertIn("Already counted", self.interaction.followup.send.call_args.args[0])

    async def test_database_failure_does_not_remember_or_reward_solve(self):
        self.store.pool.connection.fail_player_write = True
        with self.assertLogs("arena", logging.ERROR):
            await self.submit()
        self.assertEqual(self.store.pool.connection.players, {})
        self.assertEqual(self.namespace["bot"].seen, {})
        self.assertIn("Couldn't load or save", self.interaction.followup.send.call_args.args[0])

    def test_logs_original_error_not_only_discord_wrapper(self):
        try:
            raise NameError("name 'missing_parser' is not defined")
        except NameError as original:
            wrapper = RuntimeError("CommandInvokeError")
            wrapper.original = original
            with self.assertLogs("arena", logging.ERROR) as captured:
                cause = self.namespace["log_failure"]("Slash command /submit failed", wrapper)
        self.assertIsInstance(cause, NameError)
        text = "\n".join(captured.output)
        self.assertIn("NameError", text)
        self.assertIn("missing_parser", text)
        self.assertIn("Traceback", text)

    def test_error_logs_redact_tokens_dsn_and_database_password(self):
        token = "FAKE_DISCORD_TOKEN_FOR_TEST"
        dsn = "postgresql://test:FAKE_DB_PASSWORD_FOR_TEST@localhost/example"
        with patch.dict("os.environ", {"DISCORD_TOKEN": token, "DATABASE_URL": dsn}):
            error = RuntimeError(f"token={token}, dsn={dsn}, password=FAKE_DB_PASSWORD_FOR_TEST")
            with self.assertLogs("arena", logging.ERROR) as captured:
                self.namespace["log_failure"]("Test error", error)
        text = "\n".join(captured.output)
        self.assertNotIn(token, text)
        self.assertNotIn(dsn, text)
        self.assertNotIn("FAKE_DB_PASSWORD_FOR_TEST", text)
        self.assertIn("[REDACTED]", text)


if __name__ == "__main__":
    unittest.main()
