"""Agent reliability scores.

A score is only useful if an operator can say why it moved, so it is a fixed,
documented formula over stored counts - no model involved:

    score = 100 x (0.5 x task_success + 0.3 x clean_rate + 0.2 x tool_success)

* ``task_success``  completed tasks / finished tasks (completed, failed,
                    cancelled, abandoned). Abandoned counts against: the agent
                    never reported finishing.
* ``clean_rate``    finished tasks with no incident / finished tasks.
* ``tool_success``  1 - failed tool calls / tool calls.

Fewer than ``MIN_TASKS`` finished tasks gives ``None`` ("not enough data")
rather than a confident-looking number from two samples.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Incident, Task, TaskStatus

MIN_TASKS = 3
WEIGHTS = {"task_success": 0.5, "clean_rate": 0.3, "tool_success": 0.2}


async def reliability_scores(
    session: AsyncSession, user_id: str, agent_ids: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    task_query = (
        select(
            Task.agent_id,
            Task.status,
            func.count(Task.id),
            func.coalesce(func.sum(Task.tool_call_count), 0),
            func.coalesce(func.sum(Task.error_count), 0),
        )
        .where(Task.user_id == user_id)
        .group_by(Task.agent_id, Task.status)
    )
    if agent_ids:
        task_query = task_query.where(Task.agent_id.in_(agent_ids))
    rows = (await session.execute(task_query)).all()

    incident_tasks = dict(
        (
            await session.execute(
                select(Incident.agent_id, func.count(func.distinct(Incident.task_id)))
                .where(Incident.user_id == user_id)
                .where(Incident.task_id.is_not(None))
                .group_by(Incident.agent_id)
            )
        ).all()
    )

    stats: dict[str, dict[str, int]] = {}
    for agent_id, status, count, tools, errors in rows:
        s = stats.setdefault(
            agent_id, {"finished": 0, "completed": 0, "tools": 0, "errors": 0, "running": 0}
        )
        if status in TaskStatus.TERMINAL:
            s["finished"] += count
            if status == TaskStatus.COMPLETED:
                s["completed"] += count
        else:
            s["running"] += count
        s["tools"] += int(tools)
        s["errors"] += int(errors)

    out: dict[str, dict[str, Any]] = {}
    for agent_id, s in stats.items():
        finished = s["finished"]
        components = {
            "task_success": s["completed"] / finished if finished else None,
            "clean_rate": (
                max(0.0, 1 - incident_tasks.get(agent_id, 0) / finished) if finished else None
            ),
            "tool_success": 1 - s["errors"] / s["tools"] if s["tools"] else 1.0,
        }
        score = None
        if finished >= MIN_TASKS:
            score = round(
                100 * sum(WEIGHTS[k] * (components[k] or 0.0) for k in WEIGHTS), 1
            )
        out[agent_id] = {
            "score": score,
            "components": {k: (round(v, 4) if v is not None else None) for k, v in components.items()},
            "finished_tasks": finished,
            "running_tasks": s["running"],
            "reason": None if score is not None else f"Needs {MIN_TASKS} finished tasks (has {finished}).",
        }
    return out
