"""
llm/sql_generator.py  ─  자연어 → Oracle SQL 변환기 + 보안 검증 (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[역할]
  사용자의 자연어 질문을 안전한 Oracle SELECT 쿼리로 변환합니다.
  생성된 SQL 은 SqlValidator 를 통과한 경우에만 실행됩니다.

[Text-to-SQL 파이프라인]
  ┌──────────────────────────────────────────────────────────┐
  │  자연어 질문 (NL)                                        │
  │       ↓                                                  │
  │  1. 화이트리스트 테이블 목록 + 스키마 정보 로드           │
  │       ↓                                                  │
  │  2. 전문 시스템 프롬프트 + 사용자 질문 → Gemini LLM 호출  │
  │       ↓                                                  │
  │  3. LLM 응답에서 SQL 블록 추출 (마크다운 코드 블록)        │
  │       ↓                                                  │
  │  4. SqlValidator 로 보안 검증 (DML 차단, 행 제한 등)      │
  │       ↓                                                  │
  │  검증된 SELECT SQL 반환                                   │
  └──────────────────────────────────────────────────────────┘

[보안 설계]
  Text-to-SQL 의 최대 위험: LLM 이 DROP TABLE 같은 위험한 SQL 생성
  → 다중 방어 레이어로 대응:
    Layer 1: 시스템 프롬프트에 "SELECT 만 생성" 강력 지시
    Layer 2: SqlValidator 로 DML(INSERT/UPDATE/DELETE/DROP 등) 감지 후 차단
    Layer 3: FETCH FIRST N ROWS 로 결과 행 수 강제 제한
    Layer 4: 화이트리스트 테이블만 허용 (스키마 외 테이블 접근 불가)
    Layer 5: rag_readonly 계정은 SELECT 권한만 → DB 레벨에서도 차단

[화이트리스트 테이블 관리]
  settings.oracle_whitelist_tables 에 허용할 테이블 목록을 정의합니다.
  비어있으면 schema 내 모든 테이블 허용 (권장하지 않음).
  예) ORACLE_WHITELIST_TABLES=CHECKUP_MASTER,CHECKUP_DETAIL,REVENUE_DAILY
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from typing import Set, List, Optional, Tuple

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


# ──────────────────────────────────────────────────────────────────────
#  위험 SQL 패턴 (Layer 2 보안)
#
#  정규식으로 DML · DDL · 위험 함수를 탐지합니다.
#  대소문자 구분 없이(re.IGNORECASE) 검사합니다.
#
#  [왜 정규식인가?]
#  SQL 파서를 쓰면 더 정확하지만 sqlparse 같은 라이브러리 의존성이 추가됩니다.
#  정규식만으로도 명백한 DML/DDL 은 충분히 탐지 가능합니다.
#  단, 난독화된 SQL (주석 삽입, 유니코드 인코딩 등)은 추가 검증 필요.
# ──────────────────────────────────────────────────────────────────────

_DANGEROUS_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # ── DML: 데이터 변경 ─────────────────────────────────────────────
    (re.compile(r"\bINSERT\b", re.IGNORECASE), "INSERT 문 차단"),
    (re.compile(r"\bUPDATE\b", re.IGNORECASE), "UPDATE 문 차단"),
    (re.compile(r"\bDELETE\b", re.IGNORECASE), "DELETE 문 차단"),
    (re.compile(r"\bMERGE\b", re.IGNORECASE), "MERGE 문 차단"),
    (re.compile(r"\bUPSERT\b", re.IGNORECASE), "UPSERT 문 차단"),
    # ── DDL: 구조 변경 ─────────────────────────────────────────────
    (re.compile(r"\bDROP\b", re.IGNORECASE), "DROP 문 차단"),
    (re.compile(r"\bCREATE\b", re.IGNORECASE), "CREATE 문 차단"),
    (re.compile(r"\bALTER\b", re.IGNORECASE), "ALTER 문 차단"),
    (re.compile(r"\bTRUNCATE\b", re.IGNORECASE), "TRUNCATE 문 차단"),
    (re.compile(r"\bRENAME\b", re.IGNORECASE), "RENAME 문 차단"),
    # ── DCL / TCL ──────────────────────────────────────────────────
    (re.compile(r"\bGRANT\b", re.IGNORECASE), "GRANT 문 차단"),
    (re.compile(r"\bREVOKE\b", re.IGNORECASE), "REVOKE 문 차단"),
    (re.compile(r"\bCOMMIT\b", re.IGNORECASE), "COMMIT 문 차단"),
    (re.compile(r"\bROLLBACK\b", re.IGNORECASE), "ROLLBACK 문 차단"),
    (re.compile(r"\bSAVEPOINT\b", re.IGNORECASE), "SAVEPOINT 문 차단"),
    # ── Oracle 위험 패키지 / 함수 ──────────────────────────────────
    (re.compile(r"\bEXECUTE\b", re.IGNORECASE), "EXECUTE 차단"),
    (re.compile(r"\bEXEC\b", re.IGNORECASE), "EXEC 차단"),
    (re.compile(r"\bDBMS_\w+", re.IGNORECASE), "DBMS 패키지 차단"),
    (re.compile(r"\bUTL_FILE\b", re.IGNORECASE), "UTL_FILE(파일접근) 차단"),
    (re.compile(r"\bUTL_HTTP\b", re.IGNORECASE), "UTL_HTTP(외부통신) 차단"),
    (re.compile(r"\bUTL_SMTP\b", re.IGNORECASE), "UTL_SMTP 차단"),
    (re.compile(r"\bUTL_TCP\b", re.IGNORECASE), "UTL_TCP 차단"),
    # ── 다중 쿼리 / SQL Injection ──────────────────────────────────
    # 세미콜론으로 쿼리 분리 시도 차단
    (re.compile(r";\s*\w", re.IGNORECASE), "세미콜론 다중 쿼리 차단"),
    # Union-based injection: SELECT 1, @@version 등 시도 차단
    # 단, 정상적인 UNION ALL SELECT 는 서브쿼리에서 쓰임 → 화이트리스트 제어로 충분
    # (re.compile(r"\bUNION\b",       re.IGNORECASE), "UNION 차단"),  # 너무 공격적
    # 시스템 테이블 직접 접근 차단
    (re.compile(r"\bALL_USERS\b", re.IGNORECASE), "시스템 테이블 차단"),
    (re.compile(r"\bDBA_\w+", re.IGNORECASE), "DBA 뷰 차단"),
    (re.compile(r"\bV\$\w+", re.IGNORECASE), "V$ 동적 뷰 차단"),
    (re.compile(r"\bSYS\.\w+", re.IGNORECASE), "SYS 스키마 차단"),
    # 주석 기반 우회 시도 (-- 또는 /* 후 위험 구문)
    # Layer 2 에서 주석 제거 후 검사하므로 여기선 이중 방어용
    (re.compile(r"--.*\bDROP\b", re.IGNORECASE), "주석 내 DROP 차단"),
    (re.compile(r"/\*.*\bDROP\b", re.IGNORECASE | re.DOTALL), "블록주석 내 DROP 차단"),
]

# ── 멀티 스테이트먼트 엄격 차단 (세미콜론 포함 SQL 전체 거부) ─────
_RE_SEMICOLON = re.compile(r";")

# SELECT 시작 확인 패턴
# CTAS(CREATE TABLE AS SELECT) 차단을 위해 SELECT 로만 시작하도록 강제
_RE_STARTS_WITH_SELECT = re.compile(r"^\s*SELECT\b", re.IGNORECASE)

# ROWNUM / FETCH FIRST 행 제한 패턴
_RE_HAS_ROWNUM = re.compile(r"\bROWNUM\b", re.IGNORECASE)
_RE_HAS_FETCH = re.compile(r"\bFETCH\s+FIRST\b", re.IGNORECASE)

# 마크다운 코드 블록에서 SQL 추출
# ```sql ... ``` 또는 ``` ... ``` 형식
# ── 스키마 캐시 (매 질문마다 Oracle 2회 쿼리 방지) ──────────────────
# 캐시 구조: {"schema_text": str, "ts": float}
# TTL: 5분 — 테이블 구조가 바뀔 일이 거의 없으므로 충분
import time as _time

_SCHEMA_CACHE: dict = {}
_SCHEMA_CACHE_TTL = 300  # 5분

_RE_CODE_BLOCK = re.compile(
    r"```(?:sql|SQL|oracle)?\s*\n(.*?)\n```",
    re.DOTALL,
)


# ──────────────────────────────────────────────────────────────────────
#  결과 데이터 클래스
# ──────────────────────────────────────────────────────────────────────


@dataclass
class SqlGenerationResult:
    """
    SQL 생성 및 검증 결과.

    Attributes:
        sql:        생성된 SQL 문자열 (검증 실패 시 빈 문자열)
        is_valid:   보안 검증 통과 여부
        error:      검증 실패 이유 또는 LLM 오류 메시지
        raw_llm:    LLM 원본 응답 (디버깅용)
        table_used: SQL 에서 감지된 테이블명 목록
    """

    sql: str = ""
    is_valid: bool = False
    error: str = ""
    raw_llm: str = ""
    table_used: List[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
#  SQL 보안 검증기
# ──────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────
#  실행 전 Oracle 메타데이터 검증 (v2.2 신규)
#
#  [왜 필요한가]
#  LLM 이 생성한 SQL 에는 두 가지 치명적 오류가 자주 발생합니다:
#  1. ORA-00942: LLM 이 JAIN_WM.EMIHPTMI 로 생성했는데 실제론 JAIN_OCS.EMIHPTMI
#  2. ORA-00932: PTMIINDT (NUMBER YYYYMMDD) 에 TRUNC() 적용
#
#  이 함수는 실제 Oracle 메타데이터(ALL_TAB_COLUMNS)를 조회하여
#  SQL 실행 전에 사전 검증합니다.
#  실패 시 오류 원인과 수정 힌트를 반환하여 LLM 재시도에 활용합니다.
# ──────────────────────────────────────────────────────────────────────


def _pre_execute_validate(sql: str) -> Tuple[bool, str, str]:
    """
    SQL 실행 전 Oracle ALL_TAB_COLUMNS 기반 메타데이터 검증 + 자동 교정.

    [v2.3 변경사항]
    · 반환값 3번째 추가: corrected_sql (자동 교정된 SQL)
    · 스키마 오류 감지 시 → 올바른 스키마로 SQL 자동 교정하여 반환
      예) JAIN_WM.EMIHPTMI → JAIN_OCS.EMIHPTMI 자동 치환
    · 타입 충돌(ORA-00932) 시 → 힌트 반환 (자동 교정 제한적)

    [검증 항목]
    1. 테이블 존재 여부 (ORA-00942 사전 차단 + 자동 교정)
    2. TRUNC + NUMBER 컬럼 타입 충돌 감지 (ORA-00932 사전 차단)

    Returns:
        (is_valid, error_hint, corrected_sql)
        is_valid=True , ""    , ""           → 검증 통과
        is_valid=True , ""    , corrected_sql → 자동 교정 완료 → 교정된 SQL 사용
        is_valid=False, hint  , ""           → 수동 수정 필요
    """
    try:
        from db.oracle_client import execute_query

        sql_upper = sql.upper()

        # ── 1단계: FROM/JOIN 에서 스키마.테이블 추출 ──────────────────
        tbl_pattern = re.compile(
            r"\bFROM\s+([\w]+\.)?([\w]+)|\bJOIN\s+([\w]+\.)?([\w]+)",
            re.IGNORECASE,
        )
        SKIP = {"SELECT", "DUAL", "ROWNUM", "WITH"}
        found_tables = []  # [(schema, table), ...]
        for m in tbl_pattern.finditer(sql):
            raw_schema = (m.group(1) or m.group(3) or "").rstrip(".")
            raw_table = (m.group(2) or m.group(4) or "").upper()
            if not raw_table or raw_table in SKIP:
                continue
            found_tables.append((raw_schema.upper() if raw_schema else "", raw_table))

        if not found_tables:
            return True, "", ""

        # ── 2단계: 테이블 존재 여부 검증 + 자동 스키마 교정 ───────────
        # [v2.3 핵심 변경]
        # 이전: 스키마 불일치 감지 → 차단 (사용자에게 오류 반환)
        # 변경: 스키마 불일치 감지 → SQL 자동 교정 후 실행 계속
        #
        # 교정 방식:
        #   FROM JAIN_WM.EMIHPTMI → FROM JAIN_OCS.EMIHPTMI (ALL_TABLES 조회 결과 적용)
        # 교정 불가 (테이블 자체 없음) → 기존대로 오류 반환
        default_schema = str(getattr(settings, "oracle_schema", "JAIN_WM")).upper()
        corrected_sql = sql  # 자동 교정 SQL (초기값 = 원본)
        was_corrected = False

        for raw_schema, tbl in found_tables:
            chk_schema = raw_schema or default_schema
            chk_sql = (
                f"SELECT OWNER, TABLE_NAME FROM ALL_TABLES "
                f"WHERE OWNER = '{chk_schema}' AND TABLE_NAME = '{tbl}' "
                f"AND ROWNUM <= 1"
            )
            try:
                rows = execute_query(sql=chk_sql, max_rows=1)
                if not rows:
                    # 지정 스키마에 테이블 없음 → 전체 스키마에서 검색
                    search_sql = (
                        f"SELECT OWNER FROM ALL_TABLES "
                        f"WHERE TABLE_NAME = '{tbl}' AND ROWNUM <= 3"
                    )
                    candidates = execute_query(sql=search_sql, max_rows=3) or []
                    real_schemas = []
                    for r in candidates:
                        owner = r.get("OWNER", "") if isinstance(r, dict) else str(r[0])
                        if owner:
                            real_schemas.append(owner)

                    if real_schemas:
                        # ✅ 실제 스키마 발견 → SQL 자동 교정
                        correct_schema = real_schemas[0]
                        # JAIN_WM.EMIHPTMI → JAIN_OCS.EMIHPTMI 치환
                        # 스키마 없이 테이블명만 있는 경우도 처리
                        import re as _re

                        # 패턴1: WRONG_SCHEMA.TABLE → CORRECT_SCHEMA.TABLE
                        corrected_sql = _re.sub(
                            rf"\b{re.escape(chk_schema)}\.{re.escape(tbl)}\b",
                            f"{correct_schema}.{tbl}",
                            corrected_sql,
                            flags=_re.IGNORECASE,
                        )
                        was_corrected = True
                        logger.info(
                            f"SQL 스키마 자동 교정: {chk_schema}.{tbl} → "
                            f"{correct_schema}.{tbl}"
                        )
                    else:
                        # ❌ 테이블 자체가 없음 → 오류 반환
                        hint = (
                            f"{chk_schema}.{tbl} 테이블을 찾을 수 없습니다. "
                            f"테이블명을 확인하세요."
                        )
                        logger.warning(f"사전 검증 실패 (테이블 없음): {hint}")
                        return False, hint, ""
            except Exception:
                pass  # 검증 실패 시 원본 SQL 로 실행 계속

        if was_corrected:
            return True, "", corrected_sql  # 교정된 SQL 반환

        # ── 3단계: TRUNC/DATE 함수 + NUMBER 컬럼 타입 충돌 ──────────
        trunc_pattern = re.compile(r"\bTRUNC\s*\(\s*([\w]+)\s*\)", re.IGNORECASE)
        trunc_cols = trunc_pattern.findall(corrected_sql)

        for col_name in trunc_cols:
            for raw_schema, tbl in found_tables:
                chk_schema = raw_schema or default_schema
                dtype_sql = (
                    f"SELECT DATA_TYPE FROM ALL_TAB_COLUMNS "
                    f"WHERE OWNER = '{chk_schema}' "
                    f"AND TABLE_NAME = '{tbl}' "
                    f"AND COLUMN_NAME = '{col_name.upper()}' "
                    f"AND ROWNUM <= 1"
                )
                try:
                    dtype_rows = execute_query(sql=dtype_sql, max_rows=1)
                    if dtype_rows:
                        r = dtype_rows[0]
                        dtype = (
                            r.get("DATA_TYPE") if isinstance(r, dict) else r[0]
                        ) or ""
                        if str(dtype).upper() in ("NUMBER", "INTEGER", "FLOAT"):
                            hint = (
                                f"TRUNC({col_name}) 오류: {col_name} 은 {dtype} 타입 "
                                f"(ORA-00932 예방). "
                                f"예: WHERE {col_name} = TO_NUMBER(TO_CHAR(SYSDATE,'YYYYMMDD'))"
                            )
                            logger.warning(f"사전 검증 실패 (타입 충돌): {hint}")
                            return False, hint, ""
                except Exception:
                    pass

        return True, "", ""

    except Exception as exc:
        logger.debug(f"사전 검증 스킵 (Oracle 미연결 등): {exc}")
        return True, "", ""  # 검증 불가 시 원본 SQL 로 실행 계속


def _enrich_with_knowledge(schema_ctx: str, question: str) -> str:
    """
    [v2.4] 스키마 컨텍스트에 쿼리 예제 + 개발 문서를 추가합니다.

    SQL 생성 정확도 향상 3대 지식 소스:
    1. RAG_ACCESS_CONFIG — 테이블/컬럼 설명 (기존)
    2. query_db — 전산팀 검증 쿼리 예제 (신규)
       · 유사 질문에서 사용한 패턴 참고
       · 코드값, 조인 조건, 날짜 형식 검증된 예제 제공
    3. doc_db — 개발 문서 / 코드표 (신규)
       · PTMIINMN=60 → 구급차 등 코드 의미
       · ERD, 테이블 관계, 비즈니스 규칙

    보안 주의:
    · 실제 환자 데이터는 이 함수에 도달하지 않음
    · schema_ctx: 테이블 구조만 (데이터 없음) ✅
    · query/doc 검색 결과: SQL + 설명 (데이터 없음) ✅
    """
    enriched = schema_ctx

    try:
        from db.knowledge_db_builder import search_query_examples, search_doc_knowledge

        # 쿼리 예제 검색
        _q_examples = search_query_examples(question, k=2)
        if _q_examples:
            enriched += "\n\n" + _q_examples
            logger.debug(f"쿼리 예제 추가: {len(_q_examples)}자")

        # 개발 문서 / 코드표 검색
        _doc_know = search_doc_knowledge(question, k=2)
        if _doc_know:
            enriched += "\n\n" + _doc_know
            logger.debug(f"개발 문서 추가: {len(_doc_know)}자")

    except Exception as _e:
        logger.debug(f"지식DB 추가 실패 (무시): {_e}")

    return enriched


# ──────────────────────────────────────────────────────────────────────
#  PII 보호 헬퍼 함수 (v2.4 신규)
# ──────────────────────────────────────────────────────────────────────


def _get_all_pii_columns_upper() -> Set[str]:
    """
    RAG_ACCESS_CONFIG + pii_masker 키워드 두 소스에서 PII 컬럼명 수집.

    [소스 1] RAG_ACCESS_CONFIG MASK_COLUMNS
      → DBeaver 에서 수동 등록한 컬럼명 (가장 정확)
    [소스 2] pii_masker._ID_COLUMN_KEYWORDS / _NAME_COLUMN_KEYWORDS
      → 패턴 기반 자동 감지 (보조)

    Returns:
        대문자 정규화된 PII 컬럼명 집합
    """
    pii: Set[str] = set()
    # 소스 1: RAG_ACCESS_CONFIG
    try:
        from db.oracle_access_config import get_access_config_manager

        all_pii_map = get_access_config_manager().get_all_pii_columns()
        for cols in all_pii_map.values():
            pii.update(c.upper() for c in cols)
    except Exception:
        pass
    # 소스 2: pii_masker 키워드 (이름/ID 계열만 — 전화/주소는 컬럼명 충돌 적음)
    try:
        from db.pii_masker import (
            _NAME_COLUMN_KEYWORDS,
            _ID_COLUMN_KEYWORDS,
            _RRN_COLUMN_KEYWORDS,
        )

        for kw in _NAME_COLUMN_KEYWORDS | _ID_COLUMN_KEYWORDS | _RRN_COLUMN_KEYWORDS:
            pii.add(kw.upper())
    except Exception:
        pass
    return pii


def _remove_pii_columns_from_select(sql: str) -> str:
    """
    SELECT 절에서 PII 컬럼을 자동 제거합니다.

    [처리 범위]
    - 단순 컬럼명: SELECT PTMINAME, PTMIINDT → SELECT PTMIINDT
    - AS alias: SELECT PTMINAME AS 환자명, PTMIINDT → SELECT PTMIINDT
    - 스키마.테이블.컬럼: T.PTMINAME → 제거

    [처리 안 함]
    - WHERE 절 조건 (제거하면 SQL 의미 변경)
    - 집계 함수 인자 내 PII (COUNT(PTMINAME) 등 — 실제론 발생 거의 없음)

    Returns:
        PII 제거된 SQL (변경 없으면 원본 반환)
    """
    pii_cols = _get_all_pii_columns_upper()
    if not pii_cols:
        return sql

    # SELECT ... FROM 사이의 컬럼 목록 추출
    select_match = re.match(
        r"(SELECT\s+)(.*?)(\s+FROM\s+)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if not select_match:
        return sql  # 파싱 불가 → 원본 반환

    prefix = select_match.group(1)  # "SELECT "
    col_block = select_match.group(2)  # "A, B AS 별칭, C"
    suffix = sql[select_match.end(2) :]  # " FROM ..." 이후

    # 컬럼 항목 분리 (쉼표 기준, 괄호 안 쉼표는 제외)
    items = _split_select_items(col_block)

    kept = []
    removed = []
    for item in items:
        item_stripped = item.strip()
        # 컬럼명 추출 (T.컬럼명, 컬럼명 AS 별칭 등 처리)
        col_upper = _extract_column_name(item_stripped).upper()
        if col_upper in pii_cols:
            removed.append(col_upper)
        else:
            kept.append(item_stripped)

    if not removed:
        return sql  # 제거된 항목 없음

    if not kept:
        # 모든 컬럼이 PII → 집계 컬럼으로 대체
        logger.warning(f"SELECT 절 전체가 PII 컬럼 — COUNT(*) 로 대체: {removed}")
        return f"SELECT COUNT(*) AS 건수{suffix}"

    logger.info(f"PII 컬럼 SELECT 자동 제거: {removed} (잔여 {len(kept)}개 컬럼)")
    new_col_block = ",\n    ".join(kept)
    return f"{prefix}{new_col_block}{suffix}"


def _split_select_items(col_block: str) -> List[str]:
    """쉼표로 SELECT 항목을 분리 (괄호 안 쉼표 무시)."""
    items = []
    depth = 0
    current = []
    for ch in col_block:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        items.append("".join(current).strip())
    return [i for i in items if i]


def _extract_column_name(item: str) -> str:
    """
    "T.PTMINAME AS 환자명" 같은 항목에서 컬럼명만 추출.

    처리 패턴:
      PTMINAME          → PTMINAME
      T.PTMINAME        → PTMINAME
      PTMINAME AS 환자명 → PTMINAME
      T.PTMINAME AS 환자명 → PTMINAME
    """
    # AS 절 제거
    item = re.sub(r"\s+AS\s+.*$", "", item, flags=re.IGNORECASE).strip()
    # 스키마.테이블.컬럼 또는 테이블.컬럼 → 마지막 부분만
    if "." in item:
        item = item.rsplit(".", 1)[-1].strip()
    return item


def _count_select_columns(sql: str) -> int:
    """SELECT 절의 컬럼 수를 반환합니다 (집계 함수 포함)."""
    select_match = re.match(
        r"SELECT\s+(.*?)\s+FROM\s+",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if not select_match:
        return 0
    return len(_split_select_items(select_match.group(1)))


class SqlValidator:
    """
    생성된 SQL 을 다중 레이어로 보안 검증합니다.

    [검증 순서]
    1. SELECT 시작 확인 → DML/DDL 로 시작하는 SQL 즉시 차단
    2. 위험 패턴 스캔 → INSERT/UPDATE/DELETE/DROP 등 감지
    3. 화이트리스트 확인 → 허용된 테이블만 사용하는지 확인
    4. 행 제한 확인 → ROWNUM 또는 FETCH FIRST 없으면 자동 추가
    """

    def __init__(self) -> None:
        # [화이트리스트 우선순위 — v2.2 수정]
        # 이전: .env ORACLE_WHITELIST_TABLES 만 사용
        #         → EMIHPTMI 등 DB 에는 등록됐지만 .env 에 없으면 차단되는 버그
        # 수정: RAG_ACCESS_CONFIG(DB) 우선, 없으면 .env 폴백
        try:
            from db.oracle_access_config import get_access_config_manager

            _wl = get_access_config_manager().get_whitelist()
            self.whitelist: List[str] = [t.upper() for t in _wl if t]
        except Exception:
            raw: Optional[List[str]] = getattr(
                settings, "oracle_whitelist_tables", None
            )
            self.whitelist = (
                [t.upper().strip() for t in raw if t.strip()] if raw else []
            )

        # 최대 행 수 설정
        self.max_rows: int = int(getattr(settings, "oracle_max_rows", 5000))

    def validate(self, sql: str) -> Tuple[bool, str, str]:
        """
        SQL 보안 검증 수행.

        Args:
            sql: 검증할 SQL 문자열

        Returns:
            (is_valid, safe_sql, error_message)
            · is_valid=True  → safe_sql 에 실행 가능한 SQL 반환
            · is_valid=False → error_message 에 차단 이유 반환
        """
        if not sql or not sql.strip():
            return False, "", "SQL 이 비어있습니다."

        # 세미콜론 제거 (멀티 스테이트먼트 방지)
        sql = sql.rstrip(";").strip()

        # ── Layer 1: SELECT 로 시작하는지 확인 ────────────────────
        if not _RE_STARTS_WITH_SELECT.match(sql):
            reason = "SELECT 로 시작하지 않는 쿼리는 실행할 수 없습니다."
            logger.warning(f"SQL 검증 실패 (Layer1): {reason}")
            return False, "", reason

        # ── Layer 2: 위험 패턴 스캔 ──────────────────────────────
        # [이중 검사 전략]
        # 1단계: 주석 제거 후 패턴 검사 (주석 안에 DML 숨기기 방지)
        # 2단계: 세미콜론 존재 자체를 차단 (다중 쿼리 완전 차단)
        sql_no_comments = re.sub(r"--[^\n]*", "", sql)
        sql_no_comments = re.sub(r"/\*.*?\*/", "", sql_no_comments, flags=re.DOTALL)
        sql_no_comments = sql_no_comments.strip().rstrip(";")  # 끝 세미콜론은 허용

        # 세미콜론이 중간에 있으면 다중 쿼리 시도 → 즉시 차단
        # (끝에만 있는 세미콜론은 정상 SQL이므로 위에서 제거 후 검사)
        if _RE_SEMICOLON.search(sql_no_comments):
            reason = "세미콜론 다중 쿼리 차단 (SQL Injection 방지)"
            logger.warning(f"SQL 검증 실패 (Layer2-세미콜론): {reason}")
            return False, "", reason

        for pattern, reason in _DANGEROUS_PATTERNS:
            if pattern.search(sql_no_comments):
                logger.warning(f"SQL 검증 실패 (Layer2): {reason}\nSQL: {sql[:100]}")
                return False, "", f"보안 차단: {reason}"

        # ── Layer 2.5: FROM 절 존재 검증 ─────────────────
        # [버그 수정 v2.1]
        # 원인: r'\bFROM\b' 가 \x08FROM\x08 로 저장되어 regex word boundary 작동 안 함
        # 이로 인해 FROM JAIN_WM.OMTIDN02 같은 정상 SQL 도 FROM 감지 실패 → false positive 차단
        # 해결: re.search 대신 단순 문자열 포함 여부로 확인
        _sql_upper = sql_no_comments.upper()
        _is_with = _sql_upper.strip().startswith("WITH ")
        # FROM 어떤 문자들 다음에 오드 단순 포함 여부 확인
        # JAIN_WM.OMTIDN02 같은 스키마.테이블 패턴도 정상 감지
        _has_from = (
            " FROM " in _sql_upper
            or _sql_upper.startswith("FROM ")
            or "\nFROM " in _sql_upper
            or "\nFROM\n" in _sql_upper
        )
        if not _has_from and not _is_with:
            reason = "FROM 절 없는 SELECT 문 — ORA-00923 방지 차단"
            logger.warning(
                f"SQL 검증 실패 (Layer2.5-FROM없음): {reason}\n"
                f"SQL 상위 150자: {sql[:150]}"
            )
            return False, "", reason

        # ── Layer 3: 화이트리스트 테이블 확인 ────────────────────
        if self.whitelist:
            # [멀티 스키마 지원 — v2.1]
            # JAIN_WM.OMTIDN02, JAIN_OCS.EXMRQST01 처럼 다른 스키마 테이블도 허용.
            # FROM / JOIN 뒤 스키마.테이블 패턴에서 테이블명만 추출하여 화이트리스트 확인.
            # 스키마명 자체는 화이트리스트 체크 대상에서 제외.
            table_pattern = re.compile(
                r"\bFROM\s+(?:\w+\.)?([\w]+)|\bJOIN\s+(?:\w+\.)?([\w]+)",
                re.IGNORECASE,
            )
            # 스키마명 + Oracle 예약어 제외 목록
            SKIP_TOKENS = {
                "SELECT",
                "WITH",
                "DUAL",
                "ROWNUM",
                # 허용된 스키마명 (테이블명이 아님)
                "JAIN_WM",
                "JAIN_OCS",
                "JAIN_NCS",
                # 공통 Oracle 뷰/메타
                "ALL_TABLES",
                "ALL_COLUMNS",
                "USER_TABLES",
            }
            mentioned_tables = []
            for m in table_pattern.finditer(sql_no_comments):
                tbl = (m.group(1) or m.group(2) or "").upper()
                if tbl and tbl not in SKIP_TOKENS:
                    mentioned_tables.append(tbl)

            # 화이트리스트에 없는 테이블 접근 차단
            forbidden = [t for t in mentioned_tables if t not in self.whitelist]
            if forbidden:
                reason = f"허용되지 않은 테이블 접근: {', '.join(forbidden)}"
                logger.warning(f"SQL 검증 실패 (Layer3): {reason}")
                return False, "", reason

        # ── Layer 4: 행 수 제한 자동 추가 ────────────────────────
        # ── Layer 4: 행 수 제한 처리 (Oracle 10g 호환) ──────────────
        # [전략]
        # 1. FETCH FIRST 가 있으면 → 무조건 ROWNUM 으로 변환
        #    (Oracle 10g/11g 에서 FETCH FIRST 는 ORA-00933 오류)
        # 2. ROWNUM 도 없고 FETCH FIRST 도 없으면 → ROWNUM 자동 추가
        if _RE_HAS_FETCH.search(sql):
            # FETCH FIRST N ROWS ONLY 제거 후 ROWNUM 서브쿼리로 교체
            sql = re.sub(
                r"\n?\s*FETCH\s+FIRST\s+\d+\s+ROWS\s+ONLY\s*$",
                "",
                sql,
                flags=re.IGNORECASE,
            ).rstrip()
            sql = f"SELECT * FROM (\n{sql}\n) WHERE ROWNUM <= {self.max_rows}"
            logger.debug(f"FETCH FIRST → ROWNUM 변환 (Oracle 10g 호환)")

        elif not _RE_HAS_ROWNUM.search(sql):
            # ROWNUM 도 없으면 자동 추가
            sql = f"SELECT * FROM (\n{sql}\n) WHERE ROWNUM <= {self.max_rows}"
            logger.debug(f"행 제한 자동 추가: ROWNUM <= {self.max_rows}")

        # ── Layer 4.5: PII 컬럼 처리 (v2.5 변경) ─────────────────
        # [v2.5 변경 이유]
        # 기존: PII 컬럼을 SQL에서 완전 제거 → 사용자가 화면에서도 볼 수 없음
        # 변경: PII 컬럼을 SQL에 포함 허용
        #   · 화면 표시: _apply_masking() 에서 *** 마스킹 처리
        #   · LLM AI 분석: _llm_safe_rows() 에서 PII 컬럼 자동 제거 후 전달
        # → 사용자는 마스킹된 데이터로 현황 파악 가능
        # → LLM/외부 API에는 개인정보 전달 없음
        # sql = _remove_pii_columns_from_select(sql)  # [v2.5] 비활성화

        # ── Layer 4.8: 컬럼 과다 선택 감지 ───────────────────────
        # SELECT 절에 컬럼이 15개 이상이면 경고 로그
        _select_cols = _count_select_columns(sql)
        if _select_cols > 15:
            logger.warning(
                f"SQL 컬럼 과다 선택 감지: {_select_cols}개 컬럼 "
                f"(권장 12개 이하) — 개인정보 포함 여부 확인 필요"
            )

        logger.info(f"SQL 검증 통과 (Layer1~4.8): {sql[:80]}...")
        return True, sql, ""


# ──────────────────────────────────────────────────────────────────────
#  시스템 프롬프트 생성
# ──────────────────────────────────────────────────────────────────────


def _build_system_prompt(table_schema_info: str, question: str = "") -> str:
    """
    Text-to-SQL LLM 에 전달할 시스템 프롬프트를 생성합니다.

    [좋은 Text-to-SQL 프롬프트의 요소]
    1. 데이터베이스 유형 명시 (Oracle SQL 문법)
    2. 허용 테이블과 컬럼 스키마 제공
    3. 출력 형식 강제 (코드 블록만)
    4. 보안 제약 명시 (SELECT only)
    5. 병원 도메인 특화 가이드라인
    6. Oracle 특수 문법 가이드 (ROWNUM, TO_DATE, NVL 등)

    Args:
        table_schema_info: 허용 테이블의 스키마 정보 문자열

    Returns:
        시스템 프롬프트 문자열
    """
    schema_block = (
        table_schema_info.strip() if table_schema_info else "(스키마 정보 없음)"
    )

    return textwrap.dedent(f"""
    당신은 병원 데이터 분석 전문 Oracle SQL 생성 AI입니다.

    ## 역할
    사용자의 자연어 질문을 안전한 Oracle SELECT 쿼리로 변환합니다.

    ## 데이터베이스 정보
    - DBMS: Oracle Database (11g 이상)
    - 기본 스키마: JAIN_WM  (예: JAIN_WM.OMTIDN02)
    - OCS 스키마:  JAIN_OCS (처방/검사/수술 관련 테이블)
    - 스키마명을 테이블명 앞에 붙여 사용: JAIN_WM.테이블명, JAIN_OCS.테이블명

    ## 허용 테이블 및 컬럼 스키마
    {schema_block}

    ## 필수 규칙 (반드시 준수) — 위반 시 실행 오류 발생
    1. **SELECT 문만 생성** — INSERT, UPDATE, DELETE, DROP, TRUNCATE 절대 금지
    2. **코드 블록으로 SQL만 출력** — 설명 텍스트 없이 ```sql ... ``` 형식으로만 출력
    3. **ROWNUM으로 행 수 제한** — 최대 5000행, Oracle 11g 호환 (FETCH FIRST 사용 금지)
    4. **허용 테이블만 사용** — 위 "허용 테이블 및 컬럼 스키마" 에 없는 테이블 참조 금지
    5. **바인드 변수 미사용** — 리터럴 값을 SQL 에 직접 작성

    ## ⚠️ 스키마 필수 규칙 (가장 중요)
    - 각 테이블의 "FROM 절" 정보를 반드시 그대로 사용하세요
    - 예) **FROM 절: FROM JAIN_OCS.EMIHPTMI** → 반드시 `FROM JAIN_OCS.EMIHPTMI` 사용
    - 예) **FROM 절: FROM JAIN_WM.OMTIDN02**  → 반드시 `FROM JAIN_WM.OMTIDN02` 사용
    - 스키마명을 임의로 변경하면 ORA-00942 (테이블 없음) 오류 발생
    - 절대로 스키마명을 추측하거나 생략하지 마세요

    ## ⚠️ 컬럼 타입 규칙 (ORA-00932 방지)
    - 컬럼 설명에 **(NUMBER YYYYMMDD)** 라고 명시된 컬럼 → DATE 함수 적용 금지
      · 잘못된 예: TRUNC(PTMIINDT)  → ORA-00932 (NUMBER에 DATE함수 적용)
      · 올바른 예: PTMIINDT = TO_NUMBER(TO_CHAR(SYSDATE, 'YYYYMMDD'))
      · 올바른 예: PTMIINDT = TO_CHAR(SYSDATE, 'YYYYMMDD')
    - 컬럼 설명에 **(DATE)** 라고 명시된 경우에만 TRUNC, TO_DATE 사용 가능
    - 컬럼 타입이 불명확하면 오늘 날짜 비교는 두 방식 모두 주석으로 표시

    ## Oracle SQL 가이드라인
    - 날짜 비교: TO_DATE('2024-01-01', 'YYYY-MM-DD') 또는 DATE '2024-01-01'
    - 현재 날짜: SYSDATE (CURRENT_DATE 가 아님)
    - NULL 처리: NVL(컬럼, 기본값)
    - 문자열 연결: 컬럼1 || ' ' || 컬럼2
    - 올해: EXTRACT(YEAR FROM SYSDATE)
    - 월별 집계: TRUNC(날짜컬럼, 'MM')
    - 분기별 집계: TRUNC(날짜컬럼, 'Q')

    ## 병원 도메인 가이드
    - "건강검진" = 검진센터 방문 건수
    - "매출" = 수납/청구 금액
    - "입원/외래" = 입원환자 vs 외래환자 구분
    - "위내시경" = 상부내시경 검사 코드
    - 날짜 범위: "최근 1년" = SYSDATE - 365, "올해" = 해당 연도 1월 1일부터
    - "응급실" / "응급환자" = EMIHPTMI 또는 NCMEMR02 테이블 사용
    - "처방" / "검사결과" / "수술" = JAIN_OCS 스키마 테이블 사용
    - 스키마가 다른 테이블은 FROM JAIN_OCS.테이블명 형식으로 명시
    - "병실" / "입원현황" / "병동" = OMTIDN02 테이블 사용

    ## 테이블 선택 규칙 (매우 중요)
    위 "허용 테이블 및 컬럼 스키마"에서 테이블 별칭(alias)과 설명을 반드시 참고하세요.
    질문 키워드와 테이블 별칭이 가장 밀접한 테이블을 선택하세요.

    ## FROM 절 필수 규칙
    - SELECT 문에는 반드시 FROM 절이 있어야 합니다
    - FROM 없는 SELECT 는 ORA-00923 오류 발생 → 절대 금지
    - ✅ 올바른 예: SELECT A, B FROM EMIHPTMI WHERE ...
    - ❌ 금지:      SELECT A, B WHERE ...  (FROM 누락)

    ## ⚠️ 컬럼 선택 규칙 (매우 중요 — 반드시 준수)
    - **SELECT * 절대 금지** — 전체 컬럼 조회는 개인정보 노출 위험
    - **질문에 직접 관련된 컬럼만 선택** (최대 12개 이하)
    - 스키마의 🔒 마스킹 처리 컬럼은 SELECT 에 포함 가능 — 화면에 *** 로 자동 마스킹됨
    - 리스트/목록 요청: 식별용 핵심 컬럼 5~8개만 선택
      예) 응급환자 리스트 → 내원일시, 내원경위, 중증도, 진료구역, 결과 관련 컬럼만
    - 집계/통계 요청: COUNT, SUM 등 집계 + GROUP BY 컬럼만 선택
    - **나쁜 예** (금지): 테이블의 모든 컬럼을 나열하는 SELECT
    - **좋은 예**: SELECT PTMIINDT, PTMIINMN, PTMIKTS1, PTMIAREA, PTMIEMRT FROM ...

    ## ✅ 컬럼 한국어 별칭 (AS) 필수 규칙 — 반드시 적용
    - **모든 SELECT 컬럼에 AS 한국어_별칭 을 반드시 붙여라**
    - 별칭은 스키마 "설명" 컬럼의 첫 번째 의미 단어를 사용 (괄호 이전 부분)
    - 별칭에 공백이 있으면 큰따옴표로 감싸거나 붙여쓰기 사용
    - 예) PTMIAKDT → PTMIAKDT AS 내원일자
    - 예) PTMIKTS1 → PTMIKTS1 AS 중증도
    - 예) PTMIAREA → PTMIAREA AS 진료구역
    - 예) COUNT(*) → COUNT(*) AS 건수
    - 예) TO_CHAR(SYSDATE,'YYYY-MM') → TO_CHAR(SYSDATE,'YYYY-MM') AS 월
    - **별칭 없는 SELECT는 결과 테이블에 영문 컬럼명이 그대로 노출 — 사용자 가독성 저하**
    - 집계함수(COUNT, SUM, AVG, MAX, MIN)에도 반드시 AS 별칭 부여

    ## 🔒 개인정보 처리 방침
    - 스키마에 🔒 표시된 컬럼은 개인정보 — SELECT 에 포함하면 화면에 자동 마스킹(***) 처리됨
    - 환자 이름, 환자번호 등 식별 컬럼은 필요 시 SELECT 에 포함 가능 (화면 *** 표시)
    - AI 분석(LLM)에는 해당 컬럼이 자동 제거되어 전달됨 — 개인정보 외부 유출 없음
    - 통계/집계 쿼리에서는 개인 식별 컬럼 포함 불필요 (COUNT, SUM 등만 사용)

    ## 출력 형식 예시 — alias 필수 적용
    ```sql
    -- ✅ 집계 예시 (alias 필수)
    SELECT
        TO_CHAR(VISIT_DATE, 'YYYY-MM') AS 방문월,
        COUNT(*)                        AS 방문건수
    FROM  JAIN_WM.CHECKUP_MASTER
    WHERE VISIT_DATE >= ADD_MONTHS(SYSDATE, -12)
    GROUP BY TO_CHAR(VISIT_DATE, 'YYYY-MM')
    ORDER BY 방문월
    ) WHERE ROWNUM <= 1000

    -- ✅ 리스트 예시 (alias 필수)
    SELECT
        PTMIAKDT  AS 내원일자,
        PTMIAKTM  AS 내원시간,
        PTMIKTS1  AS 중증도,
        PTMIEMSY  AS 내원증상,
        PTMIAREA  AS 진료구역,
        PTMIEMRT  AS 진료결과
    FROM  JAIN_OCS.EMIHPTMI
    WHERE PTMIAKDT = TO_CHAR(SYSDATE, 'YYYYMMDD')
    AND   ROWNUM <= 5000
    ```
    """).strip()


def _build_table_schema(whitelist: List[str], question: str = "") -> str:
    """
    허용 테이블의 스키마 정보를 텍스트로 반환합니다.

    [스키마 정보 우선순위]
    0순위: session_state 수동 입력 (UI 편집기 저장값)
    1순위: schema_vector_store 유사도 검색 (질문 관련 테이블만 추출)
    2순위: settings.oracle_table_descriptions (수동 정의)
    3순위: Oracle 실시간 조회 (ALL_TAB_COLUMNS) — 모듈 캐시 5분

    [schema_vector_store 활용 효과]
    · 100개 테이블 중 질문과 관련된 3~5개만 LLM에 전달
    · 프롬프트 길이 절약 + SQL 생성 정확도 향상

    Args:
        whitelist: 허용 테이블명 목록 (빈 리스트 = 전체)
        question:  사용자 질문 (schema_vector_store 검색용)

    Returns:
        마크다운 형식의 스키마 설명 텍스트
    """
    # ── 0순위: session_state 수동 입력 명세 ─────────────────────────
    try:
        import streamlit as _st

        _manual = _st.session_state.get("da_manual_schema", "")
        if _manual and _manual.strip():
            logger.info("스키마 소스: UI 수동 입력 (session_state)")
            return _manual.strip()
    except Exception:
        pass

    # ── 0.5순위: RAG_ACCESS_CONFIG 테이블/컬럼 설명 (v1.1 신규)
    # [우선순위 이유]
    # DBeaver 에서 직접 등록한 TABLE_DESC + COLUMN_DESCS 는 가장 정확한 정보.
    # SQL 생성 정확도 향상의 핵심: 병동코드 의미, 날짜 포맷, 코드값 등 반영.
    # schema_vector_store(임베딩 기반)보다 먼저 시도하여 더 정확한 컨텍스트 제공.
    if question:
        try:
            from db.oracle_access_config import get_access_config_manager

            _mgr = get_access_config_manager()
            # 질문 관련 테이블 추출 (whitelist 기반 — 전체 화이트리스트 사용)
            _schema_ctx = _mgr.get_schema_context_for_sql_gen(
                table_names=whitelist if whitelist else None
            )
            if (
                _schema_ctx
                and "(RAG_ACCESS_CONFIG 에 등록된 테이블 없음)" not in _schema_ctx
            ):
                _has_rich_desc = "| 컬럼명 |" in _schema_ctx
                if _has_rich_desc:
                    # [v2.4] 쿼리 예제 + 개발 문서 지식을 스키마 뒤에 추가
                    _schema_ctx = _enrich_with_knowledge(_schema_ctx, question)
                    logger.info(
                        f"스키마 소스: RAG_ACCESS_CONFIG v1.1 "
                        f"(COLUMN_DESCS + 지식DB 포함, {len(_schema_ctx)}자)"
                    )
                    return _schema_ctx
                logger.debug("RAG_ACCESS_CONFIG: COLUMN_DESCS 없음 → 다음 순위 시도")
        except Exception as _e:
            logger.debug(f"RAG_ACCESS_CONFIG 스키마 조회 스킵: {_e}")

    # ── 1순위: schema_vector_store 유사도 검색 (질문 관련 테이블만 추출)
    if question:
        try:
            # schema_oracle_loader 기반 schema_db 우선 검색
            from db.schema_oracle_loader import get_schema_context_for_question as _gsc

            _ctx = _gsc(question, k=5)
            if _ctx:
                logger.debug(f"스키마 소스: schema_oracle_loader ({len(_ctx)}자)")
                return _ctx
        except Exception as _e:
            logger.debug(f"schema_oracle_loader 스킵: {_e}")
        try:
            from db.schema_vector_store import search_schema_context

            _ctx = search_schema_context(question, k_tables=4, k_examples=2)
            if _ctx:
                logger.debug(f"스키마 소스: schema_vector_store ({len(_ctx)}자)")
                return _ctx
        except Exception as _e:
            logger.debug(f"schema_vector_store 스킵: {_e}")

    # ── 2순위(구 1순위): settings 수동 정의 ──────────────────────────
    table_desc = getattr(settings, "oracle_table_descriptions", {})
    if isinstance(table_desc, str):
        import json as _j

        try:
            table_desc = (
                _j.loads(table_desc) if table_desc.strip().startswith("{") else {}
            )
        except Exception:
            table_desc = {}

    if table_desc and isinstance(table_desc, dict):
        lines = []
        for tbl, desc in table_desc.items():
            if not whitelist or tbl.upper() in {w.upper() for w in whitelist}:
                lines.append(f"### {tbl}\n{desc}")
        if lines:
            logger.debug(
                f"스키마 소스: settings.oracle_table_descriptions ({len(lines)}개 테이블)"
            )
            return "\n\n".join(lines)

    # ── 2순위: Oracle 실시간 조회 — 5분 TTL 모듈 캐시 ────────────────
    # [왜 캐시가 필요한가?]
    # 매 질문마다 get_table_schema() 가 Oracle 에 2번 쿼리 (ALL_TABLES + ALL_TAB_COLUMNS)
    # 테이블 구조는 거의 바뀌지 않으므로 5분 캐시로 DB 부하 90% 절감
    _cache_key = ",".join(sorted(whitelist)) if whitelist else "__all__"
    _now = _time.time()
    _cached = _SCHEMA_CACHE.get(_cache_key)
    if _cached and (_now - _cached.get("ts", 0)) < _SCHEMA_CACHE_TTL:
        logger.debug(
            f"스키마 소스: 모듈 캐시 hit (키={_cache_key[:40]}, "
            f"남은TTL={int(_SCHEMA_CACHE_TTL - (_now - _cached['ts']))}초)"
        )
        return _cached["schema_text"]

    try:
        from db.oracle_client import get_table_schema, format_schema_for_llm

        schema = get_table_schema(table_names=whitelist if whitelist else None)
        if schema:
            schema_text = format_schema_for_llm(schema)
            # 캐시 저장
            _SCHEMA_CACHE[_cache_key] = {"schema_text": schema_text, "ts": _now}
            logger.info(
                f"스키마 소스: Oracle 실시간 조회 ({len(schema)}개 테이블) → 캐시 저장"
            )
            return schema_text
    except Exception as exc:
        logger.warning(f"Oracle 실시간 스키마 조회 실패: {exc}")

    # ── 3순위: 테이블명만 (폴백) ──────────────────────────────────────
    if whitelist:
        logger.warning("스키마 소스: 테이블명만 제공 (컬럼 정보 없음)")
        return "허용 테이블 목록 (컬럼 정보 없음):\n" + "\n".join(
            f"- {t}" for t in whitelist
        )

    return "(스키마 정보를 가져올 수 없습니다. Oracle 연결 및 ORACLE_WHITELIST_TABLES 설정을 확인하세요.)"


# ──────────────────────────────────────────────────────────────────────
#  SQL 추출기
# ──────────────────────────────────────────────────────────────────────


def _extract_sql_from_llm_response(text: str) -> str:
    """
    LLM 응답 텍스트에서 SQL 문을 추출합니다.

    [추출 우선순위]
    1. ```sql ... ``` 코드 블록 → 가장 정확한 방식
    2. ``` ... ``` 코드 블록 (언어 미지정)
    3. SELECT 로 시작하는 텍스트 전체 → 폴백

    Args:
        text: LLM 원본 응답 텍스트

    Returns:
        추출된 SQL 문자열. 추출 실패 시 빈 문자열.
    """
    if not text:
        return ""

    # 코드 블록 추출 (```sql ... ``` 또는 ``` ... ```)
    matches = _RE_CODE_BLOCK.findall(text)
    if matches:
        # 여러 코드 블록이 있으면 첫 번째 사용
        return matches[0].strip()

    # 폴백: SELECT 로 시작하는 줄부터 끝까지 추출
    lines = text.split("\n")
    sql_lines = []
    in_sql = False
    for line in lines:
        if re.match(r"^\s*SELECT\b", line, re.IGNORECASE):
            in_sql = True
        if in_sql:
            sql_lines.append(line)

    return "\n".join(sql_lines).strip()


# ──────────────────────────────────────────────────────────────────────
#  공개 API: SQL 생성
# ──────────────────────────────────────────────────────────────────────


def generate_sql(user_question: str) -> SqlGenerationResult:
    """
    자연어 질문을 Oracle SELECT SQL 로 변환합니다.

    [처리 순서]
    1. 허용 테이블 목록 + 스키마 정보 로드
    2. 시스템 프롬프트 구성
    3. Gemini LLM 호출 (스트리밍 미사용 — SQL 전체 필요)
    4. LLM 응답에서 SQL 추출
    5. SqlValidator 보안 검증
    6. SqlGenerationResult 반환

    Args:
        user_question: 사용자 자연어 질문
                       예) "지난 1년간 월별 건강검진 수 추세 보여줘"

    Returns:
        SqlGenerationResult

    Example::

        result = generate_sql("부서별 매출 TOP 10")
        if result.is_valid:
            rows = execute_query(result.sql)
        else:
            st.error(result.error)
    """
    if not user_question or not user_question.strip():
        return SqlGenerationResult(error="질문이 비어있습니다.")

    # 화이트리스트 로드
    # [화이트리스트 우선순위 — v2.2 수정]
    # 1순위: RAG_ACCESS_CONFIG DB (가장 최신, 스키마명 포함)
    # 2순위: .env ORACLE_WHITELIST_TABLES (폴백)
    # 이전 버전은 .env 만 사용 → EMIHPTMI 등 DB 등록 테이블 차단 버그
    whitelist: List[str] = []
    try:
        from db.oracle_access_config import get_access_config_manager

        _mgr = get_access_config_manager()
        whitelist = _mgr.get_whitelist()  # RAG_ACCESS_CONFIG 활성 테이블명
        logger.debug(
            f"화이트리스트 소스: RAG_ACCESS_CONFIG ({len(whitelist)}개: {whitelist})"
        )
    except Exception as _e:
        logger.debug(f"RAG_ACCESS_CONFIG 화이트리스트 로드 실패 → .env 폴백: {_e}")
        raw_wl = getattr(settings, "oracle_whitelist_tables", [])
        whitelist = [t.upper().strip() for t in raw_wl if t.strip()] if raw_wl else []

    # 스키마 정보 구성
    # [Fix v2.1] question 을 _build_table_schema 에 전달해야
    # 0.5순위(RAG_ACCESS_CONFIG), 1순위(schema_vector_store) 가 동작함.
    # 전달 안 하면 둘 다 건너뛰어서 "응급실" → EMIHPTMI 매핑이 불가능해짐.
    schema_info = _build_table_schema(whitelist, question=user_question)
    system_prompt = _build_system_prompt(schema_info, question=user_question)

    # LLM 호출 (v1.5 — 429 키 로테이션 재시도 루프 추가)
    #
    # [키 로테이션 전략]
    # 6개 API 키를 공유 풀(llm.py 동일 풀)에서 순환 사용.
    # 429 발생 시 해당 키를 exhausted 로 마킹 → 즉시 다음 키로 재시도.
    # 모든 키 소진 시 사용자에게 명확한 오류 메시지 표시.
    raw_text = ""
    _last_exc: Optional[Exception] = None
    try:
        import google.genai as _genai
        from google.genai import types as _types
        from core.llm import get_key_pool
        from config.settings import settings as _settings

        _kpool = get_key_pool()

        # 최대 키 개수만큼 재시도 (각 키당 1회)
        _max_attempts = max(
            1, _kpool.total_count() if hasattr(_kpool, "total_count") else 6
        )
        for _attempt in range(_max_attempts):
            _api_key = _kpool.get_available_key()
            if not _api_key:
                raise RuntimeError(
                    "모든 API 키 할당량이 소진되었습니다. "
                    "내일 자정(UTC) 이후 재시도하거나 유료 플랜으로 업그레이드하세요."
                )

            try:
                _full_prompt = (
                    f"{system_prompt}\n\n## 사용자 질문\n{user_question.strip()}"
                )
                _client = _genai.Client(api_key=_api_key)
                _cfg = _types.GenerateContentConfig(
                    max_output_tokens=1024,  # SQL은 짧음 → 2048→1024 (응답 빠름)
                    temperature=0.0,  # SQL 은 결정론적으로
                    thinking_config=_types.ThinkingConfig(
                        thinking_budget=0,  # thinking 없음 → 응답 ~30% 빠름
                    )
                    if hasattr(_types, "ThinkingConfig")
                    else None,
                )
                _response = _client.models.generate_content(
                    model=_settings.chat_model,
                    contents=_full_prompt,
                    config=_cfg,
                )
                raw_text = _response.text or ""
                logger.debug(f"LLM SQL 응답 (앞 300자): {raw_text[:300]}")
                break  # 성공 → 루프 탈출

            except Exception as _exc:
                _exc_str = str(_exc).lower()
                _last_exc = _exc
                # 429 → 해당 키 마킹 후 즉시 다음 키로
                if (
                    "429" in _exc_str
                    or "quota" in _exc_str
                    or "resource_exhausted" in _exc_str
                ):
                    _key_suffix = _api_key[-4:] if len(_api_key) >= 4 else _api_key
                    if hasattr(_kpool, "mark_key_exhausted"):
                        _kpool.mark_key_exhausted(_api_key)
                    avail = (
                        _kpool.available_count()
                        if hasattr(_kpool, "available_count")
                        else 0
                    )
                    logger.warning(
                        f"SQL 생성: 키 [...{_key_suffix}] 429 초과 → "
                        f"다음 키로 전환 (남은 키: {avail}개)"
                    )
                    continue  # 대기 없이 다음 키 시도
                else:
                    raise  # 429 외 오류는 즉시 전파
        else:
            # 모든 키 소진
            raise RuntimeError(
                f"모든 API 키({_max_attempts}개) 429 초과. "
                "내일 재시도하거나 유료 플랜으로 업그레이드하세요."
            )

    except Exception as exc:
        logger.error(f"LLM SQL 생성 실패: {exc}", exc_info=True)
        return SqlGenerationResult(
            error=f"SQL 생성 중 오류 발생: {exc}",
            raw_llm="",
        )

    # SQL 추출
    extracted_sql = _extract_sql_from_llm_response(raw_text)
    if not extracted_sql:
        return SqlGenerationResult(
            error="LLM 응답에서 SQL 을 추출하지 못했습니다. 질문을 더 구체적으로 입력해 주세요.",
            raw_llm=raw_text,
        )

    # 보안 검증
    validator = SqlValidator()
    is_valid, safe_sql, error_msg = validator.validate(extracted_sql)

    if not is_valid:
        logger.warning(f"SQL 보안 검증 실패: {error_msg}")
        return SqlGenerationResult(
            sql="",
            is_valid=False,
            error=error_msg,
            raw_llm=raw_text,
        )

    # ── 사전 실행 검증: Oracle 메타데이터 기반 + 자동 교정 (v2.3) ────
    # 1) 스키마 불일치 → 자동 교정 SQL 반환 (차단 안 함)
    # 2) 테이블 자체 없음 → 오류 반환
    # 3) TRUNC + NUMBER 타입 충돌 → 힌트와 함께 오류 반환
    pre_ok, pre_hint, corrected_sql = _pre_execute_validate(safe_sql)

    if corrected_sql:
        # 스키마 자동 교정 완료 → 교정된 SQL 로 교체
        logger.info(
            f"SQL 스키마 자동 교정 적용: {safe_sql[:60]}... → {corrected_sql[:60]}..."
        )
        safe_sql = corrected_sql

    if not pre_ok:
        # 테이블 없음 or 타입 충돌 → 차단
        logger.warning(f"SQL 메타데이터 사전 검증 실패 → {pre_hint}")
        return SqlGenerationResult(
            sql=safe_sql,
            is_valid=False,
            error=f"⚠️ SQL 실행 전 오류 감지: {pre_hint}",
            raw_llm=raw_text,
        )

    # 사용된 테이블 추출 (로깅·감사용) — 스키마.테이블 패턴 포함
    table_pattern = re.compile(
        r"\bFROM\s+(?:[\w]+\.)?([\w]+)|\bJOIN\s+(?:[\w]+\.)?([\w]+)", re.IGNORECASE
    )
    used_tables = list(
        {
            (m.group(1) or m.group(2) or "").upper()
            for m in table_pattern.finditer(safe_sql)
            if (m.group(1) or m.group(2) or "").upper() not in {"ROWNUM", "DUAL", ""}
        }
    )

    logger.info(f"SQL 생성 완료: 테이블={used_tables}, 길이={len(safe_sql)}자")

    return SqlGenerationResult(
        sql=safe_sql,
        is_valid=True,
        error="",
        raw_llm=raw_text,
        table_used=used_tables,
    )
