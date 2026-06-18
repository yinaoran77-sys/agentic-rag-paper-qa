"""
应用配置管理

使用 pydantic-settings 从环境变量和 .env 文件加载配置
这是 12-Factor App 的最佳实践
"""

from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    应用配置类
    
    配置优先级（从高到低）：
    1. 环境变量
    2. .env 文件
    3. 默认值
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 忽略未定义的环境变量
    )
    
    # ---- 应用基础配置 ----
    APP_NAME: str = "Agentic RAG Paper QA"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me-in-production"
    
    # ---- CORS ----
    CORS_ORIGINS: List[str] = ["*"]
    
    # ---- 数据库 ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "raguser"
    POSTGRES_PASSWORD: str = "ragpassword"
    POSTGRES_DB: str = "ragdb"
    
    @property
    def DATABASE_URL(self) -> str:
        """异步数据库连接字符串"""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )
    
    @property
    def DATABASE_URL_SYNC(self) -> str:
        """同步数据库连接字符串（Alembic 使用）"""
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )
    
    # ---- Redis ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    
    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    # ---- OpenSearch ----
    OPENSEARCH_HOST: str = "localhost"
    OPENSEARCH_PORT: int = 9200
    OPENSEARCH_INDEX: str = "paper_chunks"
    
    # ---- Ollama ----
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b"

    # ---- LLM 提供商 ----
    # 可选值: "ollama" / "dashscope" / "openai"
    LLM_PROVIDER: str = "dashscope"

    # ---- 通义千问 (DashScope) ----
    DASHSCOPE_API_KEY: str = ""  # 在 .env 文件中设置
    DASHSCOPE_MODEL: str = "qwen-turbo"
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # ---- OpenAI（备选）----
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # ---- 上传目录 ----
    UPLOAD_DIR: str = "/root/agentic-rag-paper-qa/backend/data/papers"
    
    # ---- Langfuse ----
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_HOST: str = "http://localhost:3000"
    
    # ---- 嵌入模型 ----
    EMBEDDING_MODEL: str = "nomic-embed-text"
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 128
    
    # ---- 检索配置 ----
    TOP_K: int = 5
    BM25_WEIGHT: float = 0.4
    VECTOR_WEIGHT: float = 0.6


# 全局配置单例
# 为什么用单例？配置在应用生命周期内不变，避免重复解析
settings = Settings()
