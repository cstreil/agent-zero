"""Inject heartbeat context into system prompt when running a heartbeat turn."""

from helpers.extension import Extension
from agent import LoopData


class HeartbeatSystemPrompt(Extension):
    """Add heartbeat instructions to system prompt during silent turns."""

    async def execute(
        self,
        system_prompt: list[str] = [],
        loop_data: LoopData = LoopData(),
        **kwargs,
    ):
        if not self.agent:
            return

        # Only inject during heartbeat turns
        if not self.agent.context.data.get("_heartbeat_silent"):
            return

        system_prompt.append(
            "You are in HEARTBEAT mode. This is a periodic background check. "
            "Review HEARTBEAT.md if it exists in your workspace. "
            "Check for any pending tasks, urgent items, or follow-ups. "
            "Do not infer or repeat old tasks from prior conversation history. "
            "Be concise. If nothing needs attention, reply exactly: HEARTBEAT_OK"
        )
