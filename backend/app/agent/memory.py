from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingMemory:
    entities: dict[str, Any] = field(default_factory=dict)
    last_intent: str | None = None
    last_result_ref: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": self.entities,
            "last_intent": self.last_intent,
            "last_result_ref": self.last_result_ref,
        }

    @classmethod
    def from_dict(cls, payload: dict | None) -> "WorkingMemory":
        data = payload or {}
        return cls(
            entities=dict(data.get("entities") or {}),
            last_intent=data.get("last_intent"),
            last_result_ref=dict(data.get("last_result_ref") or {}),
        )


@dataclass
class AgentMemory:
    """Session working memory + recent episodic turns + confirmed long-term prefs."""

    working: WorkingMemory = field(default_factory=WorkingMemory)
    recent_turns: list[dict[str, Any]] = field(default_factory=list)
    long_term: list[dict[str, Any]] = field(default_factory=list)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "working": self.working.to_dict(),
            "recent_turns": self.recent_turns[-6:],
            "long_term": self.long_term[:8],
        }


class MemoryManager:
    """Load/commit structured agent memory for multi-turn coherence."""

    def __init__(self, repository=None, *, max_turns: int = 12):
        self.repository = repository
        self.max_turns = max_turns

    def load(self, *, user_id: str, conversation_id: str) -> AgentMemory:
        if self.repository is None:
            return AgentMemory()

        snapshot = self.repository.get_conversation_snapshot(user_id, conversation_id)
        working_payload = snapshot.get("working") or snapshot.get("focus") or {}
        # Legacy flat focus payloads stored table/metric at the top level.
        if "entities" not in working_payload and (
            "table" in working_payload or "metric" in working_payload
        ):
            working_payload = {
                "entities": {
                    key: value
                    for key, value in working_payload.items()
                    if key in {"table", "metric", "dimensions"}
                },
                "last_intent": working_payload.get("last_intent"),
                "last_result_ref": working_payload.get("last_result_ref") or {},
            }
        working = WorkingMemory.from_dict(working_payload)
        events = list(snapshot.get("events") or [])
        recent_turns = [
            {
                "role": event.get("type") or event.get("role") or "unknown",
                "content": event.get("content") or event.get("summary") or "",
                "entities": event.get("entities") or {},
            }
            for event in events
            if event.get("content") or event.get("summary")
        ][-self.max_turns :]

        long_term: list[dict[str, Any]] = []
        try:
            memories = self.repository.list_user_memories(user_id, confirmed_only=True)
        except Exception:
            memories = []
        for memory in memories[:8]:
            long_term.append({"kind": memory.kind, "value": memory.value})

        return AgentMemory(working=working, recent_turns=recent_turns, long_term=long_term)

    def commit_turn(
        self,
        *,
        user_id: str,
        conversation_id: str,
        question: str,
        answer: str,
        intent: Any | None = None,
        columns: list[str] | None = None,
        rows: list[list] | None = None,
        sql: str | None = None,
        trace_id: str | None = None,
        time_window: dict[str, str] | None = None,
        pending_action: dict[str, Any] | None = None,
        clear_pending: bool = False,
        result: dict[str, Any] | None = None,
    ) -> AgentMemory:
        memory = self.load(user_id=user_id, conversation_id=conversation_id)
        entities = self.extract_entities(
            intent=intent,
            columns=columns or [],
            rows=rows or [],
            sql=sql or "",
            prior=memory.working.entities,
            time_window=time_window,
            pending_action=pending_action,
            clear_pending=clear_pending,
        )
        last_intent = self._intent_label(intent)
        result_ref = {
            "row_count": len(rows or []),
            "columns": (columns or [])[:8],
            "sql_preview": (sql or "")[:240],
        }
        if rows:
            result_ref["top"] = [[str(cell) for cell in row[:4]] for row in rows[:3]]

        memory.working = WorkingMemory(
            entities=entities,
            last_intent=last_intent,
            last_result_ref=result_ref,
        )

        if self.repository is None:
            return memory

        self.repository.append_conversation_event(
            user_id,
            conversation_id,
            {
                "type": "assistant",
                "content": answer,
                "summary": answer[:240],
                "entities": entities,
                "trace_id": trace_id,
                "result": result,
            },
        )
        self.repository.update_conversation_focus(
            user_id,
            conversation_id,
            memory.working.to_dict(),
        )
        return memory

    @staticmethod
    def extract_entities(
        *,
        intent: Any | None,
        columns: list[str],
        rows: list[list],
        sql: str,
        prior: dict[str, Any] | None = None,
        time_window: dict[str, str] | None = None,
        pending_action: dict[str, Any] | None = None,
        clear_pending: bool = False,
    ) -> dict[str, Any]:
        entities = dict(prior or {})
        col_lower = [column.lower() for column in columns]

        if intent is not None and getattr(intent, "is_metric_query", False):
            metric = getattr(intent, "metric", None)
            if metric is not None:
                entities["metric"] = {
                    "name": metric.name,
                    "label": metric.label,
                    "model": metric.model,
                    "allowed_dimensions": list(getattr(metric, "allowed_dimensions", []) or []),
                }
                entities["table"] = {"ref": metric.model, "name": metric.model.split(".")[-1]}
            dimensions = getattr(intent, "dimensions", None) or []
            if dimensions:
                entities["dimensions"] = list(dimensions)
            start = getattr(intent, "start", None)
            end = getattr(intent, "end", None)
            if start or end:
                entities["time_window"] = {
                    "start": start or "",
                    "end": end or "",
                }

        if time_window and (time_window.get("start") or time_window.get("end")):
            entities["time_window"] = {
                "start": time_window.get("start") or "",
                "end": time_window.get("end") or "",
            }

        if clear_pending:
            entities.pop("pending_action", None)
        elif pending_action is not None:
            entities["pending_action"] = pending_action

        # Catalog result shaped like system.tables: database, name[, total_rows]
        if rows and "name" in col_lower and "database" in col_lower:
            db_idx = col_lower.index("database")
            name_idx = col_lower.index("name")
            database = str(rows[0][db_idx])
            name = str(rows[0][name_idx])
            entities["table"] = {
                "database": database,
                "name": name,
                "ref": f"{database}.{name}",
            }

        # Catalog result shaped like system.columns: database, table, name, type
        if rows and "table" in col_lower and "name" in col_lower and "type" in col_lower:
            table_idx = col_lower.index("table")
            db_idx = col_lower.index("database") if "database" in col_lower else None
            table_name = str(rows[0][table_idx])
            database = str(rows[0][db_idx]) if db_idx is not None else None
            ref = f"{database}.{table_name}" if database else table_name
            entities["table"] = {
                "database": database,
                "name": table_name,
                "ref": ref,
            }

        # SQL WHERE table = '...' fallback
        lowered = sql.lower()
        if "system.columns" in lowered and "table" not in entities:
            match = re.search(r"\btable\s*=\s*'([^']+)'", sql, re.I)
            db_match = re.search(r"\bdatabase\s*=\s*'([^']+)'", sql, re.I)
            if match:
                table_name = match.group(1)
                database = db_match.group(1) if db_match else None
                entities["table"] = {
                    "database": database,
                    "name": table_name,
                    "ref": f"{database}.{table_name}" if database else table_name,
                }

        return entities

    @staticmethod
    def _intent_label(intent: Any | None) -> str | None:
        if intent is None:
            return None
        route = getattr(intent, "route", None)
        if route == "explore":
            return "catalog.explore"
        if getattr(intent, "is_metric_query", False) or route == "metric":
            metric = getattr(intent, "metric", None)
            return f"metric:{getattr(metric, 'name', 'unknown')}"
        if getattr(intent, "direct_answer", None) or route == "direct":
            return "direct_answer"
        return None
