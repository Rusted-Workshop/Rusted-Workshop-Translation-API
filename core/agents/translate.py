from agno.agent import Agent
from agno.models.openai import OpenAILike
from pydantic import BaseModel

from utlis.config import AI_MODEL, AI_API_KEY, AI_BASE_URL
from core.prompts import translate_prompt


def create_agent(OutputSchema: type[BaseModel] | None):
    return Agent(
        model=OpenAILike(id=AI_MODEL, api_key=AI_API_KEY, base_url=AI_BASE_URL),
        instructions=translate_prompt,
        output_schema=OutputSchema,
        use_json_mode=True,
        structured_outputs=True,
    )
