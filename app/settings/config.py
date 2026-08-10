import os
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


class Settings(BaseSettings):
    VERSION: str = "0.1.0"
    APP_TITLE: str = "Agent DeepSeek"
    APP_DESCRIPTION: str = "Description"

    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["*"])
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list[str] = Field(default_factory=lambda: ["*"])
    CORS_ALLOW_HEADERS: list[str] = Field(default_factory=lambda: ["*"])

    ADD_LOG_ORIGINS_INCLUDE: list[str] = Field(default_factory=lambda: ["*"])
    ADD_LOG_ORIGINS_DECLUDE: list[str] = Field(default_factory=lambda: ["/system-manage", "/redoc", "/doc", "/openapi.json", "/app-config", "/track"])

    DEBUG: bool = False

    PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
    BASE_DIR: Path = PROJECT_ROOT.parent
    LOGS_ROOT: Path = BASE_DIR / "logs/"
    STATIC_ROOT: Path = BASE_DIR / "static/"
    # JWT 签名密钥：生产环境必须在 .env 中显式配置（openssl rand -hex 32 生成），
    # 此默认值仅供本地开发，切勿用于任何真实部署
    SECRET_KEY: str = "dev-only-insecure-secret-key-change-me"
    SERVICE_API_KEY: str = ""  # 对外服务的 API Key，空字符串表示未启用
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12  # 12 hours
    JWT_REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    TORTOISE_ORM: dict[str, Any] = Field(default_factory=dict)

    DATETIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"

    REDIS_URL: str = "redis://redis:6379/0"  # "redis://:password@233.233.233.233:33333/0"

    # standard_ 表专用 MySQL 连接（占位符，在 .env 中填写实际值）
    STANDARD_MYSQL_HOST: str = "localhost"
    STANDARD_MYSQL_PORT: int = 3306
    STANDARD_MYSQL_USER: str = "root"
    STANDARD_MYSQL_PASSWORD: str = ""
    STANDARD_MYSQL_DB: str = "standard_db"

    # MineRU PDF 解析服务（在 .env 中填写实际地址）
    MINERU_BASE_URL: str = "http://127.0.0.1:7860"

    # 标准 PDF 配置接口基础 URL（word 字段填充所用的 get-config-item 接口所在服务）
    JGH_PDF_API_BASE: str = "http://127.0.0.1:48888/admin-api"
    # 标准 PDF 配置接口鉴权 AppId（X-App-Id 请求头）
    JGH_PDF_APP_ID: str = ""
    # 标准文档图片基础 URL（jgh-bag 表格/公式图片前缀，MinerU 解析、Agent 读图、定时回填共用，需以 / 结尾）
    JGH_IMAGE_BASE_URL: str = "http://127.0.0.1:48888/admin-api/standard/jgh-bag/image/"

    # 服务启动时是否自动续跑未完成的查重批次（false 时由前端手动触发 /batch-resume）
    AUTO_RESUME_DEDUP_BATCH: bool = True

    # seekdb（向量库 + hybrid search，个人知识库后端）
    # 连接复用 STANDARD_MYSQL_*（同实例同端口）
    SEEKDB_KB_COLLECTION: str = "kb_entries"
    SEEKDB_MEMSTORE_COLLECTION: str = "langmem_store"

    # 内容审核：none（关闭）| keyword（本地关键词表）
    CONTENT_MODERATION: str = "none"
    CONTENT_MODERATION_KEYWORDS_FILE: str = ""  # 留空则用 app/utils/keywords_blocklist.txt

    # 阿里云短信服务
    ALIYUN_ACCESS_KEY_ID: str = ""
    ALIYUN_ACCESS_KEY_SECRET: str = ""
    ALIYUN_SMS_SIGN_NAME: str = ""  # 默认短信签名
    ALIYUN_SMS_TEMPLATE_CODE: str = ""  # 验证码短信模板 Code
    ALIYUN_SMS_PROVIDER: str = "dypnsapi"  # dypnsapi（号码认证服务）| dysmsapi（短信服务）

    # 品牌变体：standard（行业版，无积分限制）| generic（通用版，受积分配额约束）
    BRAND_VARIANT: str = "standard"
    # generic 模式下每用户默认积分配额
    DEFAULT_CREDIT_QUOTA: int = 200_000

    # compare-smart-v2 / extract-batch-fast 快/慢路径阈值
    # compact 文本字符超过此值走慢路径（约 90k tokens，128k 窗口安全）
    SMART_V2_FAST_BATCH_COMPACT_LIMIT: int = 80_000

    class Config:
        env_file = (".env", os.getenv("ENV_FILE", ".env"))
        env_file_encoding = "utf-8"
        extra = "ignore"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        # .env 优先级高于进程环境变量，避免 shell 中残留的 export 污染配置
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    @model_validator(mode="after")
    def _build_tortoise_orm(self):
        self.TORTOISE_ORM = {
            "connections": {
                "conn_standard": {
                    "engine": "tortoise.backends.mysql",
                    "credentials": {
                        "host": self.STANDARD_MYSQL_HOST,
                        "port": self.STANDARD_MYSQL_PORT,
                        "user": self.STANDARD_MYSQL_USER,
                        "password": self.STANDARD_MYSQL_PASSWORD,
                        "database": self.STANDARD_MYSQL_DB,
                        "charset": "utf8mb4",
                    },
                },
            },
            "apps": {
                "app_system": {
                    "models": ["app.models.system"],
                    "default_connection": "conn_standard",
                },
                "app_standard": {
                    "models": ["app.models.standard"],
                    "default_connection": "conn_standard",
                },
            },
            "use_tz": False,
            "timezone": "Asia/Shanghai",
        }
        return self


settings = Settings()
