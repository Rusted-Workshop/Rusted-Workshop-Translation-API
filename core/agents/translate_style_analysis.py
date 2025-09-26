from core.config import AI_BASE_URL, AI_MODEL, AI_API_KEY
from agno.models.openai.like import OpenAILike
from agno.agent.agent import Agent
from core.prompts import translate_style_prompt

translate_style_analysis_agent = Agent(
    model=OpenAILike(id=AI_MODEL, api_key=AI_API_KEY, base_url=AI_BASE_URL),
    instructions=translate_style_prompt,
)
