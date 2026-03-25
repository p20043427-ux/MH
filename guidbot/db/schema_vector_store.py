"""
db/schema_vector_store.py — 테이블 명세 / 예시 쿼리 전용 벡터 DB (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[왜 RAG 벡터 DB와 분리하는가]

  RAG 벡터 DB (faiss_db/)           Schema 벡터 DB (schema_db/)
  ─────────────────────────          ──────────────────────────────
  규정집 PDF / 지침서 청크            테이블 명세 / 컬럼 코멘트
  "퇴직금 계산 방법?"                 "어느 테이블에 병실정보 있나?"
  16,930개 768차원 벡터               수십~수백개 벡터
  build_db.py 로 구축                 이 모듈로 자동 구축

[SQL 생성 흐름]
  사용자 질문
    └─► schema_vector_store 검색 → 관련 테이블 명세 + 예시 쿼리 추출
         └─► LLM 프롬프트에 주입 → 더 정확한 SQL 생성

[데이터 소스 우선순위]
  1. Oracle ALL_TAB_COLUMNS 실시간 조회
  2. settings.oracle_table_descriptions (수동 정의)
  3. UI 수동 입력 (da_manual_schema)
  4. settings.oracle_example_queries (예시 쿼리 JSON)

[파일 경로]
  settings.rag_db_path 의 부모 디렉토리 / schema_db
  예) D:/MH/guidbot/vector_store/schema_db/
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from config.settings import settings
from core.embeddings import get_embeddings_auto
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# ── 경로 설정 ────────────────────────────────────────────────────────
# [경로 수정] .parent 제거 — vector_store/ 안에 schema_db/ 생성
# 잘못된 경로: D:/mh/guidbot/schema_db  (.parent 사용 시)
# 올바른 경로: D:/mh/guidbot/vector_store/schema_db  (아래)
_SCHEMA_DB_DIR = Path(settings.rag_db_path) / "schema_db"

# ── 모듈 캐시 ────────────────────────────────────────────────────────
_cached_db: Optional[FAISS] = None


# ═══════════════════════════════════════════════════════════════════
#  문서 변환 유틸
# ═══════════════════════════════════════════════════════════════════


def _schema_to_docs(schema: Dict[str, Any], table_name: str) -> List[Document]:
    """
    Oracle 테이블 스키마 딕셔너리 → Document 리스트 변환

    [청킹 전략]
    테이블 요약 1개 + 컬럼 10개씩 묶음
    → 너무 길면 임베딩 품질 저하 / 너무 짧으면 컨텍스트 부족

    Args:
        schema:     {"comment": str, "columns": [...]} 형태
        table_name: 테이블명 (대문자)

    Returns:
        Document 리스트
    """
    docs: List[Document] = []
    comment = schema.get("comment", "") or ""
    columns: List[Dict] = schema.get("columns", [])

    # 1. 테이블 요약 (검색 핵심 문서)
    col_summary = ", ".join(
        f"{c['name']}({c.get('comment') or c['name']})" for c in columns[:25]
    )
    docs.append(
        Document(
            page_content=(
                f"테이블: {table_name}\n"
                f"설명: {comment or '(설명 없음)'}\n"
                f"컬럼: {col_summary}"
            ),
            metadata={
                "type": "table_schema",
                "table": table_name,
                "subtype": "summary",
            },
        )
    )

    # 2. 컬럼 상세 (10개씩)
    for i in range(0, len(columns), 10):
        batch = columns[i : i + 10]
        lines = [f"[{table_name}] 컬럼 상세 ({i + 1}~{i + len(batch)}번)"]
        for c in batch:
            lines.append(
                f"  {c['name']} {c.get('type', '')} "
                + ("NULL가능" if c.get("nullable") == "Y" else "NOT NULL")
                + (f" — {c['comment']}" if c.get("comment") else "")
            )
        docs.append(
            Document(
                page_content="\n".join(lines),
                metadata={
                    "type": "table_schema",
                    "table": table_name,
                    "subtype": "columns",
                },
            )
        )

    return docs


def _example_to_doc(
    intent: str,
    sql: str,
    table: str = "",
    description: str = "",
) -> Document:
    """
    예시 쿼리 → Document 변환

    Args:
        intent:      쿼리 의도 (예: "현재 입원 환자 목록")
        sql:         SQL 문자열 (ROWNUM 포함 권장)
        table:       주 테이블명
        description: 추가 설명

    Returns:
        Document
    """
    content = (
        f"[예시 쿼리] {intent}\n"
        f"테이블: {table}\n"
        + (f"설명: {description}\n" if description else "")
        + f"SQL:\n{sql}"
    )
    return Document(
        page_content=content,
        metadata={
            "type": "example_query",
            "table": table,
            "query_intent": intent,
            "sql": sql,
        },
    )


# ═══════════════════════════════════════════════════════════════════
#  빌드 / 로드
# ═══════════════════════════════════════════════════════════════════


def build_schema_db(
    table_schemas: Optional[Dict[str, Any]] = None,
    example_queries: Optional[List[Dict]] = None,
    force: bool = False,
) -> bool:
    """
    테이블 명세 + 예시 쿼리를 벡터화하여 schema_db/ 에 저장합니다.

    Args:
        table_schemas:   {테이블명: schema_dict} (None = 자동 수집)
        example_queries: [{"intent":str, "sql":str, "table":str}] (None = 자동)
        force:           True = 기존 DB 덮어씀

    Returns:
        True = 성공
    """
    global _cached_db

    if _SCHEMA_DB_DIR.exists() and not force:
        logger.info("schema_db 이미 존재 (force=False 스킵)")
        return True

    docs: List[Document] = []

    # 테이블 스키마 수집
    if table_schemas is None:
        table_schemas = _collect_schemas()
    for tbl, sch in (table_schemas or {}).items():
        docs.extend(_schema_to_docs(sch, tbl.upper()))

    # 예시 쿼리 수집
    if example_queries is None:
        example_queries = _collect_examples()
    for eq in example_queries or []:
        docs.append(
            _example_to_doc(
                intent=eq.get("intent", ""),
                sql=eq.get("sql", ""),
                table=eq.get("table", ""),
                description=eq.get("description", ""),
            )
        )

    if not docs:
        logger.warning("schema_db: 빌드할 문서 없음")
        return False

    try:
        emb = get_embeddings_auto()
        db = FAISS.from_documents(docs, emb)
        _SCHEMA_DB_DIR.mkdir(parents=True, exist_ok=True)
        db.save_local(str(_SCHEMA_DB_DIR))
        _cached_db = db
        logger.info(f"schema_db 빌드 완료: {len(docs)}개 문서 → {_SCHEMA_DB_DIR}")
        return True
    except Exception as exc:
        logger.error(f"schema_db 빌드 실패: {exc}", exc_info=True)
        return False


def get_schema_db() -> Optional[FAISS]:
    """
    schema_db FAISS 인스턴스를 반환합니다. (메모리 캐시)

    Returns:
        FAISS 또는 None (미구축)
    """
    global _cached_db

    if _cached_db is not None:
        return _cached_db

    if not _SCHEMA_DB_DIR.exists():
        logger.warning("schema_db 없음 → build_schema_db() 먼저 호출하세요.")
        return None

    try:
        emb = get_embeddings_auto()
        _cached_db = FAISS.load_local(
            str(_SCHEMA_DB_DIR),
            emb,
            allow_dangerous_deserialization=True,
        )
        logger.info(f"schema_db 로드: {_SCHEMA_DB_DIR}")
        return _cached_db
    except Exception as exc:
        logger.error(f"schema_db 로드 실패: {exc}", exc_info=True)
        return None


# ═══════════════════════════════════════════════════════════════════
#  검색 API
# ═══════════════════════════════════════════════════════════════════


def search_schema_context(
    user_question: str,
    k_tables: int = 3,
    k_examples: int = 2,
) -> str:
    """
    사용자 질문과 관련된 테이블 명세 + 예시 쿼리를 검색하여
    LLM 프롬프트에 주입할 컨텍스트 문자열을 반환합니다.

    [사용 위치]
    sql_generator.py / _build_table_schema() 에서 호출

    Args:
        user_question: 사용자 자연어 질문
        k_tables:      테이블 명세 검색 수
        k_examples:    예시 쿼리 검색 수

    Returns:
        마크다운 컨텍스트 문자열 (없으면 "")

    Example::
        ctx = search_schema_context("현재 입원 환자 목록")
        # → "## 관련 테이블 명세\n테이블: OMTIDN02\n..."
    """
    db = get_schema_db()
    if db is None:
        return ""

    sections: List[str] = []

    # 테이블 명세 검색
    try:
        table_docs = db.similarity_search(
            user_question,
            k=k_tables,
            filter={"type": "table_schema"},
        )
        if table_docs:
            sections.append("## 관련 테이블 명세")
            for doc in table_docs:
                sections.append(doc.page_content)
    except Exception as exc:
        logger.warning(f"테이블 명세 검색 실패: {exc}")

    # 예시 쿼리 검색
    try:
        example_docs = db.similarity_search(
            user_question,
            k=k_examples,
            filter={"type": "example_query"},
        )
        if example_docs:
            sections.append("\n## 참고 예시 쿼리")
            for doc in example_docs:
                sections.append(doc.page_content)
    except Exception as exc:
        logger.warning(f"예시 쿼리 검색 실패: {exc}")

    result = "\n\n".join(sections)
    if result:
        logger.debug(f"schema 컨텍스트 {len(result)}자 반환: '{user_question[:30]}'")
    return result


def add_example_query(
    intent: str,
    sql: str,
    table: str = "",
    description: str = "",
) -> bool:
    """
    예시 쿼리를 schema_db에 추가합니다.

    기존 DB에 병합 저장합니다. 없으면 새로 생성합니다.

    Args:
        intent:      쿼리 의도 (예: "현재 빈 병상 조회")
        sql:         SQL (ROWNUM 포함 권장)
        table:       주 테이블명
        description: 추가 설명

    Returns:
        True = 성공

    Example::
        add_example_query(
            intent="현재 입원 환자 수",
            sql="SELECT COUNT(*) FROM (SELECT * FROM JAIN_WM.OMTIDN02 "
                "WHERE OMT02USEFLAG='Y') WHERE ROWNUM <= 1",
            table="OMTIDN02",
        )
    """
    global _cached_db

    doc = _example_to_doc(intent, sql, table, description)
    db = get_schema_db()

    if db is None:
        # DB 없음 → 새로 생성
        return build_schema_db(
            table_schemas={},
            example_queries=[
                {
                    "intent": intent,
                    "sql": sql,
                    "table": table,
                    "description": description,
                }
            ],
            force=True,
        )

    try:
        emb = get_embeddings_auto()
        new_db = FAISS.from_documents([doc], emb)
        db.merge_from(new_db)
        db.save_local(str(_SCHEMA_DB_DIR))
        _cached_db = db
        logger.info(f"예시 쿼리 추가: '{intent[:40]}'")
        return True
    except Exception as exc:
        logger.error(f"예시 쿼리 추가 실패: {exc}", exc_info=True)
        return False


def rebuild_from_oracle() -> bool:
    """
    Oracle에서 스키마를 새로 조회하여 schema_db를 재구축합니다.

    관리자가 테이블 구조 변경 후 호출합니다.

    Returns:
        True = 성공
    """
    global _cached_db
    _cached_db = None  # 캐시 초기화

    schemas = _collect_schemas()
    examples = _collect_examples()
    return build_schema_db(schemas, examples, force=True)


# ═══════════════════════════════════════════════════════════════════
#  내부 수집 유틸
# ═══════════════════════════════════════════════════════════════════


def _collect_schemas() -> Dict[str, Any]:
    """Oracle / settings 에서 테이블 스키마 자동 수집"""
    schemas: Dict[str, Any] = {}

    # 1순위: Oracle 실시간
    try:
        from db.oracle_client import get_table_schema

        raw_wl = getattr(settings, "oracle_whitelist_tables", [])
        wl = [t.upper().strip() for t in raw_wl if t.strip()] if raw_wl else None
        result = get_table_schema(table_names=wl)
        if result:
            schemas.update(result)
            logger.info(f"Oracle 스키마: {len(result)}개 테이블")
    except Exception as exc:
        logger.warning(f"Oracle 스키마 수집 실패: {exc}")

    # 2순위: settings 수동
    if not schemas:
        desc = getattr(settings, "oracle_table_descriptions", {})
        if isinstance(desc, dict) and desc:
            for tbl, d in desc.items():
                schemas[tbl] = {
                    "comment": "",
                    "columns": [
                        {"name": k, "type": "VARCHAR2", "nullable": "Y", "comment": v}
                        for k, v in (d if isinstance(d, dict) else {}).items()
                    ],
                }
            logger.info(f"settings 스키마: {len(schemas)}개 테이블")

    return schemas


def _collect_examples() -> List[Dict]:
    """settings / .env 에서 예시 쿼리 수집

    .env 설정 예시:
        ORACLE_EXAMPLE_QUERIES='[
          {"intent":"입원 환자 목록","sql":"SELECT ...","table":"OMTIDN02"},
          {"intent":"빈 병상 현황","sql":"SELECT ...","table":"OMTIDN02"}
        ]'
    """
    raw = getattr(settings, "oracle_example_queries", None)
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            logger.warning("ORACLE_EXAMPLE_QUERIES JSON 파싱 실패")
    return []
