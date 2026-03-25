"""
utils/exceptions.py ─ 커스텀 예외 계층 (v2.0)

[설계 원칙]
- 모든 비즈니스 예외는 GuidbotError 를 상속 → 단일 try/except 로 일괄 처리 가능
- 예외 클래스 이름만 보고도 발생 위치·원인 즉시 파악 가능 (자체 문서화)
- retryable 플래그로 상위 로직에서 자동 재시도 여부 판단
- HTTP 상태 코드 포함 → 향후 REST API 서버 확장 시 즉시 활용 가능
- context 딕셔너리로 디버깅에 필요한 추가 정보를 구조화

[예외 계층 구조]
GuidbotError                    ← 모든 비즈니스 예외의 루트
├── ConfigurationError          설정값 오류 (앱 기동 불가 상태)
├── VectorStoreError            FAISS 벡터 DB 관련
│   ├── DBNotFoundError         DB 파일이 존재하지 않음
│   └── DBBuildError            DB 구축 실패
├── EmbeddingError              임베딩 모델 로드/실행 실패
├── RetrievalError              문서 검색 실패
├── LLMError                    Gemini API 호출 실패
│   └── LLMQuotaError           API 할당량 초과 (429)
├── DatabaseError               병원 DB 관련 기본 예외
│   ├── DBConnectionError       DB 서버 연결 실패
│   └── DBPermissionError       DB 권한 부족
├── DocumentProcessError        PDF 문서 파싱 실패
└── AuthenticationError         관리자 인증 실패

[사용 예시]
    # 검색 실패
    raise RetrievalError(query="연차휴가", reason="FAISS 인덱스 손상")

    # 한 번에 모든 비즈니스 예외 처리
    try:
        result = retrieve(query, db)
    except GuidbotError as exc:
        logger.error(exc.message)
        if exc.retryable:
            # 재시도 로직
            pass
"""

from __future__ import annotations

from typing import Any


class GuidbotError(Exception):
    """
    가이드봇 최상위 예외.

    모든 비즈니스 예외의 부모 클래스입니다.
    이 클래스를 catch 하면 모든 가이드봇 예외를 일괄 처리할 수 있습니다.

    Attributes:
        message:     사용자에게 표시하거나 로그에 기록할 메시지
        retryable:   True 이면 상위 로직에서 자동 재시도 가능
                     (일시적 네트워크 오류 등은 True, 설정 오류는 False)
        status_code: HTTP 상태 코드 (향후 REST API 서버 확장 시 사용)
        context:     디버깅용 추가 정보 딕셔너리
                     (예: {"query": "연차휴가", "model": "ko-sroberta"})
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int = 500,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Args:
            message:     에러 메시지 (로그·UI 표시용)
            retryable:   재시도 가능 여부 (키워드 전용 인자)
            status_code: HTTP 상태 코드 (키워드 전용 인자)
            context:     추가 디버깅 정보 딕셔너리 (키워드 전용 인자)
        """
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.context: dict[str, Any] = context or {}

    def __repr__(self) -> str:
        """디버깅 시 예외 정보를 명확하게 표시합니다."""
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"retryable={self.retryable}, "
            f"context={self.context})"
        )


# ──────────────────────────────────────────────────────────────────────
#  설정 오류
# ──────────────────────────────────────────────────────────────────────

class ConfigurationError(GuidbotError):
    """
    설정값 오류 (앱 기동 불가 상태).

    발생 예시:
    - GOOGLE_API_KEY 환경변수 미설정
    - chunk_overlap >= chunk_size 검증 실패
    - 필수 디렉토리 생성 실패

    retryable=False: 설정 자체가 잘못된 것이므로 재시도 의미 없음
    """

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(message, retryable=False, status_code=500, **kw)


# ──────────────────────────────────────────────────────────────────────
#  벡터 스토어 오류
# ──────────────────────────────────────────────────────────────────────

class VectorStoreError(GuidbotError):
    """
    FAISS 벡터 DB 관련 기본 예외.

    DBNotFoundError, DBBuildError 의 공통 부모입니다.
    두 예외를 동시에 처리할 때 이 클래스를 catch 하세요.
    """


