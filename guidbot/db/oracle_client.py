"""
db/oracle_client.py  ─  병원 Oracle DB 연결 풀 관리 (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[설계 원칙]
  · python-oracledb (thin mode) 사용 — Oracle Instant Client 설치 불필요
  · ConnectionPool 로 반복 연결 오버헤드 최소화 (pool_min=2, pool_max=10)
  · SELECT 전용 rag_readonly 계정 강력 권장 (보안)
  · 컨텍스트 매니저(with 문)로 커넥션 자동 반환 (리소스 누수 방지)
  · db_enabled=False 시 즉시 None 반환 → 앱에 전혀 영향 없음

[Oracle Thin Mode 란?]
  기존 cx_Oracle 은 Oracle Instant Client 를 별도 설치해야 했습니다.
  python-oracledb v1.0 부터 Thin Mode 가 기본값이며,
  순수 Python 으로 Oracle 프로토콜을 구현하므로 별도 설치 불필요합니다.
  Thick Mode (cx_Oracle 호환)는 oracledb.init_oracle_client() 호출로 활성화.

[연결 풀 설정 근거]
  pool_min=2:      항상 2개 커넥션 유지 → 첫 요청 지연 없음
  pool_max=10:     최대 10개 동시 연결 (병원 동시 사용자 기준)
  pool_increment=1: 부하 증가 시 1개씩 점진적으로 확장
  ping_interval=60: 60초마다 커넥션 유효성 확인 (좀비 연결 방지)

[보안 주의사항]
  · DB 계정: 반드시 SELECT 권한만 부여된 읽기 전용 계정 사용
  · 연결 URL(패스워드 포함)이 로그에 출력되지 않도록 마스킹 처리
  · oracle_password 는 settings.SecretStr 로 관리 → repr/로그 자동 마스킹
  · 화이트리스트 테이블 외 접근 차단 (sql_generator.py 에서 적용)

[DSN 형식 예시]
  · Easy Connect: host:port/service_name
    예) 192.168.1.10:1521/ORCL
  · TNS Alias:    tnsnames.ora 의 별칭
    예) HOSPITAL_DB
  · Oracle Cloud: 접속 정보 .zip 파일의 tnsnames.ora 참조

[.env 설정 예시]
  ORACLE_HOST=192.168.1.10
  ORACLE_PORT=1521
  ORACLE_SERVICE_NAME=ORCL
  ORACLE_USER=rag_readonly
  ORACLE_PASSWORD=your_secure_password
  ORACLE_ENABLED=true
"""

from __future__ import annotations

import contextlib
import textwrap
from typing import Generator, Iterator, List, Optional, Any, Dict

from config.settings import settings
from utils.logger import get_logger

# 로거 초기화
# __name__ = "db.oracle_client" → 로그 파일에서 출처 식별 가능
logger = get_logger(__name__, log_dir=settings.log_dir)


# ──────────────────────────────────────────────────────────────────────
#  전역 커넥션 풀 (모듈 레벨 싱글톤)
#
#  [싱글톤으로 관리하는 이유]
#  Streamlit 은 매 사용자 인터랙션마다 스크립트를 재실행합니다.
#  get_oracle_pool() 을 @st.cache_resource 로 캐시하거나
#  모듈 레벨 변수로 관리하면 풀을 한 번만 생성할 수 있습니다.
#  여기서는 모듈 레벨 변수 + get_oracle_pool() 팩토리를 사용합니다.
# ──────────────────────────────────────────────────────────────────────

_pool: Optional[Any] = None  # oracledb.ConnectionPool 인스턴스 (지연 초기화)


