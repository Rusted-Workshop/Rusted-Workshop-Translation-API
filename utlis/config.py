import os

from dotenv import load_dotenv

load_dotenv()

AI_BASE_URL = os.environ.get("AI_BASE_URL", "")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "")

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_DB = os.environ.get("REDIS_DB", "0")
REDIS_USERNAME = os.environ.get("REDIS_USERNAME", "")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")

# S3配置
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_ENDPOINT_URL = os.environ.get("AWS_ENDPOINT_URL", None)
S3_BUCKET = os.environ.get("S3_BUCKET", "translation-tasks")
S3_UPLOAD_PREFIX = os.environ.get("S3_UPLOAD_PREFIX", "uploads")
S3_OUTPUT_PREFIX = os.environ.get("S3_OUTPUT_PREFIX", "outputs")
