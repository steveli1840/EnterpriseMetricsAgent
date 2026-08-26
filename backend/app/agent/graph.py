from typing import Literal, TypedDict

from app.agent.service import AgentService, ResolvedIntent


class AgentState(TypedDict, total=False):
    question: str
    user_id: str
    conversation_id: str
    stage: str
    context: dict
    retrieved_context: object
    memory: object
    intent: object
    route: Literal["metric", "explore", "direct"]
    schema_evidence: dict
    result: object


class GovernedAgentRuntime:
    """LangGraph dual-track runtime: metric compiler vs explore tool loop."""

    def __init__(self, service: AgentService):
        self.service = service
        self.graph = self._build_graph()

    def _build_graph(self):
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError:
            return None

        async def authorize(state: AgentState):
            if not state.get("user_id"):
                raise PermissionError("Authenticated user is required")
            return {"stage": "authorized"}

        async def load_memory(state: AgentState):
            memory = self.service.memory_manager.load(
                user_id=state["user_id"],
                conversation_id=state["conversation_id"],
            )
            context = dict(state.get("context") or {})
            context["memory"] = memory.to_prompt_dict()
            return {"stage": "memory_loaded", "memory": memory, "context": context}

        async def retrieve_context(state: AgentState):
            retrieved_context = await self.service.retrieve_context(
                question=state["question"],
                user_id=state["user_id"],
            )
            context = dict(state.get("context") or {})
            context["retrieval"] = {
                "knowledge_refs": (
                    retrieved_context.knowledge_refs if retrieved_context is not None else []
                ),
                "schema_refs": (
                    retrieved_context.schema_refs if retrieved_context is not None else []
                ),
            }
            return {
                "stage": "context_retrieved",
                "retrieved_context": retrieved_context,
                "context": context,
            }

        async def route_intent(state: AgentState):
            resolved = await self.service.understand(
                state["question"],
                state.get("retrieved_context"),
                memory=state.get("memory"),
            )
            route = resolved.route or ("metric" if resolved.is_metric_query else "direct")
            return {
                "stage": "routed",
                "intent": resolved,
                "route": route,
                "context": {
                    **dict(state.get("context") or {}),
                    "route": route,
                    "metric": resolved.metric.model_dump() if resolved.metric else {},
                    "dimensions": resolved.dimensions,
                    "time_window": {"start": resolved.start or "", "end": resolved.end or ""},
                },
            }

        async def lookup_schema(state: AgentState):
            resolved: ResolvedIntent | None = state.get("intent")
            metric = resolved.metric if resolved and resolved.metric else None
            schema_evidence = self.service.lookup_schema(metric) if metric else None
            context = dict(state.get("context") or {})
            context["schema_tool"] = schema_evidence
            return {
                "stage": "schema_loaded",
                "schema_evidence": schema_evidence or {},
                "context": context,
            }

        async def run_metric(state: AgentState):
            result = await self.service.analyze(
                question=state["question"],
                user_id=state["user_id"],
                conversation_id=state["conversation_id"],
                context=state.get("retrieved_context"),
                intent=state.get("intent"),
                schema_evidence=state.get("schema_evidence"),
                memory=state.get("memory"),
            )
            return {"stage": "answered", "result": result}

        async def run_explore(state: AgentState):
            intent = state.get("intent") or ResolvedIntent(route="explore")
            if getattr(intent, "route", None) != "explore":
                intent = ResolvedIntent(route="explore")
            result = await self.service.analyze(
                question=state["question"],
                user_id=state["user_id"],
                conversation_id=state["conversation_id"],
                context=state.get("retrieved_context"),
                intent=intent,
                memory=state.get("memory"),
            )
            return {"stage": "answered", "result": result}

        async def run_direct(state: AgentState):
            result = await self.service.analyze(
                question=state["question"],
                user_id=state["user_id"],
                conversation_id=state["conversation_id"],
                context=state.get("retrieved_context"),
                intent=state.get("intent") or ResolvedIntent(route="direct", direct_answer="请换个方式提问。"),
                memory=state.get("memory"),
            )
            return {"stage": "answered", "result": result}

        def select_route(state: AgentState) -> str:
            route = state.get("route") or "direct"
            if route == "metric":
                return "metric"
            if route == "explore":
                return "explore"
            return "direct"

        builder = StateGraph(AgentState)
        builder.add_node("authorize", authorize)
        builder.add_node("load_memory", load_memory)
        builder.add_node("retrieve_context", retrieve_context)
        builder.add_node("route_intent", route_intent)
        builder.add_node("lookup_schema", lookup_schema)
        builder.add_node("run_metric", run_metric)
        builder.add_node("run_explore", run_explore)
        builder.add_node("run_direct", run_direct)
        builder.add_edge(START, "authorize")
        builder.add_edge("authorize", "load_memory")
        builder.add_edge("load_memory", "retrieve_context")
        builder.add_edge("retrieve_context", "route_intent")
        builder.add_conditional_edges(
            "route_intent",
            select_route,
            {
                "metric": "lookup_schema",
                "explore": "run_explore",
                "direct": "run_direct",
            },
        )
        builder.add_edge("lookup_schema", "run_metric")
        builder.add_edge("run_metric", END)
        builder.add_edge("run_explore", END)
        builder.add_edge("run_direct", END)
        return builder.compile()

    async def analyze(self, **inputs):
        if self.graph is None:
            return await self.service.analyze(**inputs)
        state = await self.graph.ainvoke(inputs)
        return state["result"]