def _build_dsn() -> str:
    """
    settings 에서 Oracle DSN(Data Source Name) 문자열을 생성합니다.

    [DSN 우선순위]
    1. settings.oracle_dsn 이 직접 설정된 경우 → 그대로 사용
    2. host + port + service_name 으로 Easy Connect 형식 생성

    Easy Connect 형식: host:port/service_name
    예) 192.168.1.10:1521/ORCL

    Returns:
        DSN 문자열

    Raises:
        ValueError: 필수 설정값이 없을 때
    """
    # oracle_dsn 이 직접 지정된 경우 (TNS Alias 등)
    direct_dsn: Optional[str] = getattr(settings, "oracle_dsn", None)
    if direct_dsn:
        return direct_dsn

    # Easy Connect: host:port/service_name 조합
    host: str = getattr(settings, "oracle_host", "")
    port: int = getattr(settings, "oracle_port", 1521)
    svc: str = getattr(settings, "oracle_service_name", "")

    if not host or not svc:
        raise ValueError(
            "Oracle DSN 설정이 없습니다. "
            ".env 에 ORACLE_HOST, ORACLE_SERVICE_NAME 을 설정하거나 "
            "ORACLE_DSN 을 직접 지정하세요."
        )

    return f"{host}:{port}/{svc}"


def get_oracle_pool() -> Optional[Any]:
    """
    Oracle 커넥션 풀 싱글톤을 반환합니다.

    [지연 초기화 (Lazy Initialization)]
    앱 시작 시 즉시 풀을 만들지 않고, 처음 요청이 들어올 때 생성합니다.
    이유: Oracle 서버가 없거나 비활성화된 환경에서도 앱이 정상 기동됩니다.

    [Thread Safety]
    Streamlit 은 단일 스레드(이벤트 루프)이므로 별도 Lock 불필요.
    만약 멀티스레드 환경으로 전환하면 threading.Lock 추가 필요.

    Returns:
        oracledb.ConnectionPool. Oracle 비활성화 또는 초기화 실패 시 None.
    """
    global _pool

    # Oracle 기능이 비활성화된 경우 바로 None 반환
    oracle_enabled: bool = getattr(settings, "oracle_enabled", False)
    if not oracle_enabled:
        return None

    # 이미 풀이 초기화되어 있으면 재사용
    if _pool is not None:
        return _pool

    try:
        import oracledb  # python-oracledb (pip install oracledb)

        # ── Thick Mode 처리 (Oracle 10g/11g 구버전 지원) ──────────────
        # Thin Mode(기본): Oracle 12.1 이상만 지원
        # Thick Mode: Oracle Instant Client 설치 + init_oracle_client() 호출 필요
        _thick_mode = getattr(settings, "oracle_thick_mode", False)
        _lib_dir = getattr(settings, "oracle_client_lib_dir", "").strip()

        if _thick_mode:
            if _lib_dir:
                oracledb.init_oracle_client(lib_dir=_lib_dir)
                logger.info(f"Oracle Thick Mode 활성화 (lib_dir={_lib_dir})")
            else:
                # lib_dir 미설정 시 PATH/LD_LIBRARY_PATH 에서 자동 탐색
                oracledb.init_oracle_client()
                logger.info("Oracle Thick Mode 활성화 (lib_dir=자동 탐색)")
        else:
            logger.debug("Oracle Thin Mode 사용 (12.1+ 전용)")

        dsn = _build_dsn()
        user = getattr(settings, "oracle_user", "")
        # SecretStr 의 경우 .get_secret_value() 로 실제 값 추출
        password = (
            settings.oracle_password.get_secret_value()
            if hasattr(settings.oracle_password, "get_secret_value")
            else str(getattr(settings, "oracle_password", ""))
        )

        # 마스킹된 DSN 정보를 로그에 기록 (패스워드 제외)
        safe_dsn = f"{user}:***@{dsn}"
        logger.info(f"Oracle 커넥션 풀 초기화 중: {safe_dsn}")

        _pool = oracledb.create_pool(
            user=user,
            password=password,
            dsn=dsn,
            # pool_min: 항상 유지할 최소 커넥션 수
            # → 첫 요청에 즉시 커넥션 제공 (대기 없음)
            min=int(getattr(settings, "oracle_pool_min", 2)),
            # pool_max: 최대 동시 커넥션 수
            # → 초과 요청은 pool_timeout 만큼 대기 후 오류
            max=int(getattr(settings, "oracle_pool_max", 10)),
            # pool_increment: 풀 부족 시 한 번에 늘릴 커넥션 수
            # 1로 설정하면 점진적 확장 (메모리 효율)
            increment=1,
            # ping_interval: 커넥션 사용 전 유효성 확인 주기 (초)
            # Oracle 서버의 idle timeout 보다 짧게 설정 권장
            ping_interval=60,
            # session_callback: 커넥션 체크아웃 시 자동으로 세션 설정 적용
            # NLS_DATE_FORMAT 등 세션 레벨 설정에 활용 가능
        )

        logger.info(
            f"Oracle 커넥션 풀 초기화 완료 "
            f"(min={_pool.min}, max={_pool.max}, dsn={safe_dsn})"
        )
        return _pool

    except ImportError:
        logger.warning(
            "oracledb 패키지가 설치되지 않았습니다. pip install oracledb 로 설치하세요."
        )
        return None

    except Exception as exc:
        # 연결 실패 시 앱 전체가 죽지 않도록 경고만 출력
        logger.error(f"Oracle 커넥션 풀 초기화 실패: {exc}", exc_info=True)
        return None


