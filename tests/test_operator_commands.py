import asyncio
import signal
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, call, patch

from databases import Database

import comet.services.operator_commands as commands
from comet.core.db_router import ReplicaAwareDatabase
from comet.core.schema_migrations import run_schema_migrations


class OperatorCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = TemporaryDirectory()
        self.database = ReplicaAwareDatabase(
            Database(f"sqlite+aiosqlite:///{self.temp_dir.name}/commands.db")
        )
        await self.database.connect()
        await run_schema_migrations(
            self.database,
            is_sqlite=True,
            is_postgres=False,
        )
        self.database_patch = patch.object(commands, "database", self.database)
        self.database_patch.start()

    async def asyncTearDown(self):
        self.database_patch.stop()
        await self.database.disconnect()
        self.temp_dir.cleanup()

    async def test_closed_command_is_acknowledged_and_completed_by_target_owner(self):
        called = asyncio.Event()

        def start():
            called.set()
            return True

        with patch.dict(commands._HANDLERS, {"scraper.start": start}):
            dispatcher = asyncio.create_task(commands.run_command_dispatcher())
            try:
                result = await commands.dispatch_scraper_command("scraper.start")
            finally:
                dispatcher.cancel()
                await asyncio.gather(dispatcher, return_exceptions=True)

        self.assertTrue(called.is_set())
        self.assertEqual(result[0]["outcome"], "succeeded")
        row = await self.database.fetch_one(
            "SELECT status, acknowledged_at, finished_at FROM operator_commands"
        )
        self.assertEqual(row["status"], "finished")
        self.assertIsNotNone(row["acknowledged_at"])
        self.assertIsNotNone(row["finished_at"])

    async def test_unclaimed_command_expires_without_late_execution(self):
        identity = commands.RuntimeIdentity.current()
        await self.database.execute(
            """
            INSERT INTO background_scraper_runtimes (
                instance_id, process_id, state, draining,
                processed, success, failed, torrents_found,
                discovered_items, errors, last_heartbeat
            ) VALUES (
                :instance_id, 999999, 'running', 0,
                0, 0, 0, 0, 0, 0, :last_heartbeat
            )
            """,
            {
                "instance_id": identity.instance_id,
                "last_heartbeat": commands.time.time(),
            },
        )
        with patch.object(commands, "_COMMAND_TIMEOUT_SECONDS", 0.05):
            result = await commands.dispatch_scraper_command("scraper.pause")

        self.assertEqual(result, [])
        row = await self.database.fetch_one(
            "SELECT status, outcome, error_code FROM operator_commands"
        )
        self.assertEqual(
            dict(row),
            {
                "status": "finished",
                "outcome": "expired",
                "error_code": "command_timeout",
            },
        )

    async def test_acknowledged_command_expiration_is_not_overwritten_late(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_start():
            started.set()
            await release.wait()
            return True

        with (
            patch.dict(commands._HANDLERS, {"scraper.start": slow_start}),
            patch.object(commands, "_COMMAND_TIMEOUT_SECONDS", 0.05),
            patch.object(commands, "_POLL_INTERVAL_SECONDS", 0.005),
        ):
            dispatcher = asyncio.create_task(commands.run_command_dispatcher())
            try:
                dispatched = asyncio.create_task(
                    commands.dispatch_scraper_command("scraper.start")
                )
                await started.wait()
                self.assertEqual(await dispatched, [])
                release.set()
                await asyncio.sleep(0.15)
            finally:
                release.set()
                dispatcher.cancel()
                await asyncio.gather(dispatcher, return_exceptions=True)

        row = await self.database.fetch_one(
            "SELECT status, outcome, error_code FROM operator_commands"
        )
        self.assertEqual(
            dict(row),
            {
                "status": "finished",
                "outcome": "expired",
                "error_code": "command_timeout",
            },
        )

    async def test_usenet_command_targets_one_live_owner(self):
        identity = commands.RuntimeIdentity.current()
        called = asyncio.Event()

        async def drain():
            called.set()
            return True

        with patch.dict(commands._HANDLERS, {"usenet.drain": drain}):
            dispatcher = asyncio.create_task(commands.run_command_dispatcher())
            try:
                result = await commands.dispatch_usenet_command(
                    "usenet.drain",
                    instance_id=identity.instance_id,
                    process_id=commands.os.getpid(),
                )
            finally:
                dispatcher.cancel()
                await asyncio.gather(dispatcher, return_exceptions=True)

        self.assertTrue(called.is_set())
        self.assertEqual(result["outcome"], "succeeded")

    async def test_command_polling_does_not_write_a_runtime_row_each_poll(self):
        runtime_writes = 0
        original_execute = self.database.execute

        async def tracked_execute(query, *args, **kwargs):
            nonlocal runtime_writes
            if "INSERT INTO background_scraper_runtimes" in str(query):
                runtime_writes += 1
            return await original_execute(query, *args, **kwargs)

        with (
            patch.object(commands, "_POLL_INTERVAL_SECONDS", 0.005),
            patch.object(commands, "_RUNTIME_HEARTBEAT_INTERVAL_SECONDS", 0.03),
            patch.object(self.database, "execute", tracked_execute),
        ):
            dispatcher = asyncio.create_task(commands.run_command_dispatcher())
            try:
                await asyncio.sleep(0.08)
            finally:
                dispatcher.cancel()
                await asyncio.gather(dispatcher, return_exceptions=True)

        self.assertGreaterEqual(runtime_writes, 2)
        self.assertLessEqual(runtime_writes, 4)

    async def test_runtime_restart_uses_an_interrupt_only_for_its_own_process(self):
        with (
            patch.object(commands.asyncio, "sleep", new=AsyncMock()),
            patch.object(commands, "request_runtime_restart") as request,
            patch.object(commands.os, "getpid", return_value=123),
            patch.object(commands.os, "kill") as kill,
        ):
            await commands._terminate_web_master(123)
            await commands._terminate_web_master(456)

        self.assertEqual(
            kill.call_args_list,
            [
                call(123, signal.SIGINT),
                call(456, signal.SIGTERM),
            ],
        )
        self.assertEqual(request.call_count, 2)
