import os

from dotenv import load_dotenv

load_dotenv()

AI_BASE_URL = os.environ.get("AI_BASE_URL")
AI_API_KEY = os.environ.get("AI_API_KEY")
AI_MODEL = os.environ.get("AI_MODEL")