@contextlib.contextmanager
def get_oracle_connection() -> Generator[Optional[Any], None, None]:
    """
    Oracle 커넥션을 컨텍스트 매니저로 제공합니다.

    [사용 방법]
    with get_oracle_connection() as conn:
        if conn is None:
            # Oracle 비활성화 또는 연결 실패
            return
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM TABLE")

    [자동 반환 보장]
    with 블록이 끝나면 finally 에서 반드시 커넥션을 풀에 반환합니다.
    예외가 발생해도 반환되므로 리소스 누수가 없습니다.

    Yields:
        oracledb.Connection 또는 None (Oracle 비활성화/연결 실패)
    """
    pool = get_oracle_pool()
    if pool is None:
        yield None
        return

    conn = None
    try:
        # 풀에서 커넥션 획득
        conn = pool.acquire()
        logger.debug("Oracle 커넥션 획득")
        yield conn  # ← 정상 경로: 1회만 yield

    except Exception as exc:
        # [핵심 수정]
        # except 블록에서 yield 하면 @contextmanager 제너레이터가
        # "두 번 yield" 또는 예외 전파 시 finally 미실행 → RuntimeError
        # → yield None 제거하고 예외 로깅만 수행, finally 에서 정리
        logger.error(f"Oracle 커넥션 오류: {exc}", exc_info=True)
        # 예외를 다시 던져 호출부에서 명확한 에러 처리 가능하게 함
        raise

    finally:
        # 커넥션을 풀에 반환 (예외가 발생해도 반드시 실행)
        if conn is not None:
            try:
                pool.release(conn)
                logger.debug("Oracle 커넥션 풀 반환")
            except Exception as fe:
                logger.warning(f"Oracle 커넥션 반환 실패: {fe}")


# ── 감사 로그 파일 경로 ─────────────────────────────────────────────
# 누가 언제 어떤 SQL을 실행했는지 별도 파일에 기록
# 병원 내부 감사 요구사항 충족
_AUDIT_LOGGER = None


def _get_audit_logger():
    """쿼리 감사 전용 로거 (쿼리 실행 이력 파일)"""
    global _AUDIT_LOGGER
    if _AUDIT_LOGGER is None:
        import logging
        from pathlib import Path

        audit_log_dir = (
            Path(settings.log_dir) if hasattr(settings, "log_dir") else Path("logs")
        )
        audit_log_dir.mkdir(parents=True, exist_ok=True)
        h = logging.FileHandler(
            audit_log_dir / "query_audit.log",
            encoding="utf-8",
        )
        h.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        _AUDIT_LOGGER = logging.getLogger("query_audit")
        _AUDIT_LOGGER.setLevel(logging.INFO)
        if not _AUDIT_LOGGER.handlers:
            _AUDIT_LOGGER.addHandler(h)
        _AUDIT_LOGGER.propagate = False
    return _AUDIT_LOGGER


