"""
Heartbeat runner for Agent Zero - periodic proactive agent checks.

Inspired by Openclaw's heartbeat mechanism. This module provides:
- Phase-based scheduling (distributed intervals per context)
- Preflight gates (busy check, active hours, empty file)
- HEARTBEAT.md task parsing (YAML-like task blocks)
- Silent communication (runs in background without UI clutter)
"""

import asyncio
import hashlib
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from helpers.extension import Extension
from helpers.print_style import PrintStyle
from helpers import files
from helpers.localization import Localization

HEARTBEAT_TOKEN = "HEARTBEAT_OK"
DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_ACK_MAX_CHARS = 300

# In-memory state for heartbeat runs per context
_heartbeat_state: dict[str, dict] = {}
_state_lock = threading.RLock()


def _get_state(context_id: str) -> dict:
    """Get or create heartbeat state for a context."""
    with _state_lock:
        if context_id not in _heartbeat_state:
            _heartbeat_state[context_id] = {
                "last_run": 0,
                "task_state": {},
            }
        return _heartbeat_state[context_id]


def _resolve_phase(context_id: str, interval_seconds: int) -> int:
    """Resolve a stable phase offset for this context within the interval."""
    interval_seconds = max(1, interval_seconds)
    digest = hashlib.sha256(f"heartbeat:{context_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % interval_seconds


def _normalize_modulo(value: int, divisor: int) -> int:
    """Normalize value to positive modulo."""
    return ((value % divisor) + divisor) % divisor


def _compute_next_due(now: int, interval: int, phase: int) -> int:
    """Compute next heartbeat due timestamp."""
    interval = max(1, interval)
    phase = _normalize_modulo(phase, interval)
    cycle_position = _normalize_modulo(now, interval)
    delta = _normalize_modulo(phase - cycle_position, interval)
    if delta == 0:
        delta = interval
    return now + delta


def _is_task_due(state: dict, task_name: str, interval_str: str, now: int) -> bool:
    """Check if a heartbeat task is due based on its interval."""
    last_run = state.get("task_state", {}).get(task_name)
    if last_run is None:
        return True
    try:
        interval_seconds = _parse_duration(interval_str)
        return now - last_run >= interval_seconds
    except Exception:
        return False


def _parse_duration(raw: str) -> int:
    """Parse a duration string (e.g. '30m', '2h', '1d') into seconds."""
    raw = str(raw).strip().lower()
    if not raw:
        return 30 * 60  # default 30 minutes

    # Try to parse as number with optional unit suffix
    import re
    match = re.match(r'^(\d+(?:\.\d+)?)\s*([smhdw])?$', raw)
    if not match:
        # Try plain number (treat as minutes)
        try:
            return int(float(raw)) * 60
        except ValueError:
            return 30 * 60

    value = float(match.group(1))
    unit = match.group(2) or 'm'

    multipliers = {
        's': 1,
        'm': 60,
        'h': 60 * 60,
        'd': 24 * 60 * 60,
        'w': 7 * 24 * 60 * 60,
    }
    return int(value * multipliers.get(unit, 60))


def _is_within_active_hours(config: dict) -> bool:
    """Check if current time is within configured active hours."""
    active = config.get("active_hours")
    if not active:
        return True

    start = active.get("start", "00:00")
    end = active.get("end", "24:00")
    tz_name = active.get("timezone", "local")

    try:
        from zoneinfo import ZoneInfo
        if tz_name == "local":
            tz = ZoneInfo(Localization.get().get_timezone())
        else:
            tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    now = datetime.now(tz)
    current_min = now.hour * 60 + now.minute

    def parse_time(t: str) -> int:
        parts = t.strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h * 60 + m

    start_min = parse_time(start)
    end_min = parse_time(end)

    if start_min == end_min:
        return False
    if end_min > start_min:
        return start_min <= current_min < end_min
    return current_min >= start_min or current_min < end_min


def _parse_heartbeat_tasks(content: str) -> list[dict]:
    """Parse heartbeat tasks from HEARTBEAT.md content."""
    tasks = []
    lines = content.split("\n")
    in_tasks_block = False

    for i, line in enumerate(lines):
        trimmed = line.strip()
        if trimmed == "tasks:":
            in_tasks_block = True
            continue
        if not in_tasks_block:
            continue
        if not trimmed.startswith("- name:"):
            # Check if we've exited the block
            if trimmed and not trimmed.startswith(" ") and not trimmed.startswith("\t") and not trimmed.startswith("-"):
                in_tasks_block = False
            continue

        name = trimmed.replace("- name:", "").strip().strip('"').strip("'")
        interval = ""
        prompt = ""

        for j in range(i + 1, len(lines)):
            next_line = lines[j]
            next_trimmed = next_line.strip()
            if next_trimmed.startswith("- name:"):
                break
            if next_line.startswith(" ") or next_line.startswith("\t"):
                if next_trimmed.startswith("interval:"):
                    interval = next_trimmed.replace("interval:", "").strip().strip('"').strip("'")
                elif next_trimmed.startswith("prompt:"):
                    prompt = next_trimmed.replace("prompt:", "").strip().strip('"').strip("'")
            else:
                if next_trimmed:
                    in_tasks_block = False
                    break

        if name and interval and prompt:
            tasks.append({"name": name, "interval": interval, "prompt": prompt})

    return tasks


def _read_heartbeat_file(workspace_dir: str, filename: str = "HEARTBEAT.md") -> tuple[Optional[str], list[dict]]:
    """Read HEARTBEAT.md and parse tasks. Returns (content, tasks)."""
    filepath = os.path.join(workspace_dir, filename)
    if not os.path.exists(filepath):
        return None, []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        tasks = _parse_heartbeat_tasks(content)
        return content, tasks
    except Exception:
        return None, []


def _is_content_effectively_empty(content: str) -> bool:
    """Check if HEARTBEAT.md content has no actionable items."""
    if not content:
        return True
    for line in content.split("\n"):
        trimmed = line.strip()
        if not trimmed:
            continue
        if trimmed.startswith("#"):
            continue
        if trimmed.startswith("```"):
            continue
        if trimmed in ("-", "- [ ]", "- [x]", "*", "+"):
            continue
        return False
    return True


class HeartbeatRunner:
    """Central heartbeat scheduler and runner."""

    _instance: Optional["HeartbeatRunner"] = None
    _tasks: dict[str, asyncio.Task] = {}
    _running = False
    _lock = threading.RLock()

    @classmethod
    def get(cls) -> "HeartbeatRunner":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._tasks = {}
        self._running = False

    def start(self):
        """Start heartbeat loops for all active contexts."""
        with self._lock:
            if self._running:
                return
            self._running = True
            from agent import AgentContext
            for ctx in AgentContext.all():
                self._ensure_loop(ctx.id)

    def stop(self):
        """Stop all heartbeat loops."""
        with self._lock:
            self._running = False
            for task in list(self._tasks.values()):
                task.cancel()
            self._tasks.clear()

    def context_started(self, context_id: str):
        """Called when a new context starts - begins heartbeat for it."""
        with self._lock:
            if not self._running:
                return
            self._ensure_loop(context_id)

    def context_ended(self, context_id: str):
        """Called when a context ends - stops its heartbeat."""
        with self._lock:
            task = self._tasks.pop(context_id, None)
            if task and not task.done():
                task.cancel()

    def _ensure_loop(self, context_id: str):
        if context_id in self._tasks and not self._tasks[context_id].done():
            return
        self._tasks[context_id] = asyncio.create_task(
            self._heartbeat_loop(context_id)
        )

    async def _heartbeat_loop(self, context_id: str):
        """Main loop for a single context."""
        from agent import AgentContext

        # Initial delay to let context stabilize
        await asyncio.sleep(5)

        while self._running:
            try:
                context = AgentContext.get(context_id)
                if not context:
                    break

                config = self._get_config(context)
                if not config.get("enabled", True):
                    await asyncio.sleep(60)
                    continue

                # Preflight checks
                skip_reason = self._preflight(context, config)
                if skip_reason:
                    PrintStyle.debug(f"Heartbeat skipped ({context_id}): {skip_reason}")
                    await asyncio.sleep(60)
                    continue

                # Run heartbeat
                await self._run_heartbeat(context, config)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                PrintStyle.error(f"Heartbeat error ({context_id}): {e}")

            await asyncio.sleep(60)

    def _get_config(self, context) -> dict:
        """Resolve heartbeat config for a context."""
        # Try context-level config first
        if hasattr(context, "config") and hasattr(context.config, "additional"):
            cfg = context.config.additional.get("heartbeat", {})
            if cfg:
                return cfg

        # Fallback to global settings
        try:
            from helpers import settings
            s = settings.get_settings()
            return s.get("heartbeat", {})
        except Exception:
            pass

        return {"enabled": False}

    def _preflight(self, context, config: dict) -> Optional[str]:
        """Run preflight gates. Returns skip reason or None if should run."""
        # Check busy
        if config.get("skip_when_busy", True) and context.is_running():
            return "busy"

        # Check active hours
        if not _is_within_active_hours(config):
            return "quiet-hours"

        # Check interval
        interval = _parse_duration(config.get("every", "30m"))
        state = _get_state(context.id)
        now = int(datetime.now(timezone.utc).timestamp())
        phase = _resolve_phase(context.id, interval)
        next_due = _compute_next_due(state["last_run"], interval, phase) if state["last_run"] else now

        if now < next_due:
            return "not-due"

        return None

    async def _run_heartbeat(self, context, config: dict):
        """Execute a single heartbeat run."""
        from agent import AgentContext, UserMessage

        interval = _parse_duration(config.get("every", "30m"))
        now = int(datetime.now(timezone.utc).timestamp())
        state = _get_state(context.id)

        # Read HEARTBEAT.md
        workspace = getattr(getattr(context, "config", None), "additional", {}).get("workspace", ".")
        content, tasks = _read_heartbeat_file(workspace, config.get("heartbeat_file", "HEARTBEAT.md"))

        # Check empty file
        if content is not None and _is_content_effectively_empty(content) and not tasks:
            state["last_run"] = now
            PrintStyle.debug(f"Heartbeat skipped ({context.id}): empty file")
            return

        # Build prompt
        prompt = config.get("prompt",
            "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. "
            "Do not infer or repeat old tasks from prior chats. "
            "If nothing needs attention, reply HEARTBEAT_OK."
        )

        # Check due tasks
        due_tasks = [t for t in tasks if _is_task_due(state, t["name"], t["interval"], now * 1000)]
        if tasks and not due_tasks:
            state["last_run"] = now
            PrintStyle.debug(f"Heartbeat skipped ({context.id}): no tasks due")
            return

        if due_tasks:
            task_list = "\n".join(f"- {t['name']}: {t['prompt']}" for t in due_tasks)
            prompt = (
                f"Run the following periodic tasks (only those due):\n\n"
                f"{task_list}\n\n"
                f"After completing all due tasks, reply HEARTBEAT_OK."
            )
            if content:
                prompt += f"\n\nAdditional context:\n{content}"

        # Run silent heartbeat
        PrintStyle.info(f"Heartbeat running for context {context.id}")
        try:
            task = context.communicate(
                UserMessage(message=prompt, system_message=[]),
                silent=True
            )
            await task.result()

            # Update task timestamps
            for t in due_tasks:
                state["task_state"][t["name"]] = now * 1000
            state["last_run"] = now

        except Exception as e:
            PrintStyle.error(f"Heartbeat run failed ({context.id}): {e}")


class HeartbeatStart(Extension):
    """Extension to start heartbeat runner on framework init."""

    async def execute(self, **kwargs):
        HeartbeatRunner.get().start()
