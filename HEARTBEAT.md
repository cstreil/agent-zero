# Heartbeat Checklist

> This file is read by the heartbeat system during periodic background checks.
> Keep it short and actionable to avoid prompt bloat.

- Quick scan: anything urgent in inboxes?
- If it's daytime, do a lightweight check-in if nothing else is pending.
- If a task is blocked, write down _what is missing_ and ask next time.

---

# Optional: Periodic Tasks

Define interval-based tasks that are only checked when due:

tasks:

- name: inbox-triage
  interval: 30m
  prompt: "Check for urgent unread emails and flag anything time sensitive."

- name: calendar-scan
  interval: 2h
  prompt: "Check for upcoming meetings that need prep or follow-up."

# Additional instructions

- Keep alerts short.
- If nothing needs attention after all due tasks, reply HEARTBEAT_OK.
