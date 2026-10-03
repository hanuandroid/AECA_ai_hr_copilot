from datetime import date

from app.services.ai.authorization import AIUserContext, DataScope
from app.services.ai.llm.base import LLMMessage, LLMProvider
from app.services.ai.sql.models import SQLGenerationOutput
from app.services.ai.sql.prompts import SQL_SYSTEM_PROMPT, build_generation_message


class SQLGenerator:
    """Asks the (untrusted) model for a SQL string. Its output is never executed directly."""

    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def generate(self, question: str, ctx: AIUserContext, scope: DataScope, today: date) -> SQLGenerationOutput:
        return await self._llm.generate_structured(
            [LLMMessage(role="user", content=build_generation_message(question, ctx, scope, today))],
            SQLGenerationOutput,
            system=SQL_SYSTEM_PROMPT,
        )