def execute_query(
    sql: str,
    params: Optional[Dict[str, Any]] = None,
    max_rows: int = 5000,
    timeout_sec: int = 30,
    user_context: str = "unknown",
) -> Optional[List[Dict[str, Any]]]:
    """
    Oracle SQL 을 실행하고 결과를 딕셔너리 리스트로 반환합니다.

    [보안 강화 — v2.0]
    · 감사 로그: query_audit.log 에 실행자/시간/SQL/결과행수 기록
    · 타임아웃:  timeout_sec 초 초과 시 강제 중단 (기본 30초)
    · 빈 SQL 차단: 실행 전 검증
    · 예외 전파:  호출부에서 정확한 ORA 코드 표시

    [max_rows 설계]
    병원 데이터는 수백만 건이 될 수 있습니다.
    fetchmany(max_rows) 로 메모리 효율화.

    Args:
        sql:          실행할 SELECT 쿼리
        params:       바인드 변수 딕셔너리
        max_rows:     최대 반환 행 수 (기본 5000)
        timeout_sec:  쿼리 타임아웃 초 (기본 30)
        user_context: 감사 로그용 사용자 식별자

    Returns:
        딕셔너리 리스트 또는 None
    """
    import time as _time

    if not sql or not sql.strip():
        logger.warning("빈 SQL 수신 → 실행 건너뜀")
        return None

    # SQL 단일 라인 요약 (로그용)
    _sql_summary = " ".join(sql.split())[:200]
    _t_start = _time.time()

    with get_oracle_connection() as conn:
        if conn is None:
            return None

        try:
            cursor = conn.cursor()

            # ── 쿼리 타임아웃 설정 (Oracle Client 18.1+ 전용) ──────
            # callTimeout 은 Oracle Client 18.1+ 에서만 지원합니다.
            # Oracle 11.2 (DPI-1050) 또는 Thin 모드에서는 예외 발생
            # → AttributeError + DatabaseError + Exception 모두 무시
            # → 타임아웃 없이 실행 (Oracle DB 서버 세션 타임아웃에 위임)
            try:
                conn.callTimeout = timeout_sec * 1000
            except Exception:
                pass  # Oracle 11.2/Thin 모드: callTimeout 미지원 → 무시

            cursor.execute(sql, params or {})

            columns: List[str] = [col[0] for col in cursor.description or []]
            if not columns:
                logger.warning("쿼리 결과에 컬럼 정보가 없습니다.")
                return None

            rows = cursor.fetchmany(max_rows)
            _elapsed = (_time.time() - _t_start) * 1000

            # ── 감사 로그 기록 ────────────────────────────────────
            # logs/query_audit.log 에 저장 (일별 롤링 권장)
            _get_audit_logger().info(
                f"USER={user_context} | "
                f"ROWS={len(rows)} | "
                f"TIME={_elapsed:.0f}ms | "
                f"SQL={_sql_summary}"
            )
            logger.info(
                f"Oracle 쿼리 완료: {len(rows)}행 | {_elapsed:.0f}ms (max={max_rows}행)"
            )

            # ── CLOB / LOB 즉시 읽기 (커넥션 닫히기 전) ──────────────
            # [수정 이유 — v2.1]
            # Oracle CLOB 은 커넥션이 열려있는 동안만 읽을 수 있는 lazy 객체.
            # with 블록 밖(커넥션 반환 후)에서 str(clob) 호출 시 DPY-1001 발생.
            # → 커넥션이 살아있는 이 시점에 .read() 로 즉시 문자열 변환.
            # 대상: oracledb.LOB 인스턴스 (CLOB, NCLOB, BLOB 등)
            def _read_cell(v: Any) -> Any:
                """LOB 객체면 즉시 읽어 str 반환, 그 외는 그대로."""
                try:
                    import oracledb

                    if isinstance(v, oracledb.LOB):
                        return v.read()
                except Exception:
                    pass
                return v

            return [
                {col: _read_cell(val) for col, val in zip(columns, row)} for row in rows
            ]

        except Exception as exc:
            _elapsed = (_time.time() - _t_start) * 1000
            logger.error(
                f"Oracle 쿼리 실패: {exc} | {_elapsed:.0f}ms\nSQL: {_sql_summary}",
                exc_info=True,
            )
            # 감사 로그에 실패도 기록
            try:
                _get_audit_logger().warning(
                    f"USER={user_context} | "
                    f"ROWS=ERROR | "
                    f"TIME={_elapsed:.0f}ms | "
                    f"ERR={str(exc)[:100]} | "
                    f"SQL={_sql_summary}"
                )
            except Exception:
                pass
            raise


