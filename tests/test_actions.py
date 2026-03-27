"""Tests for ailm.core.actions — safe, whitelisted command execution."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from ailm.core.actions import ActionDef, ActionRegistry, ActionResult


# --- Fixtures ---


@pytest.fixture
def registry() -> ActionRegistry:
    return ActionRegistry()


# --- ActionDef ---


class TestActionDef:
    def test_allowed_params_single(self):
        ad = ActionDef(command=["cmd", "{foo}"], requires_confirmation=True, description="d")
        assert ad.allowed_params == frozenset({"foo"})

    def test_allowed_params_multiple(self):
        ad = ActionDef(
            command=["cmd", "{a}", "--flag={b}"],
            requires_confirmation=True,
            description="d",
        )
        assert ad.allowed_params == frozenset({"a", "b"})

    def test_allowed_params_none(self):
        ad = ActionDef(command=["cmd", "literal"], requires_confirmation=True, description="d")
        assert ad.allowed_params == frozenset()

    def test_frozen(self):
        ad = ActionDef(command=["cmd"], requires_confirmation=True, description="d")
        with pytest.raises(AttributeError):
            ad.description = "changed"  # type: ignore[misc]


class TestActionResult:
    def test_fields(self):
        r = ActionResult(success=True, output="ok", error="")
        assert r.success is True
        assert r.output == "ok"
        assert r.error == ""

    def test_frozen(self):
        r = ActionResult(success=True, output="ok", error="")
        with pytest.raises(AttributeError):
            r.success = False  # type: ignore[misc]


# --- ActionRegistry.list_actions ---


class TestListActions:
    def test_returns_all_actions(self, registry: ActionRegistry):
        actions = registry.list_actions()
        assert "restart_service" in actions
        assert "journal_vacuum" in actions
        assert "reboot" in actions

    def test_returns_copy(self, registry: ActionRegistry):
        """Mutating the returned dict does not affect the registry."""
        actions = registry.list_actions()
        actions["evil"] = ActionDef(
            command=["rm", "-rf", "/"],
            requires_confirmation=True,
            description="nope",
        )
        assert "evil" not in registry.SAFE_ACTIONS

    def test_all_actions_require_confirmation(self, registry: ActionRegistry):
        for name, action in registry.list_actions().items():
            assert action.requires_confirmation, f"{name} must require confirmation"


# --- ActionRegistry.execute — validation gates ---


class TestExecuteValidation:
    async def test_unknown_action_rejected(self, registry: ActionRegistry):
        result = await registry.execute("nonexistent", {}, confirmed=True)
        assert not result.success
        assert "Unknown action" in result.error

    async def test_unconfirmed_action_refused(self, registry: ActionRegistry):
        result = await registry.execute("reboot", {}, confirmed=False)
        assert not result.success
        assert "requires confirmation" in result.error

    async def test_missing_parameter_rejected(self, registry: ActionRegistry):
        result = await registry.execute("restart_service", {}, confirmed=True)
        assert not result.success
        assert "Missing parameters" in result.error

    async def test_extra_parameter_rejected(self, registry: ActionRegistry):
        result = await registry.execute(
            "restart_service",
            {"name": "nginx", "evil": "payload"},
            confirmed=True,
        )
        assert not result.success
        assert "Extra parameters" in result.error

    async def test_no_params_needed_empty_dict_ok(self, registry: ActionRegistry):
        """'reboot' has no placeholders, so empty params should pass validation."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await registry.execute("reboot", {}, confirmed=True)

        assert result.success


# --- ActionRegistry.execute — successful execution (mocked) ---


class TestExecuteSuccess:
    async def test_restart_service_success(self, registry: ActionRegistry):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await registry.execute(
                "restart_service",
                {"name": "nginx"},
                confirmed=True,
            )

        assert result.success
        assert result.error == ""
        # Verify the exact command passed — no shell, correct args
        mock_exec.assert_called_once_with(
            "systemctl",
            "restart",
            "nginx",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def test_journal_vacuum_success(self, registry: ActionRegistry):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"Vacuuming done.\n", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await registry.execute(
                "journal_vacuum",
                {"size": "500M"},
                confirmed=True,
            )

        assert result.success
        assert "Vacuuming done." in result.output
        mock_exec.assert_called_once_with(
            "journalctl",
            "--vacuum-size=500M",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def test_reboot_success(self, registry: ActionRegistry):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await registry.execute("reboot", {}, confirmed=True)

        assert result.success
        mock_exec.assert_called_once_with(
            "systemctl",
            "reboot",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def test_nonzero_exit_code_is_failure(self, registry: ActionRegistry):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"Unit not found.\n")
        mock_proc.returncode = 5

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await registry.execute(
                "restart_service",
                {"name": "nonexistent"},
                confirmed=True,
            )

        assert not result.success
        assert "Unit not found." in result.error

    async def test_oserror_handled(self, registry: ActionRegistry):
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("No such file or directory"),
        ):
            result = await registry.execute("reboot", {}, confirmed=True)

        assert not result.success
        assert "Failed to start process" in result.error


# --- Security: parameter injection ---


class TestParameterInjection:
    """Verify that malicious parameter values are passed as literal
    arguments to create_subprocess_exec, not interpreted by a shell."""

    async def test_semicolon_injection_is_literal(self, registry: ActionRegistry):
        """'; rm -rf /' passed as service name becomes a single literal arg."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"Unit not found.\n")
        mock_proc.returncode = 5

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await registry.execute(
                "restart_service",
                {"name": "; rm -rf /"},
                confirmed=True,
            )

        # The malicious string is passed as ONE argument, not split by shell
        mock_exec.assert_called_once_with(
            "systemctl",
            "restart",
            "; rm -rf /",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def test_pipe_injection_is_literal(self, registry: ActionRegistry):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 5

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await registry.execute(
                "restart_service",
                {"name": "nginx | cat /etc/shadow"},
                confirmed=True,
            )

        mock_exec.assert_called_once_with(
            "systemctl",
            "restart",
            "nginx | cat /etc/shadow",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def test_backtick_injection_is_literal(self, registry: ActionRegistry):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 5

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await registry.execute(
                "restart_service",
                {"name": "`whoami`"},
                confirmed=True,
            )

        mock_exec.assert_called_once_with(
            "systemctl",
            "restart",
            "`whoami`",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def test_dollar_expansion_injection_is_literal(self, registry: ActionRegistry):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 5

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await registry.execute(
                "journal_vacuum",
                {"size": "$(cat /etc/passwd)"},
                confirmed=True,
            )

        mock_exec.assert_called_once_with(
            "journalctl",
            "--vacuum-size=$(cat /etc/passwd)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )


# --- Logging ---


class TestLogging:
    async def test_execution_logged(self, registry: ActionRegistry, caplog):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok\n", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with caplog.at_level("INFO", logger="ailm.core.actions"):
                await registry.execute(
                    "restart_service",
                    {"name": "sshd"},
                    confirmed=True,
                )

        assert any("Executing action" in r.message for r in caplog.records)
        assert any("restart_service" in r.message for r in caplog.records)

    async def test_rejection_logged(self, registry: ActionRegistry, caplog):
        with caplog.at_level("WARNING", logger="ailm.core.actions"):
            await registry.execute("nonexistent", {}, confirmed=True)

        assert any("rejected" in r.message.lower() for r in caplog.records)
