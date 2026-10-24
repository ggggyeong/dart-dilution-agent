"""설정: 프로젝트 루트의 .env를 읽어 타입이 정해진 객체로 제공한다."""

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenDART
    dart_api_key: SecretStr

    # B001 수집 기간. 분석 구간 앞 6개월은 정정본의 원본을 찾기 위한 여유분
    collect_start_date: date = date(2023, 4, 16)
    collect_end_date: date = date(2026, 10, 15)
    analysis_start_date: date = date(2023, 10, 16)

    # PostgreSQL — 기본값은 docker-compose.yml과 같다
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "filingscope"
    postgres_password: SecretStr = SecretStr("filingscope")
    postgres_db: str = "filingscope"

    # KRX 정보데이터시스템 로그인 (pykrx의 지수·시가총액·종목 목록에 필요)
    krx_id: str | None = None
    krx_pw: SecretStr | None = None

    # OpenAI — RAG 조각 임베딩 (text-embedding-3-small, scripts/rag_chunk.py)
    openai_api_key: SecretStr | None = None

    # LLM (Event Agent의 LLM 단계부터 사용)
    anthropic_api_key: SecretStr | None = None

    @property
    def postgres_conninfo(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"user={self.postgres_user} password={self.postgres_password.get_secret_value()} "
            f"dbname={self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # pykrx는 .env를 읽지 않고 환경 변수 KRX_ID/KRX_PW만 본다.
    # pykrx를 import하기 전에 get_settings()가 먼저 호출되어야 한다.
    if settings.krx_id and settings.krx_pw:
        os.environ.setdefault("KRX_ID", settings.krx_id)
        os.environ.setdefault("KRX_PW", settings.krx_pw.get_secret_value())
    return settings