def test_connection() -> tuple[bool, str]:
    """
    Oracle 연결 상태를 테스트합니다.

    사이드바의 시스템 상태 표시에서 호출됩니다.

    Returns:
        (is_connected, message):
          (True,  "Oracle 연결 정상") 또는
          (False, "오류 메시지")
    """
    oracle_enabled: bool = getattr(settings, "oracle_enabled", False)
    if not oracle_enabled:
        return False, "Oracle 비활성화 (ORACLE_ENABLED=false)"

    with get_oracle_connection() as conn:
        if conn is None:
            return False, "Oracle 연결 실패 (설정 확인 필요)"

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM DUAL")  # Oracle 연결 확인용 더미 쿼리
            return True, "Oracle 연결 정상"
        except Exception as exc:
            return False, f"Oracle 쿼리 실패: {exc}"


def get_table_list() -> List[str]:
    """
    현재 계정이 접근 가능한 테이블 목록을 반환합니다.

    [ALL_TABLES vs USER_TABLES]
    · USER_TABLES: 현재 계정 소유 테이블만 (rag_readonly 계정의 경우 거의 없음)
    · ALL_TABLES:  SELECT 권한이 부여된 모든 테이블 (권장)

    Returns:
        테이블명 리스트. 연결 실패 시 빈 리스트.
    """
    sql = """
        SELECT TABLE_NAME
        FROM   ALL_TABLES
        WHERE  OWNER = :owner
        ORDER  BY TABLE_NAME
    """
    owner = getattr(
        settings, "oracle_schema", getattr(settings, "oracle_user", "")
    ).upper()
    rows = execute_query(sql, {"owner": owner}, max_rows=1000)

    if not rows:
        return []

    return [row["TABLE_NAME"] for row in rows]


def close_pool() -> None:
    """
    Oracle 연결 풀을 닫고 전역 변수를 초기화합니다.

    [언제 사용하는가?]
    - 사이드바 '재연결' 버튼 클릭 시
    - Oracle 서버 재시작 후 연결 재초기화 필요 시
    - 설정 변경(HOST/PORT 등) 후 새 설정으로 재연결 시

    [동작]
    1. 기존 풀의 모든 커넥션 종료 (close() 호출)
    2. 전역 _pool 변수 None 으로 초기화
    3. 다음 get_oracle_pool() 호출 시 새 풀 자동 생성
    """
    global _pool
    if _pool is not None:
        try:
            _pool.close()
            logger.info("Oracle 연결 풀 정상 종료")
        except Exception as exc:
            logger.warning(f"Oracle 연결 풀 종료 중 오류 (무시): {exc}")
        finally:
            _pool = None
    else:
        logger.debug("close_pool: 풀이 없음 (이미 닫혔거나 미초기화)")


