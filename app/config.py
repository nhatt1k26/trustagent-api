from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET: str

    MAIL_HOST: str = "smtp.gmail.com"
    MAIL_PORT: int = 587
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = ""

    FE_LOGIN_URL: str = "https://trustagent.io.vn/"

    # ─── Kiro AI Chat (ACP) ──────────────────────────────────────────────
    # API key do người dùng tự set trong .env; kiro-cli đọc từ biến môi trường này.
    KIRO_API_KEY: str = ""
    # Đường dẫn tới binary kiro-cli (mặc định lấy trong PATH).
    KIRO_CLI_PATH: str = "kiro-cli"
    # Thư mục làm việc riêng cho các phiên kiro (KIRO_HOME).
    KIRO_WORK_DIR: str = "~/.trustagent-kiro"
    # Thời gian (giây) trước khi thu hồi phiên nhàn rỗi.
    KIRO_TIMEOUT: int = 900
    # Agent / model mặc định (để trống dùng mặc định của kiro-cli).
    KIRO_AGENT: str = ""
    KIRO_MODEL: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
