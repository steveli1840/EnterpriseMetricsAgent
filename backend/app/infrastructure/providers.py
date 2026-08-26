from collections.abc import Sequence

from openai import AsyncOpenAI

from app.settings import Settings


class DeepSeekChatProvider:
    def __init__(self, settings: Settings):
        self.model = settings.deepseek_model
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key.get_secret_value(),
            base_url=settings.deepseek_api_base,
        )

    async def health(self) -> dict:
        response = await self.client.models.list()
        available = {model.id for model in response.data}
        if self.model not in available:
            raise ValueError(f"configured DeepSeek model is unavailable: {self.model}")
        return {"model": self.model, "status": "available"}

    async def complete_json(self, messages: list[dict]) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
        )
        return response.choices[0].message.content or "{}"

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str | dict = "auto",
    ) -> dict:
        """Return assistant message payload including optional tool_calls."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or None,
            tool_choice=tool_choice if tools else None,
            temperature=0,
        )
        message = response.choices[0].message
        tool_calls = []
        for call in message.tool_calls or []:
            tool_calls.append(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments or "{}",
                    },
                }
            )
        return {
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": tool_calls,
        }


class DashScopeEmbeddingProvider:
    def __init__(self, settings: Settings):
        self.model = settings.dashscope_embedding_model
        self.dimensions = settings.dashscope_embedding_dimensions
        self.client = AsyncOpenAI(
            api_key=settings.dashscope_api_key.get_secret_value(),
            base_url=settings.dashscope_api_base,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=self.model,
            input=list(texts),
            dimensions=self.dimensions,
            encoding_format="float",
        )
        vectors = [item.embedding for item in response.data]
        if any(len(vector) != self.dimensions for vector in vectors):
            raise ValueError("embedding provider returned an unexpected vector dimension")
        return vectors
