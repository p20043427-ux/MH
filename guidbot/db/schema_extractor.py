"""
db/schema_extractor.py ─ DB 스키마·권한 자동 추출 → RAG 지식화 (v2.1)

[v2.1 수정사항]
- BUG#5 수정: tbl_rows 포맷 오류
  이전: f"{tbl_rows:,}건" → tbl_rows 가 None 또는 str 이면 TypeError
  수정: _safe_int() 헬퍼로 안전하게 int 변환 후 포맷팅

[핵심 기능]
병원 DB 의 테이블 정의(컬럼명/타입/설명)와 사용자 권한 정보를
SQL 쿼리로 자동 추출하여 LangChain Document 로 변환합니다.
이 Document 들은 벡터 DB 에 추가되어 RAG 지식베이스를 확장합니다.

[왜 DB 스키마를 RAG 에 포함하는가?]
- "환자 정보는 어느 테이블에 있나요?" → DB 스키마 문서로 답변 가능
- "USER_A는 어떤 테이블에 접근할 수 있나요?" → 권한 문서로 답변 가능
- DBeaver 수동 관리 완전 대체 → 항상 최신 스키마 자동 반영

[보안]
- SELECT 권한만 있는 rag_readonly 계정 사용 필수
- information_schema 만 쿼리 (실제 환자/직원 데이터 미접근)
- 패스워드·시크릿은 추출 내용에 절대 포함하지 않음
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from sqlalchemy import text

from config.settings import settings
from db.connector import get_db_connector
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


# ──────────────────────────────────────────────────────────────────────
#  스키마 추출 SQL
# ──────────────────────────────────────────────────────────────────────

_SQL_MYSQL_TABLES = """
SELECT
    t.TABLE_NAME       AS 테이블명,
    t.TABLE_COMMENT    AS 테이블설명,
    t.TABLE_ROWS       AS 예상행수,
    t.CREATE_TIME      AS 생성일시
FROM information_schema.TABLES t
WHERE t.TABLE_SCHEMA = :db_name
  AND t.TABLE_TYPE   = 'BASE TABLE'
ORDER BY t.TABLE_NAME
"""

_SQL_MYSQL_COLUMNS = """
SELECT
    c.TABLE_NAME       AS 테이블명,
    c.COLUMN_NAME      AS 컬럼명,
    c.COLUMN_TYPE      AS 데이터타입,
    c.IS_NULLABLE      AS NULL허용,
    c.COLUMN_DEFAULT   AS 기본값,
    c.COLUMN_KEY       AS 키종류,
    c.COLUMN_COMMENT   AS 컬럼설명,
    c.ORDINAL_POSITION AS 순서
FROM information_schema.COLUMNS c
WHERE c.TABLE_SCHEMA = :db_name
ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
"""

_SQL_MYSQL_USER_GRANTS = """
SELECT
    GRANTEE            AS 사용자,
    TABLE_SCHEMA       AS 데이터베이스,
    TABLE_NAME         AS 테이블명,
    PRIVILEGE_TYPE     AS 권한종류,
    IS_GRANTABLE       AS 재부여가능
FROM information_schema.TABLE_PRIVILEGES
WHERE TABLE_SCHEMA = :db_name
ORDER BY GRANTEE, TABLE_NAME, PRIVILEGE_TYPE
"""

_SQL_MSSQL_TABLES = """
SELECT
    t.name             AS 테이블명,
    ep.value           AS 테이블설명,
    p.rows             AS 예상행수
FROM sys.tables t
LEFT JOIN sys.extended_properties ep
    ON ep.major_id = t.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
LEFT JOIN sys.partitions p
    ON p.object_id = t.object_id AND p.index_id IN (0, 1)
ORDER BY t.name
"""

_SQL_MSSQL_COLUMNS = """
SELECT
    t.name             AS 테이블명,
    c.name             AS 컬럼명,
    tp.name            AS 데이터타입,
    c.max_length       AS 최대길이,
    c.is_nullable      AS NULL허용,
    dc.definition      AS 기본값,
    ep.value           AS 컬럼설명
FROM sys.columns c
JOIN sys.tables t  ON c.object_id = t.object_id
JOIN sys.types tp  ON c.user_type_id = tp.user_type_id
LEFT JOIN sys.default_constraints dc ON c.default_object_id = dc.object_id
LEFT JOIN sys.extended_properties ep
    ON ep.major_id = c.object_id AND ep.minor_id = c.column_id
   AND ep.name = 'MS_Description'
