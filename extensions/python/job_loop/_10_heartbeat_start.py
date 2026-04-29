"""Start heartbeat runner on framework initialization."""

from helpers.extension import Extension
from helpers.heartbeat import HeartbeatRunner


class HeartbeatJobLoop(Extension):
    """Extension to start heartbeat runner during job loop initialization."""

    async def execute(self, **kwargs):
        HeartbeatRunner.get().start()
