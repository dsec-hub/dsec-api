"""Best-effort hand-off of task-assignment events to dsec-hub.

dsec-api has no notification delivery of its own — email/Telegram/Discord and
the per-user channel prefs all live in the committee dashboard (dsec-hub). When
a task is (re)assigned via the REST API or the MCP server, those writes never
touch hub's server actions, so the dashboard's on-assign hook can't fire. To
close that gap we POST a small event to hub's internal endpoint, which runs the
very same on-assign notifier the dashboard uses (honouring the recipient's
channel prefs + dedupe).

This is a one-way, fire-and-forget *event emission*, NOT a data dependency: it
runs after the task is already committed, uses a short timeout, and swallows
every error. A slow or unreachable hub never blocks or fails a task write.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# This runs inline on the assignment write path, so a slow/unreachable hub must
# not stall the response for long. Hub's handler is a thin DB-backed dispatch,
# so a few seconds is generous.
_TIMEOUT = httpx.Timeout(4.0)


def notify_task_assigned(
    *,
    task_id: int,
    assignee_person_id: int,
    actor_user_id: int | None = None,
) -> None:
    """Tell dsec-hub a task was (re)assigned so it can notify the assignee.

    `assignee_person_id` is a people.id (Task.assignee_id); hub maps it to the
    assignee's login user. `actor_user_id` is the app_user who made the change,
    used by hub to skip self-assignment — pass None (the default) when we can't
    resolve one (API-key / MCP callers), and hub will always notify.

    No-op when the hand-off isn't configured (blank URL/secret). Never raises.
    """
    if not settings.HUB_NOTIFY_URL or not settings.HUB_NOTIFY_SECRET:
        return
    try:
        httpx.post(
            settings.HUB_NOTIFY_URL,
            json={
                "taskId": task_id,
                "assigneePersonId": assignee_person_id,
                "actorUserId": actor_user_id,
            },
            headers={"Authorization": f"Bearer {settings.HUB_NOTIFY_SECRET}"},
            timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; a failed ping must never break the write
        logger.warning("task-assigned notify hand-off failed (task %s): %s", task_id, exc)