def get_table_schema(table_names: Optional[List[str]] = None) -> dict:
    """
    Oracle ALL_TAB_COLUMNS 에서 테이블 컬럼 명세를 실시간 조회합니다.

    [v1.3 핵심 변경] READ-ONLY 계정 지원
    RAG_READONLY 같은 읽기전용 계정은 직접 소유한 테이블이 없습니다.
    → OWNER 필터 없이 ALL_TAB_COLUMNS 전체 조회 후 권한 있는 테이블만 반환
    → 접근 가능한 모든 테이블의 컬럼 정보를 LLM 에 제공

    [실제 테이블 소유자 자동 감지]
    ALL_TABLES 에서 접근 가능한 테이블의 OWNER 를 함께 반환
    → SQL 생성 시 올바른 스키마 prefix 제공 가능

    Args:
        table_names: 조회할 테이블명 목록 (None = 전체 접근 가능 테이블)

    Returns:
        테이블별 컬럼 명세 딕셔너리. 연결 실패 시 빈 dict.
    """
    # ── 테이블 소유자 자동 감지용 힌트 ────────────────────────────────
    # ORACLE_SCHEMA 설정이 있으면 해당 스키마 우선, 없으면 전체 접근 가능 테이블 조회
    _hint_schema = getattr(settings, "oracle_schema", "").strip().upper()
    _user = getattr(settings, "oracle_user", "").strip().upper()

    # ── 접근 가능한 테이블 목록 + 실제 소유자 조회 ────────────────────
    # ALL_TABLES: 현재 계정이 접근 가능한 모든 테이블 (소유 + SELECT 권한 부여)
    if _hint_schema and _hint_schema != _user:
        # oracle_schema 를 별도로 명시한 경우 해당 스키마 우선 조회
        tbl_owner_sql = """
SELECT OWNER, TABLE_NAME
FROM   ALL_TABLES
WHERE  OWNER = :owner
ORDER  BY TABLE_NAME
"""
        tbl_owner_params = {"owner": _hint_schema}
    else:
        # READ-ONLY 계정: 소유 테이블 없음 → 접근 가능한 모든 테이블
        # 시스템 스키마(SYS, SYSTEM 등) 제외
        tbl_owner_sql = """
SELECT OWNER, TABLE_NAME
FROM   ALL_TABLES
WHERE  OWNER NOT IN (
    'SYS','SYSTEM','OUTLN','DBSNMP','APPQOSSYS','WMSYS','EXFSYS',
    'CTXSYS','XDB','ANONYMOUS','APEX_PUBLIC_USER','APEX_030200',
    'FLOWS_FILES','MDSYS','OLAPSYS','ORDPLUGINS','ORDSYS','ORDDATA',
    'SI_INFORMTN_SCHEMA','DMSYS','SYSMAN','MGMT_VIEW','RAG_READONLY'
)
ORDER BY TABLE_NAME
"""
        tbl_owner_params = {}

    # ── 컬럼 조회 SQL (OWNER 필터 제거 → ALL_TAB_COLUMNS 전체 활용) ──
    if _hint_schema and _hint_schema != _user:
        col_sql = """
SELECT
    c.OWNER,
    c.TABLE_NAME,
    c.COLUMN_NAME,
    c.DATA_TYPE
        || CASE
            WHEN c.DATA_TYPE IN ('VARCHAR2','NVARCHAR2','CHAR','NCHAR')
                THEN '(' || c.DATA_LENGTH || ')'
            WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL
                THEN '(' || c.DATA_PRECISION
                     || CASE WHEN c.DATA_SCALE > 0 THEN ',' || c.DATA_SCALE ELSE '' END
                     || ')'
            ELSE ''
           END AS DATA_TYPE_FULL,
    c.NULLABLE,
    NVL(cc.COMMENTS, '-') AS COL_COMMENT
FROM ALL_TAB_COLUMNS c
LEFT JOIN ALL_COL_COMMENTS cc
    ON  cc.OWNER = c.OWNER AND cc.TABLE_NAME = c.TABLE_NAME
    AND cc.COLUMN_NAME = c.COLUMN_NAME
WHERE c.OWNER = :owner
ORDER BY c.TABLE_NAME, c.COLUMN_ID
"""
        col_params = {"owner": _hint_schema}
    else:
        col_sql = """
SELECT
    c.OWNER,
    c.TABLE_NAME,
    c.COLUMN_NAME,
    c.DATA_TYPE
        || CASE
            WHEN c.DATA_TYPE IN ('VARCHAR2','NVARCHAR2','CHAR','NCHAR')
                THEN '(' || c.DATA_LENGTH || ')'
            WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL
                THEN '(' || c.DATA_PRECISION
                     || CASE WHEN c.DATA_SCALE > 0 THEN ',' || c.DATA_SCALE ELSE '' END
                     || ')'
            ELSE ''
           END AS DATA_TYPE_FULL,
    c.NULLABLE,
    NVL(cc.COMMENTS, '-') AS COL_COMMENT
FROM ALL_TAB_COLUMNS c
LEFT JOIN ALL_COL_COMMENTS cc
    ON  cc.OWNER = c.OWNER AND cc.TABLE_NAME = c.TABLE_NAME
    AND cc.COLUMN_NAME = c.COLUMN_NAME
WHERE c.OWNER NOT IN (
    'SYS','SYSTEM','OUTLN','DBSNMP','APPQOSSYS','WMSYS','EXFSYS',
    'CTXSYS','XDB','ANONYMOUS','APEX_PUBLIC_USER','APEX_030200',
    'FLOWS_FILES','MDSYS','OLAPSYS','ORDPLUGINS','ORDSYS','ORDDATA',
    'SI_INFORMTN_SCHEMA','DMSYS','SYSMAN','MGMT_VIEW','RAG_READONLY'
)
ORDER BY c.TABLE_NAME, c.COLUMN_ID
"""
        col_params = {}

    # ── 테이블 코멘트 조회 ─────────────────────────────────────────────
    tbl_comment_sql = """
SELECT OWNER, TABLE_NAME, NVL(COMMENTS, '') AS COMMENTS
FROM   ALL_TAB_COMMENTS
WHERE  TABLE_TYPE = 'TABLE'
"""

    try:
        # 접근 가능한 테이블 목록 + 소유자 조회
        tbl_owner_rows = execute_query(
            tbl_owner_sql, params=tbl_owner_params, max_rows=5000
        )
        if tbl_owner_rows is None:
            logger.warning("get_table_schema: Oracle 연결 없음")
            return {}

        # 테이블명 → 실제 소유자 매핑 (SQL prefix 용)
        tbl_owner_map: dict = {
            r["TABLE_NAME"]: r["OWNER"] for r in (tbl_owner_rows or [])
        }

        # 테이블명 필터링
        filter_set: Optional[set] = None
        if table_names:
            filter_set = {t.upper().strip() for t in table_names}

        # 접근 가능한 테이블 중 필터 적용
        if filter_set:
            tbl_owner_map = {k: v for k, v in tbl_owner_map.items() if k in filter_set}

        if not tbl_owner_map:
            logger.warning(
                f"get_table_schema: 접근 가능한 테이블 없음 "
                f"(힌트스키마={_hint_schema or '없음'}, 필터={filter_set or '없음'}). "
                f".env 에 ORACLE_SCHEMA=실제스키마소유자 를 추가하세요."
            )
            return {}

        # 컬럼 조회
        col_rows = execute_query(col_sql, params=col_params, max_rows=50000)

        # 테이블 코멘트 조회
        tbl_comment_rows = execute_query(tbl_comment_sql, params={}, max_rows=5000)
        tbl_comments: dict = {
            r["TABLE_NAME"]: r["COMMENTS"] for r in (tbl_comment_rows or [])
        }

        # 결과 조립
        result: dict = {}
        for row in col_rows or []:
            tname = row.get("TABLE_NAME", "")
            if tname not in tbl_owner_map:  # 접근 가능한 테이블만
                continue

            if tname not in result:
                actual_owner = tbl_owner_map[tname]
                result[tname] = {
                    "comment": tbl_comments.get(tname, ""),
                    "owner": actual_owner,  # 실제 소유자 (SQL prefix 용)
                    "columns": [],
                }
            result[tname]["columns"].append(
                {
                    "name": row.get("COLUMN_NAME", ""),
                    "type": row.get("DATA_TYPE_FULL", ""),
                    "nullable": row.get("NULLABLE", "Y"),
                    "comment": row.get("COL_COMMENT", "-"),
                }
            )

        owner_summary = list({v["owner"] for v in result.values()})
        logger.info(
            f"get_table_schema: {len(result)}개 테이블 명세 수집 완료"
            f" (소유자: {owner_summary})"
        )
        return result

    except Exception as exc:
        logger.error(f"get_table_schema 오류: {exc}", exc_info=True)
        return {}


