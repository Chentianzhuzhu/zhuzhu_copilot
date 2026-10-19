"""配置管理模块：从环境变量和 .env 文件读取配置"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # MySQL
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_db: str = "zhuzhu_copilot_auth"

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    # Redis 6+ ACL 用户名；留空 = 以 default 用户认证（兼容旧行为）。
    # 指向远程缓存节点时使用 api_app 账号（见 outputs/redis_api_integration.md）。
    redis_user: str = ""

    # L2 远程共享缓存节点（积分余额缓存/原子扣减等正确性敏感 + 跨机共享数据）。
    # redis_l2_host 留空 = 未启用 L2，redis_l2 回落本机 redis_url（行为与旧版一致）。
    redis_l2_host: str = ""
    redis_l2_port: int = 6379
    redis_l2_db: int = 0
    redis_l2_user: str = ""
    redis_l2_password: str = ""

    # JWT
    jwt_secret: str = "default_secret_change_in_production"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24
    # 生产环境开关：为 true 时拒绝使用默认/弱 JWT 密钥启动（避免线上用示例密钥）
    require_strong_jwt_secret: bool = False
    # 客户端登录令牌（access token）有效期，由 /api/auth/refresh 滑动续期
    access_token_expire_minutes: int = 120
    # 会话续期窗口：使用中的 token 每次访问敏感接口时滑动续期，
    # 空闲超过该时长后会话过期（需重新登录/刷新）。
    session_idle_grace_minutes: int = 60

    # 登录安全：失败锁定（同一账号/IP 在窗口内累计失败次数达到阈值即锁定）
    login_max_fail: int = 5               # 窗口内最大失败次数
    login_lock_minutes: int = 5           # 锁定分钟数
    login_fail_window_minutes: int = 5    # 滑动统计窗口（分钟）

    # 登录握手 state：客户端登录前申请一次性随机 state，登录成功后原样回传，
    # 服务端校验并消费，防止 login CSRF / 重放（Redis 单次消费 + TTL）。
    login_state_ttl_seconds: int = 600    # state 有效期（10 分钟）
    # 是否强制要求登录请求携带有效 state（true 时无 state 的登录/注册被拒绝）
    require_login_state: bool = False

    # 管理员登录安全：更严格，防止爆破后台
    admin_max_fail: int = 5
    admin_lock_minutes: int = 15

    # CORS：生产环境建议配置为具体前端来源（逗号分隔），禁止使用 * 搭配 credentials
    cors_origins: str = "*"

    # 服务器
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # 默认管理员
    admin_username: str = "zhutianlaing"
    admin_password: str = "windows10"

    @property
    def mysql_url(self) -> str:
        auth = f"{self.mysql_user}:{self.mysql_password}"
        return (
            f"mysql+aiomysql://{auth}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        if self.redis_user and self.redis_password:
            # Redis 6+ ACL：redis://<user>:<password>@host:port/db
            return f"redis://{self.redis_user}:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def redis_l2_url(self) -> str:
        if not self.redis_l2_host:
            # 未启用 L2：与本机 redis 同一实例，业务代码无需分支判断
            return self.redis_url
        if self.redis_l2_user and self.redis_l2_password:
            return f"redis://{self.redis_l2_user}:{self.redis_l2_password}@{self.redis_l2_host}:{self.redis_l2_port}/{self.redis_l2_db}"
        if self.redis_l2_password:
            return f"redis://:{self.redis_l2_password}@{self.redis_l2_host}:{self.redis_l2_port}/{self.redis_l2_db}"
        return f"redis://{self.redis_l2_host}:{self.redis_l2_port}/{self.redis_l2_db}"

    @property
    def cors_origin_list(self) -> list:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()