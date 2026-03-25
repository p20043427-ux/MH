"""
utils/logger.py ─ 구조화 로거 모듈 (v3.0)

[v3.0 개선사항]
1. Request ID 지원
   - ContextLogger 에 request_id 를 자동으로 포함시킬 수 있습니다.
   - 동시 다중 사용자 환경(Streamlit)에서 로그 추적이 쉬워집니다.
   예: [REQ-a1b2] RAG 검색 시작 → [REQ-a1b2] 검색 완료 → [REQ-a1b2] 답변 생성

2. 성능 타이밍 로그 헬퍼
   - PerfTimer: with 문으로 코드 블록 실행 시간을 자동 로깅합니다.
   예:
       with PerfTimer(logger, "FAISS 검색"):
           result = vector_db.similarity_search(...)
       # → [PERF] FAISS 검색: 123ms

3. L-03 버그 수정: TimedRotatingFileHandler delay 옵션
   - delay=True 로 변경: 실제 첫 로그 발생 시에만 파일을 생성합니다.
   - 이전 delay=False: 앱 기동 시 즉시 파일 생성 시도 → 디렉토리 없으면 오류
   - settings.py _create_directories() 가 먼저 실행되도록 import 순서 보장

[설계 원칙]
- print() 완전 제거 → logging 모듈로 100% 표준화
- 콘솔: INFO 이상 (운영 가시성 확보)
- 파일:  DEBUG 이상 전체 트레이스 (유지보수·디버깅용)
- 일별 자동 롤오버(TimedRotatingFileHandler) + 30일 보관
- 핸들러 독립 관리: 콘솔/파일 상태를 분리하여 중복 등록 방지
- ContextLogger: request_id, user_id 등 컨텍스트를 메시지 앞에 자동 주입

[사용 예시]
    # 기본 로거
    logger = get_logger(__name__, log_dir=settings.log_dir)
    logger.info("벡터 DB 로드 완료")

    # 요청별 컨텍스트 로거
    from utils.logger import get_logger, ContextLogger
    base = get_logger(__name__, log_dir=settings.log_dir)
    req_logger = ContextLogger(base, request_id="REQ-abc123")
    req_logger.info("검색 시작")  # 출력: [REQ-abc123] 검색 시작

    # 성능 타이밍
    from utils.logger import PerfTimer
    with PerfTimer(logger, "Cross-Encoder 리랭킹"):
        scores = cross_encoder.predict(pairs)
    # 출력: [PERF] Cross-Encoder 리랭킹: 245ms
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Generator, Set

# ── 로그 포맷 정의 ─────────────────────────────────────────────────────
# 콘솔 포맷: 시간 + 레벨 + 모듈명 + 메시지 (간결)
_CONSOLE_FMT = "[%(asctime)s] %(levelname)-8s | %(name)s | %(message)s"

# 파일 포맷: 함수명과 줄번호 추가 (디버깅 시 코드 위치 즉시 파악)
_FILE_FMT = (
    "[%(asctime)s] %(levelname)-8s | %(name)s | "
    "%(funcName)s:%(lineno)d | %(message)s"
)

# 날짜 포맷: 24시간 기준 (병원 업무 특성상 야간 로그도 명확히 구분)
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# ── 핸들러 등록 상태 추적 (L-03 수정 포함) ────────────────────────────
# 콘솔 핸들러: 이름 → 등록 여부 (중복 방지)
_configured_console: Set[str] = set()

# 파일 핸들러: 이름 → 등록된 log_dir (동일 경로 중복 방지 + 경로 변경 감지)
_configured_file: Dict[str, Path] = {}


def get_logger(name: str, log_dir: Path | None = None) -> logging.Logger:
    """
    표준화된 로거를 반환합니다.

    콘솔 핸들러와 파일 핸들러를 독립적으로 관리합니다.
    log_dir 없이 먼저 호출된 로거도 나중에 log_dir 를 넣어 재호출하면
    파일 핸들러가 추가됩니다 (이전 버전의 버그 수정).

    [L-03 수정] delay=True 로 변경
    - 파일은 실제 첫 로그 발생 시에만 생성됩니다.
    - settings._create_directories() 가 log_dir 를 먼저 만들어두므로 정상 동작.
    - delay=False 는 import 시 즉시 파일 생성 시도 → 디렉토리 미존재 시 오류.

    Args:
        name    : 로거 이름 (__name__ 전달 권장, 예: "core.retriever")
        log_dir : 파일 저장 경로. None 이면 콘솔만 출력.

    Returns:
        설정 완료된 logging.Logger 인스턴스

    Example::

        # 기본 사용법
        logger = get_logger(__name__, log_dir=settings.log_dir)
        logger.info("초기화 완료")

        # 파일 없이 콘솔만
        logger = get_logger(__name__)
        logger.warning("임시 경고")
    """
    # ── Python logging 레지스트리에서 기존 로거 객체 가져오기 ──────────
    # logging.getLogger() 는 동일 name 에 대해 항상 같은 객체를 반환합니다.
    # Streamlit 핫리로드 시에도 이 객체는 유지됩니다.
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)    # 핸들러별 레벨로 실제 출력 제어
    logger.propagate = False          # 루트 로거로의 전파 차단 (중복 출력 방지)

    # ── 콘솔 핸들러 관리: 정확히 1개만 유지 ────────────────────────────
    #
    # [중복 발생 원인]
    # Streamlit 은 매 리런마다 스크립트를 처음부터 재실행합니다.
    # 각 모듈의 모듈 레벨 코드(logger = get_logger(...))도 재실행됩니다.
    # Python logging 레지스트리의 Logger 객체는 프로세스 종료 전까지 유지되므로
    # 핸들러가 계속 누적됩니다.
    #   예) Streamlit 리런 3회 → 콘솔 핸들러 3개 → 로그 3중복
    #
    # [해결 전략]
    # 단순히 '없으면 추가'가 아니라, '초과분을 먼저 제거' 후 '없으면 추가'
    # → 이미 누적된 핸들러도 정리하고, 정확히 1개를 보장합니다.
    console_handlers = [
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, TimedRotatingFileHandler)
    ]

    # 2개 이상이면 첫 번째만 남기고 나머지 제거 (누적분 정리)
    for h in console_handlers[1:]:
        logger.removeHandler(h)
        h.close()

    # 하나도 없으면 새로 추가
    if not console_handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(_CONSOLE_FMT, _DATE_FMT))
        logger.addHandler(console_handler)

    # ── 파일 핸들러 관리: log_dir 가 제공된 경우에만 처리 ───────────────
    if log_dir is not None:
        log_dir = Path(log_dir)

        # ── 파일 핸들러: 경로 기반으로 1개만 유지 ─────────────────────
        # 이미 동일 경로의 파일 핸들러가 있으면 추가하지 않습니다.
        # 다른 경로가 요청되면 기존 핸들러를 닫고 새로 추가합니다.
        # (운영 중 log_dir 를 동적으로 바꾸는 케이스 대응)
        existing_file_handlers = [
            h for h in logger.handlers
            if isinstance(h, TimedRotatingFileHandler)
        ]

        # 파일명 계산 (예: "core.rag_pipeline" → "rag_pipeline.log")
        module_short = name.split(".")[-1].replace("__", "") or "app"
        new_log_file = Path(log_dir) / f"{module_short}.log"

        if existing_file_handlers:
            existing_path = Path(existing_file_handlers[0].baseFilename)
            if existing_path == new_log_file:
                # 동일 파일 → 아무것도 하지 않음
                return logger
            # 다른 경로 → 기존 핸들러 제거 후 새로 추가
            for h in existing_file_handlers:
                logger.removeHandler(h)
                h.close()

        # log_dir 생성 (설정 단계에서 이미 만들어졌지만 안전 장치로 재확인)
        log_dir.mkdir(parents=True, exist_ok=True)

        file_handler = TimedRotatingFileHandler(
            filename    = str(new_log_file),
            when        = "midnight",   # 자정에 새 파일로 롤오버
            backupCount = 30,           # 최근 30일치 보관 후 자동 삭제
            encoding    = "utf-8",
            delay       = True,         # 실제 첫 로그 기록 시에만 파일 생성
                                        # (import 시 빈 파일이 생기는 현상 방지)
        )
        file_handler.setLevel(logging.DEBUG)   # 파일에는 DEBUG 포함 전체 기록
        file_handler.setFormatter(logging.Formatter(_FILE_FMT, _DATE_FMT))
        logger.addHandler(file_handler)

    return logger


def configure_all_loggers(log_dir: Path) -> None:
    """
    앱 기동 시 기존 등록된 모든 가이드봇 로거에 파일 핸들러를 일괄 추가합니다.

    문제 상황:
        utils/file_sync.py 등에서 get_logger(__name__) 를 log_dir 없이 먼저
        호출하는 경우, 나중에 settings.log_dir 를 알아도 파일 핸들러가
        없는 상태로 남아 있을 수 있습니다.

    해결책:
        main.py / build_db.py 최상단에서 settings 로드 직후 이 함수를 호출하면
        이미 등록된 모든 guidbot 로거에 파일 핸들러가 추가됩니다.

    Args:
        log_dir: 파일 핸들러를 추가할 로그 디렉토리

    Example::

        # main.py 상단 (settings import 직후)
        from utils.logger import configure_all_loggers
        from config.settings import settings
        configure_all_loggers(settings.log_dir)
    """
    log_dir = Path(log_dir)
    # guidbot 프로젝트 관련 모듈만 필터링 (시스템 로거 제외)
    guidbot_prefixes = ("core.", "db.", "ui.", "utils.", "config.", "__main__")

    for logger_name in list(logging.Logger.manager.loggerDict.keys()):
        if any(logger_name.startswith(prefix) for prefix in guidbot_prefixes):
            get_logger(logger_name, log_dir=log_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ContextLogger: 요청 ID 등 컨텍스트 자동 주입
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ContextLogger:
    """
    요청 ID, 사용자 ID 등 컨텍스트를 모든 로그 메시지 앞에 자동으로 추가하는
    로거 래퍼입니다.

    [주요 용도]
    Streamlit 은 여러 사용자가 동시에 요청을 처리할 수 있습니다.
    요청별로 고유 ID를 붙여두면 로그에서 어떤 사용자의 어떤 요청인지
    추적할 수 있어 디버깅이 훨씬 쉬워집니다.

    [동작 예시]
        base = get_logger(__name__)
        log = ContextLogger(base, request_id="REQ-a1b2")
        log.info("검색 시작")
        # 출력: ... | [REQ-a1b2] 검색 시작

        # 추가 컨텍스트 바인딩 (새 인스턴스 반환)
        log2 = log.bind(user_ip="192.168.1.1")
        log2.info("답변 생성")
        # 출력: ... | [REQ-a1b2] [192.168.1.1] 답변 생성

    Attributes:
        _logger  : 내부 표준 Logger 인스턴스
        _context : 컨텍스트 딕셔너리 (키=값 쌍)
        _prefix  : 메시지 앞에 붙을 문자열 ("[REQ-a1b2]" 형태)
    """

    def __init__(self, logger: logging.Logger, **context: Any) -> None:
        """
        Args:
            logger  : 기반 Logger 인스턴스
            **context: 키=값 형태의 컨텍스트 (예: request_id="REQ-a1b2")
        """
        self._logger = logger
        self._context = context
        # 컨텍스트 값들을 "[값]" 형태로 연결하여 prefix 생성
        self._prefix = " ".join(f"[{v}]" for v in context.values())

    def _fmt(self, msg: str) -> str:
        """메시지 앞에 컨텍스트 prefix 를 붙인 문자열을 반환합니다."""
        return f"{self._prefix} {msg}" if self._prefix else msg

    # 표준 logging.Logger 와 동일한 인터페이스 제공
    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """DEBUG 레벨 로그 (파일에만 기록)"""
        self._logger.debug(self._fmt(msg), *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """INFO 레벨 로그 (콘솔 + 파일)"""
        self._logger.info(self._fmt(msg), *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """WARNING 레벨 로그 (콘솔 + 파일)"""
        self._logger.warning(self._fmt(msg), *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """ERROR 레벨 로그 (콘솔 + 파일)"""
        self._logger.error(self._fmt(msg), *args, **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """ERROR + 스택 트레이스 로그 (예외 처리 블록에서 사용)"""
        self._logger.exception(self._fmt(msg), *args, **kwargs)

    def bind(self, **extra: Any) -> "ContextLogger":
        """
        현재 컨텍스트에 추가 컨텍스트를 합쳐 새 ContextLogger 를 반환합니다.

        원본 인스턴스는 변경되지 않습니다 (불변성 보장).

        Args:
            **extra: 추가할 컨텍스트 (기존 키와 충돌 시 extra 값으로 덮어씀)

        Returns:
            새 ContextLogger 인스턴스
        """
        merged = {**self._context, **extra}
        return ContextLogger(self._logger, **merged)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PerfTimer: 코드 블록 실행 시간 자동 로깅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PerfTimer:
    """
    코드 블록의 실행 시간을 자동으로 측정하여 로그에 기록하는 컨텍스트 매니저입니다.

    [사용 방법]
        with PerfTimer(logger, "FAISS 벡터 검색"):
            candidates = vector_db.similarity_search(query, k=10)
        # 완료 시 자동 로그: "[PERF] FAISS 벡터 검색: 87ms"

    [elapsed_ms 접근]
        with PerfTimer(logger, "임베딩 로드") as t:
            embeddings = get_embeddings_auto()
        print(t.elapsed_ms)  # → 3245

    Attributes:
        _logger    : 로그를 기록할 Logger 인스턴스
        _label     : 측정 대상을 설명하는 레이블
        _threshold : 이 시간(ms) 이상일 때만 WARNING 으로 기록 (기본: 로그 없음)
        elapsed_ms : 실행 완료 후 소요 시간 (밀리초 정수)
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        warn_threshold_ms: int = 0,  # 0이면 임계값 없음 (항상 INFO)
    ) -> None:
        """
        Args:
            logger          : 로그를 기록할 Logger
            label           : 측정 대상 레이블 (로그 메시지에 포함)
            warn_threshold_ms: 이 시간(ms) 초과 시 WARNING 로그 (기본: 비활성)
        """
        self._logger = logger
        self._label = label
        self._warn_threshold_ms = warn_threshold_ms
        self._start: float = 0.0   # __enter__ 에서 설정
        self.elapsed_ms: int = 0   # __exit__ 에서 설정 (외부 접근 가능)

    def __enter__(self) -> "PerfTimer":
        """코드 블록 진입 시 시작 시각을 기록합니다."""
        self._start = time.perf_counter()
        return self  # with ... as t 패턴에서 t 로 접근

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """
        코드 블록 종료 시 소요 시간을 계산하고 로그에 기록합니다.

        예외 발생 시에도 실행됩니다 (finally 블록과 유사).

        Returns:
            False: 예외를 다시 발생시킴 (PerfTimer 가 예외를 삼키지 않음)
        """
        elapsed_sec = time.perf_counter() - self._start
        self.elapsed_ms = int(elapsed_sec * 1000)  # 초 → 밀리초 변환

        if self._warn_threshold_ms > 0 and self.elapsed_ms > self._warn_threshold_ms:
            # 임계값 초과: WARNING 레벨로 기록 (느린 작업 감지)
            self._logger.warning(
                f"[PERF ⚠️] {self._label}: {self.elapsed_ms}ms "
                f"(임계값 {self._warn_threshold_ms}ms 초과)"
            )
        else:
            # 정상 범위: DEBUG 레벨 (파일에만 기록, 콘솔에는 표시 안 됨)
            self._logger.debug(f"[PERF] {self._label}: {self.elapsed_ms}ms")

        # 예외를 다시 발생시켜 정상적인 오류 전파 유지
        return False


@contextmanager
def perf_log(
    logger: logging.Logger,
    label: str,
    warn_threshold_ms: int = 0,
) -> Generator[None, None, None]:
    """
    PerfTimer 의 함수형 대안 (제너레이터 기반).

    elapsed_ms 에 접근할 필요 없을 때 더 간결하게 사용할 수 있습니다.

    [사용 방법]
        with perf_log(logger, "임베딩 계산"):
            result = model.encode(texts)
        # → [PERF] 임베딩 계산: 156ms (파일 로그)

    Args:
        logger          : Logger 인스턴스
        label           : 측정 레이블
        warn_threshold_ms: WARNING 임계값 (0이면 비활성)
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if warn_threshold_ms > 0 and elapsed_ms > warn_threshold_ms:
            logger.warning(
                f"[PERF ⚠️] {label}: {elapsed_ms}ms "
                f"(임계값 {warn_threshold_ms}ms 초과)"
            )
        else:
            logger.debug(f"[PERF] {label}: {elapsed_ms}ms")