def format_schema_for_llm(schema: dict) -> str:
    """
    get_table_schema() 결과를 LLM 프롬프트용 텍스트로 변환합니다.

    [v1.3 변경] 실제 소유자(OWNER) 정보 포함
    READ-ONLY 계정 환경에서 LLM 이 올바른 스키마 prefix 를 SQL 에 사용하도록
    각 테이블의 실제 소유자를 프롬프트에 명시합니다.

    예시 출력:
    ### OMTIDN02 — 병실 정보  (소유자: HOSPITAL)
    → SQL 작성 시 FROM HOSPITAL.OMTIDN02 사용 권장
    | 컬럼명 | 타입 | NULL허용 | 설명 |

    Args:
        schema: get_table_schema() 반환값

    Returns:
        마크다운 형식의 스키마 텍스트
    """
    if not schema:
        return "(테이블 명세를 가져올 수 없습니다. Oracle 연결을 확인하세요.)"

    lines = []
    for tname, info in schema.items():
        comment = info.get("comment", "")
        owner = info.get("owner", "")  # 실제 테이블 소유자

        # 헤더: 테이블명 + 코멘트 + 소유자
        header = f"### {tname}"
        if comment:
            header += f" — {comment}"
        if owner:
            header += f"  (소유자: {owner})"
            header += f"\n-- SQL 작성 시: FROM {owner}.{tname}"
        lines.append(header)

        cols = info.get("columns", [])
        if cols:
            lines.append("| 컬럼명 | 타입 | NULL허용 | 설명 |")
            lines.append("|--------|------|----------|------|")
            for col in cols:
                name = col.get("name", "")
                dtype = col.get("type", "")
                nullable = col.get("nullable", "Y")
                cmt = col.get("comment", "-")
                lines.append(f"| {name} | {dtype} | {nullable} | {cmt} |")
        lines.append("")

    return "\n".join(lines)


def get_oracle_client():
    """oracle_access_config.py 호환용 래퍼"""

    class _Client:
        @property
        def is_connected(self):
            return get_oracle_pool() is not None

        def table_exists(self, table_name: str, schema: str = "JAIN_WM") -> bool:
            try:
                rows = execute_query(
                    "SELECT COUNT(*) AS CNT FROM ALL_TABLES "
                    "WHERE OWNER = :owner AND TABLE_NAME = :tname",
                    {"owner": schema.upper(), "tname": table_name.upper()},
                    max_rows=1,
                )
                return bool(rows and rows[0]["CNT"] > 0)
            except Exception:
                return False

        def execute_query(self, sql: str):
            rows = execute_query(sql)
            if rows is None:
                return None, []
            columns = list(rows[0].keys()) if rows else []
            return [[r[c] for c in columns] for r in rows], columns

    return _Client()
