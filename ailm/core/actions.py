"""Safe, whitelisted command execution for ailm.

All executable actions are defined in a hardcoded whitelist. Every action
requires explicit confirmation before execution. Commands use
asyncio.create_subprocess_exec (never shell=True) and parameters are
validated against the allowed placeholders for each action.
"""

import asyncio
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Matches {param} placeholders in command templates.
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class ActionDef:
    """Definition of a safe, whitelisted action."""

    command: list[str]  # template with {param} placeholders
    requires_confirmation: bool  # always True for v0.1
    description: str

    @property
    def allowed_params(self) -> frozenset[str]:
        """Extract the set of parameter names from the command template."""
        return frozenset(
            name for part in self.command for name in _PLACEHOLDER_RE.findall(part)
        )


@dataclass(frozen=True)
class ActionResult:
    """Result of an action execution attempt."""

    success: bool
    output: str
    error: str


class ActionRegistry:
    """Registry of safe, whitelisted system actions.

    The SAFE_ACTIONS dict is the single source of truth and cannot be
    modified at runtime (class-level, frozen dataclasses).
    """

    SAFE_ACTIONS: dict[str, ActionDef] = {
        "restart_service": ActionDef(
            command=["systemctl", "restart", "{name}"],
            requires_confirmation=True,
            description="Restart a systemd service",
        ),
        "journal_vacuum": ActionDef(
            command=["journalctl", "--vacuum-size={size}"],
            requires_confirmation=True,
            description="Vacuum journal logs to specified size",
        ),
        "reboot": ActionDef(
            command=["systemctl", "reboot"],
            requires_confirmation=True,
            description="Reboot the system",
        ),
    }

    def list_actions(self) -> dict[str, ActionDef]:
        """Return a copy of the action registry."""
        return dict(self.SAFE_ACTIONS)

    async def execute(
        self,
        action_name: str,
        params: dict[str, str],
        *,
        confirmed: bool,
    ) -> ActionResult:
        """Execute a whitelisted action.

        Args:
            action_name: Key in SAFE_ACTIONS.
            params: Parameter values to substitute into the command template.
            confirmed: Must be True to proceed. False is always rejected.

        Returns:
            ActionResult with success/output/error fields.
        """
        # --- Gate 1: action must exist in whitelist ---
        action = self.SAFE_ACTIONS.get(action_name)
        if action is None:
            msg = f"Unknown action: {action_name!r}"
            logger.warning("Action rejected: %s", msg)
            return ActionResult(success=False, output="", error=msg)

        # --- Gate 2: confirmation required ---
        if not confirmed:
            msg = f"Action {action_name!r} requires confirmation"
            logger.warning("Action rejected: %s", msg)
            return ActionResult(success=False, output="", error=msg)

        # --- Gate 3: validate parameters ---
        allowed = action.allowed_params
        provided = frozenset(params)

        missing = allowed - provided
        if missing:
            msg = f"Missing parameters: {sorted(missing)}"
            logger.warning("Action rejected (%s): %s", action_name, msg)
            return ActionResult(success=False, output="", error=msg)

        extra = provided - allowed
        if extra:
            msg = f"Extra parameters not allowed: {sorted(extra)}"
            logger.warning("Action rejected (%s): %s", action_name, msg)
            return ActionResult(success=False, output="", error=msg)

        # --- Build final command (no shell interpretation) ---
        cmd = [part.format(**params) for part in action.command]

        logger.info("Executing action %r: %s", action_name, cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()

            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")

            success = proc.returncode == 0
            if not success:
                logger.warning(
                    "Action %r exited with code %d: %s",
                    action_name,
                    proc.returncode,
                    stderr.strip(),
                )

            return ActionResult(success=success, output=stdout, error=stderr)

        except OSError as exc:
            msg = f"Failed to start process: {exc}"
            logger.error("Action %r OSError: %s", action_name, msg)
            return ActionResult(success=False, output="", error=msg)