ORDER BY t.name, c.column_id
"""


# ──────────────────────────────────────────────────────────────────────
#  유틸리티 함수
# ──────────────────────────────────────────────────────────────────────

def _safe_int(value: Any, default: int = 0) -> int:
    """
    DB 쿼리 결과의 숫자 값을 안전하게 int 로 변환합니다.

    [BUG#5 수정]
    MySQL TABLE_ROWS 컬럼은 InnoDB 엔진에서 None 또는 str 로 반환될 수 있습니다.
    이를 직접 f"{value:,}건" 포맷에 적용하면 TypeError 가 발생합니다.
    이 함수로 안전하게 변환 후 포맷팅합니다.

    Args:
        value:   변환할 값 (None, str, int, float 등 DB 반환값 모두 처리)
        default: 변환 불가 시 반환할 기본값

    Returns:
        정수값 또는 default

    Example::

        _safe_int(None)       → 0
        _safe_int("1234")     → 1234
        _safe_int(1234.5)     → 1234
        _safe_int("invalid")  → 0
    """
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# ──────────────────────────────────────────────────────────────────────
#  데이터 클래스
# ──────────────────────────────────────────────────────────────────────

@dataclass
class SchemaInfo:
    """
    DB 스키마 추출 결과 요약.

    SchemaExtractor.extract() 의 반환값입니다.

    Attributes:
        tables:       테이블 정보 딕셔너리 리스트
        columns:      컬럼 정보 딕셔너리 리스트
        user_grants:  사용자 권한 딕셔너리 리스트
        table_count:  추출된 테이블 수
        column_count: 추출된 컬럼 수
    """

    tables:       List[Dict[str, Any]] = field(default_factory=list)
    columns:      List[Dict[str, Any]] = field(default_factory=list)
    user_grants:  List[Dict[str, Any]] = field(default_factory=list)
    table_count:  int = 0
    column_count: int = 0


# ──────────────────────────────────────────────────────────────────────
#  스키마 추출기
# ──────────────────────────────────────────────────────────────────────

class SchemaExtractor:
    """
    병원 DB 스키마·권한 자동 추출기.

    extract() 로 스키마 정보를 가져오고,
    to_documents() 로 RAG 용 Document 리스트로 변환합니다.
    """

    def extract(self) -> Optional[SchemaInfo]:
        """
        DB 스키마 전체를 추출합니다.

        [추출 대상]
        - information_schema.TABLES: 테이블명, 설명, 예상 행수
        - information_schema.COLUMNS: 컬럼명, 타입, NULL 허용, 설명 등
        - information_schema.TABLE_PRIVILEGES: 사용자별 권한 (MySQL 전용)

        Returns:
            SchemaInfo. DB 비활성화 또는 연결 실패 시 None.
        """
        connector = get_db_connector()
        if connector is None or not connector.is_connected:
            logger.info("DB 비활성화 또는 미연결 → 스키마 추출 건너뜀")
            return None

        db_name = settings.db_name
        info = SchemaInfo()

        try:
            with connector.get_session() as session:
                # ── 테이블 정보 ────────────────────────────────────────
                if settings.db_type == "mysql":
                    rows = session.execute(
                        text(_SQL_MYSQL_TABLES), {"db_name": db_name}
                    ).mappings().all()
                else:
                    rows = session.execute(text(_SQL_MSSQL_TABLES)).mappings().all()

                info.tables = [dict(row) for row in rows]
                info.table_count = len(info.tables)
                logger.info(f"테이블 {info.table_count}개 추출 완료")

                # ── 컬럼 정보 ──────────────────────────────────────────
                if settings.db_type == "mysql":
                    rows = session.execute(
                        text(_SQL_MYSQL_COLUMNS), {"db_name": db_name}
                    ).mappings().all()
                else:
                    rows = session.execute(text(_SQL_MSSQL_COLUMNS)).mappings().all()

                info.columns = [dict(row) for row in rows]
                info.column_count = len(info.columns)
                logger.info(f"컬럼 {info.column_count}개 추출 완료")

                # ── 사용자 권한 (MySQL 전용) ───────────────────────────
                if settings.db_type == "mysql":
                    try:
                        rows = session.execute(
                            text(_SQL_MYSQL_USER_GRANTS), {"db_name": db_name}
                        ).mappings().all()
                        info.user_grants = [dict(row) for row in rows]
                        logger.info(f"권한 정보 {len(info.user_grants)}건 추출 완료")
                    except Exception as exc:
                        # rag_readonly 계정은 TABLE_PRIVILEGES 접근 권한이 없을 수 있음
                        # → 경고만 출력하고 계속 (스키마 정보는 계속 유효)
                        logger.warning(f"권한 정보 추출 실패 (계속 진행): {exc}")

            return info

        except Exception as exc:
            logger.error(f"스키마 추출 실패: {exc}", exc_info=True)
            return None

    def to_documents(self, info: SchemaInfo) -> List[Document]:
        """
        SchemaInfo 를 RAG 용 LangChain Document 리스트로 변환합니다.

        [변환 전략]
        - 테이블별로 하나의 Document (테이블명 + 컬럼 목록 + 설명)
        - 권한 정보는 사용자별로 하나의 Document
        - 각 Document 의 metadata["source"] = "db_schema" 또는 "db_user_grants"

        [왜 테이블별로 하나의 Document 인가?]
        "patient 테이블의 컬럼 목록" 같은 질문에서
        테이블 정보가 하나의 청크에 있어야 관련 내용이 한 번에 검색됩니다.
        테이블/컬럼이 각각 별도 Document 이면 두 번의 검색이 필요합니다.

        Args:
            info: extract() 반환값

        Returns:
            RAG 벡터 DB 에 추가할 Document 리스트
        """
        if not info:
            return []

        documents: List[Document] = []

        # ── 컬럼 정보를 테이블명으로 그룹핑 ──────────────────────────
        columns_by_table: Dict[str, List[Dict]] = {}
        for col in info.columns:
            tbl_name = col.get("테이블명", "")
            columns_by_table.setdefault(tbl_name, []).append(col)

        # ── 테이블별 Document 생성 ────────────────────────────────────
        for table in info.tables:
            tbl_name = table.get("테이블명", "")
            tbl_desc = table.get("테이블설명") or "설명 없음"

            # BUG#5 수정: _safe_int() 로 안전 변환 후 포맷팅
            tbl_rows = _safe_int(table.get("예상행수"))
            tbl_rows_str = f"{tbl_rows:,}건" if tbl_rows > 0 else "알 수 없음"

            # 컬럼 목록 텍스트
            col_lines: List[str] = []
            for col in columns_by_table.get(tbl_name, []):
                col_name = col.get("컬럼명", "")
                col_type = col.get("데이터타입", "")
                col_desc = col.get("컬럼설명") or ""
                col_key  = col.get("키종류") or ""
                nullable = "NULL 허용" if col.get("NULL허용") in ("YES", True, 1) else "NOT NULL"

                key_info  = f" [{col_key}]" if col_key else ""
                desc_info = f" - {col_desc}" if col_desc else ""
                col_lines.append(
                    f"  - {col_name} ({col_type}, {nullable}){key_info}{desc_info}"
                )

            content = (
                f"[DB 테이블 정보] {settings.db_name}.{tbl_name}\n"
                f"테이블 설명: {tbl_desc}\n"
                f"예상 데이터 건수: {tbl_rows_str}\n\n"
                f"컬럼 목록:\n"
                + ("\n".join(col_lines) if col_lines else "  (컬럼 정보 없음)")
            )

            documents.append(Document(
                page_content=content,
                metadata={
                    "source": "db_schema",
                    "table":  tbl_name,
                    "type":   "table_definition",
                    "page":   "N/A",
                },
            ))

        # ── 사용자 권한별 Document 생성 ──────────────────────────────
        grants_by_user: Dict[str, List[Dict]] = {}
        for grant in info.user_grants:
            user = grant.get("사용자", "unknown")
            grants_by_user.setdefault(user, []).append(grant)

        for user, grants in grants_by_user.items():
            grant_lines = [
                f"  - {g.get('테이블명', '')}: {g.get('권한종류', '')} "
                f"({'재부여 가능' if g.get('재부여가능') == 'YES' else '재부여 불가'})"
                for g in grants
            ]

            content = (
                f"[DB 사용자 권한 정보] 사용자: {user}\n"
                f"데이터베이스: {settings.db_name}\n\n"
                f"보유 권한:\n"
                + "\n".join(grant_lines)
            )

            documents.append(Document(
                page_content=content,
                metadata={
                    "source": "db_user_grants",
                    "user":   user,
                    "type":   "user_permission",
                    "page":   "N/A",
                },
            ))

        logger.info(
            f"DB 스키마 → Document 변환 완료: "
            f"테이블 {len(info.tables)}개, "
            f"사용자 {len(grants_by_user)}명"
        )
        return documents


def extract_schema_documents() -> List[Document]:
    """
    DB 스키마·권한을 추출하여 RAG 용 Document 리스트를 반환합니다.

    build_db.py 에서 호출하는 편의 함수입니다.

    Returns:
        Document 리스트. DB 비활성화 또는 추출 실패 시 빈 리스트.

    Example::

        from db.schema_extractor import extract_schema_documents
        db_docs = extract_schema_documents()
        # all_docs = pdf_docs + db_docs 로 벡터 DB 구축
    """
    extractor = SchemaExtractor()
    info = extractor.extract()

    if info is None:
        return []

    return extractor.to_documents(info)