class DBNotFoundError(VectorStoreError):
    """
    벡터 DB 파일(index.faiss)이 존재하지 않음.

    해결 방법: build_db.py 를 실행하여 DB 를 먼저 구축하세요.

    retryable=False: 파일이 없는 것은 재시도로 해결 불가
    status_code=503: Service Unavailable (DB 준비 안 됨)
    """

    def __init__(self, path: str) -> None:
        """
        Args:
            path: 찾을 수 없는 DB 파일 경로 (로그·메시지에 포함)
        """
        super().__init__(
            f"벡터 DB 를 찾을 수 없습니다: {path}\n"
            f"해결: python build_db.py 를 먼저 실행하세요.",
            retryable=False,
            status_code=503,
            context={"path": path},
        )


class DBBuildError(VectorStoreError):
    """
    벡터 DB 구축 실패.

    발생 예시:
    - 임베딩 모델 OOM (Out of Memory)
    - 디스크 공간 부족
    - FAISS 인덱스 파일 저장 실패

    retryable=True: 임시 리소스 부족이면 재시도 가능
    """

    def __init__(self, reason: str) -> None:
        """
        Args:
            reason: 구축 실패 원인 설명
        """
        super().__init__(
            f"벡터 DB 구축 실패: {reason}",
            retryable=True,
            status_code=500,
            context={"reason": reason},
        )


# ──────────────────────────────────────────────────────────────────────
#  임베딩 모델 오류
# ──────────────────────────────────────────────────────────────────────

class EmbeddingError(GuidbotError):
    """
    임베딩 모델 로드 또는 실행 실패.

    발생 예시:
    - HuggingFace 모델 다운로드 실패 (인터넷 단절)
    - 모델 캐시 파일 손상
    - CUDA/MPS 디바이스 초기화 실패

    retryable=False: 모델 자체 문제는 재시도로 해결 불가
    """

    def __init__(self, model: str, reason: str) -> None:
        """
        Args:
            model:  실패한 임베딩 모델 이름
            reason: 실패 원인 설명
        """
        super().__init__(
            f"임베딩 모델 오류 [{model}]: {reason}",
            retryable=False,
            status_code=503,
            context={"model": model, "reason": reason},
        )


# ──────────────────────────────────────────────────────────────────────
#  검색 오류
# ──────────────────────────────────────────────────────────────────────

class RetrievalError(GuidbotError):
    """
    문서 검색 실패.

    발생 예시:
    - 빈 쿼리 입력
    - FAISS 인덱스 파일 손상
    - Cross-Encoder 모델 실행 오류

    retryable=True: FAISS 임시 오류는 재시도 가능
    """

    def __init__(self, query: str, reason: str) -> None:
        """
        Args:
            query:  검색 실패한 질문 (짧게 로깅)
            reason: 실패 원인 설명
        """
        super().__init__(
            f"검색 실패 (질문: '{query[:50]}...' 생략): {reason}",
            retryable=True,
            status_code=500,
            context={"query": query, "reason": reason},
        )


# ──────────────────────────────────────────────────────────────────────
#  LLM (Gemini API) 오류
# ──────────────────────────────────────────────────────────────────────

class LLMError(GuidbotError):
    """
    Gemini LLM API 호출 실패.

    발생 예시:
    - 네트워크 오류 (일시적)
    - 잘못된 API 키
    - 모델 내부 오류 (500)

    retryable=True: 일시적 오류는 지수 백오프 후 재시도 가능
    status_code=502: Bad Gateway (LLM 서비스 응답 오류)
    """

    def __init__(self, reason: str) -> None:
        """
        Args:
            reason: 실패 원인 설명
        """
        super().__init__(
            f"LLM 응답 오류: {reason}",
            retryable=True,
            status_code=502,
            context={"reason": reason},
        )


