"""
utils 패키지 ─ 공통 유틸리티 모듈

[패키지 구성]
  exceptions   : 비즈니스 예외 계층 (GuidbotError 루트)
  logger       : 구조화 로거 (request ID, 성능 타이밍 지원)
  monitor      : 성능 메트릭 수집기 (Streamlit 세션 통합)
  file_sync    : 파일 동기화 유틸리티 (G드라이브 → 로컬)
  text_cleaner : 텍스트 전처리 (공백 정규화, 노이즈 제거)

[빠른 사용]
  from utils.logger import get_logger, ContextLogger
  from utils.exceptions import GuidbotError, LLMQuotaError
  from utils.monitor import get_metrics
"""

from utils.exceptions import (
    GuidbotError,
    LLMError,
    LLMQuotaError,
    RetrievalError,
    VectorStoreError,
    DBConnectionError,
)
from utils.logger import get_logger, ContextLogger
from utils.monitor import get_metrics

__all__ = [
    "GuidbotError",
    "LLMError",
    "LLMQuotaError",
    "RetrievalError",
    "VectorStoreError",
    "DBConnectionError",
    "get_logger",
    "ContextLogger",
    "get_metrics",
]
