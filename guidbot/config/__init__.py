"""
config 패키지

[공개 API]
  from config import settings           # 전역 설정 싱글톤
  from config.settings import AppSettings  # 설정 클래스 (타입 힌트용)

[설계 원칙]
  - settings 싱글톤은 앱 기동 시 단 1회 생성 (pydantic-settings)
  - .env 파일 + 환경변수 자동 로드 및 타입 검증
  - SecretStr 필드로 민감 정보 자동 마스킹
"""

from config.settings import AppSettings, settings

__all__ = ["settings", "AppSettings"]