class LLMQuotaError(LLMError):
    """
    Gemini API 할당량 초과 (HTTP 429).

    Google AI API 의 무료/유료 할당량을 초과했을 때 발생합니다.

    retryable=False: 할당량이 리셋될 때까지 재시도 의미 없음
                     (llm.py 에서 즉시 전파, 재시도 루프 없음)
    status_code=429: Too Many Requests
    """

    def __init__(self) -> None:
        # LLMError 의 __init__ 을 호출하여 reason 설정
        super().__init__("API 할당량이 초과되었습니다. 잠시 후 다시 시도하세요.")
        # 부모 클래스가 retryable=True 로 설정하므로 명시적으로 False 재설정
        self.retryable = False
        self.status_code = 429


# ──────────────────────────────────────────────────────────────────────
#  병원 DB 오류
# ──────────────────────────────────────────────────────────────────────

class DatabaseError(GuidbotError):
    """
    병원 내부 DB 관련 기본 예외.

    DBConnectionError, DBPermissionError 의 공통 부모입니다.
    DB 관련 예외를 통합 처리할 때 이 클래스를 catch 하세요.
    """


class DBConnectionError(DatabaseError):
    """
    DB 서버 연결 실패.

    발생 예시:
    - DB 서버 다운
    - 네트워크 방화벽 차단
    - 잘못된 호스트/포트

    retryable=True: 서버가 일시적으로 다운된 경우 재시도 가능
    status_code=503: Service Unavailable
    """

    def __init__(self, host: str, reason: str) -> None:
        """
        Args:
            host:   연결 시도한 호스트 (IP:포트)
            reason: 연결 실패 원인
        """
        super().__init__(
            f"DB 연결 실패 ({host}): {reason}",
            retryable=True,
            status_code=503,
            context={"host": host, "reason": reason},
        )


class DBPermissionError(DatabaseError):
    """
    DB 권한 부족.

    발생 예시:
    - SELECT 전용 계정(rag_readonly)으로 INSERT 시도
    - information_schema 조회 권한 없음

    retryable=False: 권한 문제는 관리자가 계정 권한을 수정해야 해결됨
    status_code=403: Forbidden
    """

    def __init__(self, user: str, operation: str) -> None:
        """
        Args:
            user:      DB 사용자명
            operation: 시도한 작업 (예: "SELECT", "INSERT", "DROP")
        """
        super().__init__(
            f"DB 권한 부족 (사용자: {user}, 작업: {operation})\n"
            f"해결: SELECT 전용 rag_readonly 계정을 사용하거나 DBA에게 권한 부여 요청",
            retryable=False,
            status_code=403,
            context={"user": user, "operation": operation},
        )


# ──────────────────────────────────────────────────────────────────────
#  문서 처리 오류
# ──────────────────────────────────────────────────────────────────────

class DocumentProcessError(GuidbotError):
    """
    PDF 문서 로드 또는 파싱 실패.

    발생 예시:
    - 암호화된 PDF (비밀번호 보호)
    - 손상된 PDF 파일
    - 지원하지 않는 PDF 버전

    retryable=False: 파일 자체 문제는 재시도로 해결 불가
    status_code=422: Unprocessable Entity (파일 처리 불가)
    """

    def __init__(self, filename: str, reason: str) -> None:
        """
        Args:
            filename: 처리 실패한 PDF 파일명
            reason:   실패 원인 설명
        """
        super().__init__(
            f"문서 처리 실패 [{filename}]: {reason}",
            retryable=False,
            status_code=422,
            context={"filename": filename, "reason": reason},
        )


# ──────────────────────────────────────────────────────────────────────
#  인증 오류
# ──────────────────────────────────────────────────────────────────────

class AuthenticationError(GuidbotError):
    """
    관리자 인증 실패.

    발생 예시:
    - 관리자 패스워드 불일치

    retryable=False: 잘못된 패스워드로 재시도는 의미 없음 (보안 위협)
    status_code=401: Unauthorized
    """

    def __init__(self) -> None:
        super().__init__(
            "인증에 실패했습니다. 패스워드를 확인하세요.",
            retryable=False,
            status_code=401,
        )
