import os

from dotenv import load_dotenv

load_dotenv()

# OpenAI 配置 - 支持 OPENAI_ 和 AI_ 两种前缀
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("AI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL") or os.environ.get("AI_BASE_URL", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL") or os.environ.get("AI_MODEL", "gpt-4")

# 兼容旧代码
AI_API_KEY = OPENAI_API_KEY
AI_BASE_URL = OPENAI_BASE_URL
AI_MODEL = OPENAI_MODEL

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "translation_api")
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    (
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    ),
)

# Redis 配置
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_DB = os.environ.get("REDIS_DB", "0")
REDIS_USERNAME = os.environ.get("REDIS_USERNAME", "")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")

# S3 配置
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_ENDPOINT_URL = os.environ.get("AWS_ENDPOINT_URL", None)
S3_BUCKET = os.environ.get("S3_BUCKET", "translation-tasks")
S3_UPLOAD_PREFIX = os.environ.get("S3_UPLOAD_PREFIX", "uploads")
S3_OUTPUT_PREFIX = os.environ.get("S3_OUTPUT_PREFIX", "outputs")
