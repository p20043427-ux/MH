"""
db/connector.py ─ 병원 DB 연결 풀 관리 (v2.0)

[설계 원칙]
- SQLAlchemy 연결 풀로 반복 연결 오버헤드 최소화 (pool_size=5)
- SELECT 전용 계정(rag_readonly) 사용 강력 권장 (보안)
- 컨텍스트 매니저(with 문)로 세션 자동 반환 보장 (리소스 누수 방지)
- db_enabled=False 시 즉시 None 반환 → 앱에 전혀 영향 없음

[보안 주의사항]
- DB 계정: 반드시 SELECT 권한만 부여된 읽기 전용 계정 사용
- 연결 URL(패스워드 포함)이 로그에 출력되지 않도록 _get_masked_url() 로 마스킹
- db_password 는 settings.SecretStr 로 관리 → repr/로그에서 자동 마스킹

[연결 풀 설정 근거]
- pool_size=5:       동시 5개 연결 상시 유지 (병원 동시 사용자 기준)
- max_overflow=10:   피크 시 최대 15개 연결 허용
- pool_pre_ping=True: 사용 전 연결 유효성 확인 (좀비 연결 방지)
- pool_recycle=3600:  1시간마다 연결 재생성 (MySQL 8시간 타임아웃 대응)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings
from utils.exceptions import DBConnectionError, DBPermissionError
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


class DatabaseConnector:
    """
    SQLAlchemy 기반 병원 DB 연결 관리자.

    [연결 풀이 필요한 이유]
    매 질문마다 DB 에 새로 연결하면 MySQL 핸드셰이크에 50~200ms 오버헤드 발생.
    연결 풀은 미리 연결을 유지하여 이 오버헤드를 제거합니다.

    [사용 예시]
        connector = get_db_connector()
        if connector:
            with connector.get_session() as session:
                rows = session.execute(text("SELECT 1")).fetchall()
    """

    def __init__(self) -> None:
        self._engine:          Optional[Engine]      = None
        self._session_factory: Optional[sessionmaker] = None

    def _get_masked_url(self) -> str:
        """
        로그용으로 패스워드가 마스킹된 DB 연결 URL 을 반환합니다.

        원본: mysql+pymysql://rag_readonly:mypass123@192.168.1.10:3306/hospital_db
        마스킹: mysql+pymysql://rag_readonly:***@192.168.1.10:3306/hospital_db
        """
        url = settings.db_url
        if "@" in url:
            # "://user:password@host" 형태에서 password 부분만 *** 로 대체
            # rsplit(":", 1): 마지막 ":" 기준 분리 → user 보존, password 제거
            parts = url.split("@")
            prefix = parts[0].rsplit(":", 1)[0]
            return f"{prefix}:***@{'@'.join(parts[1:])}"
        return url

    def connect(self) -> bool:
        """
        DB 에 연결하고 연결 풀을 초기화합니다.

        [db_enabled=False 처리]
        settings.db_enabled 가 False 이면 연결 시도 없이 즉시 False 반환.
        이 경우 앱의 다른 모든 기능(RAG 검색, LLM 답변)은 정상 동작합니다.

        Returns:
            True: 연결 성공 | False: DB 비활성화 또는 연결 실패

        Raises:
            DBConnectionError: 연결 실패 시 (retryable=True)
        """
        if not settings.db_enabled:
            logger.info("DB 연결 비활성화 (settings.db_enabled=False) → 건너뜀")
            return False

        try:
            self._engine = create_engine(
                settings.db_url,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,    # 사용 전 연결 유효성 확인 (좀비 연결 방지)
                pool_recycle=3600,     # 1시간마다 연결 재생성 (MySQL 8h 타임아웃 대응)
                echo=False,            # SQL 로그 비활성화 (보안: 쿼리 내용 노출 방지)
            )

            # 연결 테스트: 간단한 쿼리로 실제 연결 확인
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))

            self._session_factory = sessionmaker(bind=self._engine)
            logger.info(f"DB 연결 성공: {self._get_masked_url()}")
            return True

        except OperationalError as exc:
            # 연결 자체 실패 (서버 다운, 방화벽 차단 등)
            raise DBConnectionError(
                host=f"{settings.db_host}:{settings.db_port}",
                reason=str(exc),
            ) from exc

    def disconnect(self) -> None:
        """
        DB 연결 풀을 해제합니다.

        앱 종료 시 또는 설정 변경 시 호출하세요.
        dispose() 는 풀의 모든 연결을 닫고 엔진을 초기화합니다.
        """
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("DB 연결 해제 완료")

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """
        DB 세션 컨텍스트 매니저.

        with 블록 종료 시 자동으로 세션을 반환합니다.
        예외 발생 시 자동 롤백 후 세션 반환 (리소스 누수 없음).

        [왜 컨텍스트 매니저인가?]
        try/finally 패턴으로 항상 session.close() 를 보장합니다.
        연결 풀에 세션이 반환되어 다음 요청에서 재사용됩니다.

        Raises:
            DBConnectionError: connect() 가 먼저 호출되지 않은 경우

        Example::

            with connector.get_session() as session:
                result = session.execute(
                    text("SELECT * FROM patients WHERE id = :id"),
                    {"id": patient_id}
                ).mappings().all()
        """
        if self._session_factory is None:
            raise DBConnectionError(
                host=settings.db_host,
                reason=(
                    "DB 세션이 초기화되지 않았습니다. "
                    "get_db_connector() 를 통해 연결된 커넥터를 사용하세요."
                ),
            )

        session = self._session_factory()
        try:
            yield session
            session.commit()   # 정상 완료 시 커밋 (SELECT 만 있으면 사실상 no-op)
        except SQLAlchemyError as exc:
            session.rollback()  # 오류 시 롤백 (데이터 무결성 보장)
            logger.error(f"DB 세션 오류 (롤백 완료): {exc}")
            raise
        finally:
            session.close()    # 항상 세션 반환 (연결 풀에 돌려놓음)

    @property
    def is_connected(self) -> bool:
        """현재 DB 연결 상태 (True=연결됨, False=미연결)."""
        return self._engine is not None


# ──────────────────────────────────────────────────────────────────────
#  전역 싱글톤
# ──────────────────────────────────────────────────────────────────────

_connector_instance: Optional[DatabaseConnector] = None


def get_db_connector() -> Optional[DatabaseConnector]:
    """
    DatabaseConnector 싱글톤을 반환합니다.

    [동작]
    - db_enabled=False: None 반환 (DB 기능 전체 비활성화)
    - 최초 호출: 인스턴스 생성 후 자동 연결 시도
    - 이후 호출: 기존 인스턴스 반환 (재연결 없음)
    - 연결 실패: None 반환 (앱은 계속 동작, DB 기능만 비활성화)

    Returns:
        DatabaseConnector 인스턴스 또는 None (DB 비활성화/연결 실패 시)

    Example::

        connector = get_db_connector()
        if connector is None:
            # DB 없이 동작 (PDF RAG 만 사용)
            pass
        else:
            with connector.get_session() as session:
                ...
    """
    global _connector_instance

    if not settings.db_enabled:
        return None

    if _connector_instance is None:
        _connector_instance = DatabaseConnector()
        try:
            _connector_instance.connect()
        except DBConnectionError as exc:
            logger.error(f"DB 초기 연결 실패 → DB 기능 비활성화: {exc.message}")
            _connector_instance = None

    return _connector_instance
