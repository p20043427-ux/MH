"""
db/schema_oracle_loader.py  ─  Oracle 스키마 → 벡터DB 저장 (v1.1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v1.1 버그 수정]

  Fix 1: No module named 'rag'
    · 수정 전: from core.embeddings import get_embeddings_auto  ← 없는 경로
    · 수정 후: from core.embeddings import get_embeddings_auto  ← 실제 경로

  Fix 2: ORA-01008 not all variables bound
    · 수정 전: SQL에 :schema 바인드 변수를 f-string 으로 치환 후 execute_query 에 params 미전달
    · 수정 후: 바인드 변수 완전 제거, 테이블명도 리터럴 IN ('T1','T2') 로 직접 삽입

  Fix 3: ORA-00942 RAG_ACCESS_CONFIG 없음
    · 이 에러는 코드 문제가 아니라 DDL 미실행
    · oracle_access_config.py 의 .env 폴백이 정상 동작하므로
      RAG_ACCESS_CONFIG 없어도 벡터DB 구축 가능하도록 폴백 처리

[벡터DB 저장 경로]
  settings.rag_db_path.parent / "schema_db"
  = D:/MH/guidbot/vector_store/schema_db/

[업로드 되는 정보 경로 (우선순위)]
  1. JAIN_WM.RAG_ACCESS_CONFIG (TABLE_DESC, COLUMN_DESCS)  ← DBeaver 등록 정보
  2. Oracle ALL_TAB_COLUMNS + ALL_COL_COMMENTS              ← DB 자동 추출
  두 소스를 병합하여 Document 생성 (RAG_ACCESS_CONFIG 우선)

[실행]
  python -m db.schema_oracle_loader           # 최초 구축
  python -m db.schema_oracle_loader --force   # 재구축
  python -m db.schema_oracle_loader --show-docs  # 미리보기
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# ──────────────────────────────────────────────────────────────────────
#  저장 경로 (schema_vector_store.py 와 동일한 경로 사용)
#  D:\MH\guidbot\vector_store\schema_db\
# ──────────────────────────────────────────────────────────────────────

# [경로 수정] .parent 제거
SCHEMA_DB_DIR = Path(settings.rag_db_path) / "schema_db"


# ──────────────────────────────────────────────────────────────────────
#  Oracle 스키마 조회 SQL
#
#  [Fix 2] :schema 바인드 변수 완전 제거
#  execute_query() 는 내부적으로 cursor.execute(sql, params) 를 사용하는데
#  SQL 문자열에 :schema 가 남아있고 params 에 없으면 ORA-01008 발생.
#  → 테이블명과 스키마명을 f-string 으로 직접 삽입.
#  → execute_query 호출 시 params 불필요.
# ──────────────────────────────────────────────────────────────────────


def _make_column_sql(schema: str, table_names: List[str]) -> str:
    """
    ALL_TAB_COLUMNS + ALL_COL_COMMENTS 조회 SQL 생성.
    테이블명을 IN 절에 리터럴로 삽입하여 바인드 변수 문제 회피.
    """
    tbl_list = ", ".join(f"'{t}'" for t in table_names)
    return f"""
SELECT
    c.TABLE_NAME,
    c.COLUMN_NAME,
    c.DATA_TYPE,
    c.NULLABLE,
    c.DATA_LENGTH,
    c.COLUMN_ID,
    NVL(cc.COMMENTS, ' ') AS COL_COMMENT
FROM ALL_TAB_COLUMNS c
LEFT JOIN ALL_COL_COMMENTS cc
    ON  cc.OWNER       = '{schema}'
    AND cc.TABLE_NAME  = c.TABLE_NAME
    AND cc.COLUMN_NAME = c.COLUMN_NAME
WHERE c.OWNER      = '{schema}'
  AND c.TABLE_NAME IN ({tbl_list})
ORDER BY c.TABLE_NAME, c.COLUMN_ID
"""


def _make_table_comment_sql(schema: str, table_names: List[str]) -> str:
    """ALL_TAB_COMMENTS 조회 SQL 생성."""
    tbl_list = ", ".join(f"'{t}'" for t in table_names)
    return f"""
SELECT
    t.TABLE_NAME,
    NVL(tc.COMMENTS, ' ') AS TABLE_COMMENT
FROM ALL_TABLES t
LEFT JOIN ALL_TAB_COMMENTS tc
    ON  tc.OWNER      = '{schema}'
    AND tc.TABLE_NAME = t.TABLE_NAME
WHERE t.OWNER      = '{schema}'
  AND t.TABLE_NAME IN ({tbl_list})
"""


# ──────────────────────────────────────────────────────────────────────
#  Oracle 스키마 조회
# ──────────────────────────────────────────────────────────────────────


def _fetch_oracle_schema(
    table_names: List[str],
    schema: str,
) -> Dict[str, Dict[str, Any]]:
    """
    Oracle ALL_TAB_COLUMNS + ALL_COL_COMMENTS 에서 스키마 정보를 조회합니다.

    [v1.1 수정]
    · :schema 바인드 변수 제거 → f-string 으로 직접 삽입
    · execute_query 에 params 전달 불필요
    · ROWNUM 으로 결과 제한 (Oracle 11g 호환)

    Returns:
        {테이블명: {"comment": str, "columns": [...]}}
        조회 실패 시 빈 dict 반환 (벡터DB 구축은 계속)
    """
    if not table_names:
        return {}

    result: Dict[str, Dict[str, Any]] = {
        t: {"comment": "", "columns": []} for t in table_names
    }

    try:
        from db.oracle_client import execute_query

        # ── 테이블 코멘트 조회 ───────────────────────────────────────
        tbl_sql = _make_table_comment_sql(schema, table_names)
        tbl_sql_with_rownum = f"SELECT * FROM ({tbl_sql}) WHERE ROWNUM <= 500"
        tbl_rows = execute_query(sql=tbl_sql_with_rownum, max_rows=500) or []

        for row in tbl_rows:
            if isinstance(row, dict):
                tbl = row.get("TABLE_NAME", "")
                comm = str(row.get("TABLE_COMMENT") or "")
            elif isinstance(row, (tuple, list)) and len(row) >= 2:
                tbl, comm = str(row[0]), str(row[1] or "")
            else:
                continue
            if tbl in result:
                result[tbl]["comment"] = comm

        # ── 컬럼 정보 조회 ───────────────────────────────────────────
        col_sql = _make_column_sql(schema, table_names)
        col_sql_with_rownum = f"SELECT * FROM ({col_sql}) WHERE ROWNUM <= 5000"
        col_rows = execute_query(sql=col_sql_with_rownum, max_rows=5000) or []

        for row in col_rows:
            if isinstance(row, dict):
                tbl = row.get("TABLE_NAME", "")
                col = row.get("COLUMN_NAME", "")
                typ = row.get("DATA_TYPE", "")
                null = row.get("NULLABLE", "Y")
                comm = str(row.get("COL_COMMENT") or "")
            elif isinstance(row, (tuple, list)) and len(row) >= 5:
                tbl, col, typ = str(row[0]), str(row[1]), str(row[2])
                null = str(row[3])
                comm = str(row[6]) if len(row) > 6 else ""
            else:
                continue
            if tbl in result:
                result[tbl]["columns"].append(
                    {
                        "name": col,
                        "type": typ,
                        "nullable": null,
                        "comment": comm,
                    }
                )

        logger.info(
            f"Oracle 스키마 조회 완료: {len(table_names)}개 테이블, "
            f"총 컬럼={sum(len(v['columns']) for v in result.values())}"
        )

    except Exception as exc:
        logger.warning(
            f"Oracle 스키마 조회 실패 (RAG_ACCESS_CONFIG 정보만 사용): {exc}"
        )

    return result


# ──────────────────────────────────────────────────────────────────────
#  Document 생성
# ──────────────────────────────────────────────────────────────────────


def _build_table_document(
    table_name: str,
    alias: str,
    table_desc: str,
    table_comment: str,
    column_descs: Dict[str, str],
    oracle_columns: List[Dict],
    mask_columns: set,
) -> Document:
    """
    테이블 개요 Document 생성.

    [정보 우선순위]
    설명:   RAG_ACCESS_CONFIG.TABLE_DESC  >  Oracle ALL_TAB_COMMENTS
    컬럼:   RAG_ACCESS_CONFIG.COLUMN_DESCS  병합  Oracle ALL_TAB_COLUMNS
    """
    desc = table_desc or table_comment or ""
    title = alias or table_name

    lines = [
        f"테이블명: {table_name}",
        f"별칭: {title}",
    ]
    if desc:
        lines.append(f"설명: {desc}")

    # 컬럼 목록 병합
    oracle_col_map = {c["name"]: c for c in oracle_columns}
    # RAG_ACCESS_CONFIG 컬럼 먼저, 그 다음 Oracle 전용 컬럼
    all_col_names = list(column_descs.keys()) + [
        c for c in oracle_col_map if c not in column_descs
    ]

    if all_col_names:
        lines.append("\n컬럼 목록:")
        for col in all_col_names:
            col_desc = column_descs.get(col, "")
            oracle_info = oracle_col_map.get(col, {})
            col_type = oracle_info.get("type", "")
            ora_comment = oracle_info.get("comment", "")
            final_desc = col_desc or ora_comment or ""
            pii_mark = " [PII-마스킹]" if col.lower() in mask_columns else ""

            line = f"  - {col}"
            if col_type:
                line += f" ({col_type})"
            if final_desc:
                line += f": {final_desc}"
            line += pii_mark
            lines.append(line)

    return Document(
        page_content="\n".join(lines),
        metadata={
            "source": "oracle_schema",
            "table_name": table_name,
            "alias": alias,
            "doc_type": "table_overview",
            "column_count": len(all_col_names),
        },
    )


def _build_column_documents(
    table_name: str,
    alias: str,
    column_descs: Dict[str, str],
    oracle_columns: List[Dict],
    mask_columns: set,
) -> List[Document]:
    """
    컬럼별 개별 Document 생성.
    "OMT02BLD 가 뭐야?" 같은 컬럼 단위 질문에 대응.

    설명이 없는 컬럼은 노이즈 방지를 위해 Document 미생성.
    """
    docs: List[Document] = []
    oracle_col_map = {c["name"]: c for c in oracle_columns}
    all_cols = {**{c: "" for c in oracle_col_map}, **column_descs}

    for col_name, col_desc in all_cols.items():
        oracle_info = oracle_col_map.get(col_name, {})
        col_type = oracle_info.get("type", "")
        ora_comment = oracle_info.get("comment", "")
        nullable = oracle_info.get("nullable", "Y")
        final_desc = col_desc or ora_comment

        if not final_desc or final_desc.strip() in ("", " "):
            continue  # 설명 없으면 Document 미생성

        pii_note = "⚠️ 개인정보 마스킹 대상" if col_name.lower() in mask_columns else ""
        content = (
            f"테이블: {table_name} ({alias})\n"
            f"컬럼명: {col_name}\n"
            f"데이터타입: {col_type or '미상'}\n"
            f"NULL허용: {'예' if nullable == 'Y' else '아니오'}\n"
            f"설명: {final_desc}\n"
        )
        if pii_note:
            content += f"{pii_note}\n"

        docs.append(
            Document(
                page_content=content,
                metadata={
                    "source": "oracle_schema",
                    "table_name": table_name,
                    "column_name": col_name,
                    "doc_type": "column_detail",
                    "is_pii": col_name.lower() in mask_columns,
                },
            )
        )

    return docs


# ──────────────────────────────────────────────────────────────────────
#  공개 API
# ──────────────────────────────────────────────────────────────────────


def build_schema_documents() -> List[Document]:
    """
    RAG_ACCESS_CONFIG + Oracle ALL_TAB_COLUMNS → Document 목록 생성.

    [벡터DB에 올라가는 정보 경로]

    경로 1: JAIN_WM.RAG_ACCESS_CONFIG
      · TABLE_DESC   → 테이블 한국어 설명
      · COLUMN_DESCS → 컬럼별 설명 (JSON)
      · MASK_COLUMNS → PII 컬럼 표시
      · ALIAS        → 테이블 별칭

    경로 2: Oracle ALL_TAB_COLUMNS + ALL_COL_COMMENTS
      · 컬럼명, 데이터타입, NULL허용
      · Oracle DB에 등록된 COMMENT

    → 두 소스 병합 (RAG_ACCESS_CONFIG 설명 우선)
    → LangChain Document 변환
    → schema_db/ FAISS 저장

    RAG_ACCESS_CONFIG 없으면 .env 화이트리스트 테이블로 폴백하여
    Oracle 컬럼 정보만으로 Document 생성.
    """
    from db.oracle_access_config import get_access_config_manager

    manager = get_access_config_manager()
    active_tables = manager.get_all_active()

    if not active_tables:
        logger.warning(
            "활성화된 테이블 없음. "
            "RAG_ACCESS_CONFIG 미등록 시 .env ORACLE_WHITELIST_TABLES 확인."
        )
        return []

    table_names = [t.table_name for t in active_tables]
    schema_name = str(getattr(settings, "oracle_schema", "JAIN_WM"))

    logger.info(
        f"스키마 Document 생성 시작: {len(table_names)}개 테이블 "
        f"({', '.join(table_names)})"
    )

    # Oracle 스키마 조회 (실패해도 계속)
    oracle_schema = _fetch_oracle_schema(table_names, schema_name)

    documents: List[Document] = []

    for cfg in active_tables:
        tbl = cfg.table_name
        oracle_info = oracle_schema.get(tbl, {"comment": "", "columns": []})
        oracle_cols = oracle_info["columns"]
        oracle_comment = oracle_info["comment"]

        # 1) 테이블 개요 Document (테이블당 1개)
        table_doc = _build_table_document(
            table_name=tbl,
            alias=cfg.alias,
            table_desc=cfg.table_desc,
            table_comment=oracle_comment,
            column_descs=cfg.column_descs,
            oracle_columns=oracle_cols,
            mask_columns=cfg.mask_columns,
        )
        documents.append(table_doc)

        # 2) 컬럼별 Document (컬럼당 1개, 설명 있는 것만)
        col_docs = _build_column_documents(
            table_name=tbl,
            alias=cfg.alias,
            column_descs=cfg.column_descs,
            oracle_columns=oracle_cols,
            mask_columns=cfg.mask_columns,
        )
        documents.extend(col_docs)
        logger.debug(f"  {tbl}: 테이블 개요 1개 + 컬럼 {len(col_docs)}개 Document")

    logger.info(
        f"Document 생성 완료: 총 {len(documents)}개 "
        f"(테이블개요 {len(active_tables)}개 + 컬럼상세 {len(documents) - len(active_tables)}개)"
    )
    return documents


def build_schema_vector_db(
    output_dir: Optional[Path] = None,
    force_rebuild: bool = False,
) -> bool:
    """
    스키마 Document → FAISS 벡터DB 저장.

    저장 경로: D:/MH/guidbot/vector_store/schema_db/
    (settings.rag_db_path.parent / "schema_db")

    [Fix 1] from rag.embeddings → from core.embeddings import get_embeddings_auto
    """
    save_dir = output_dir or SCHEMA_DB_DIR
    save_dir.mkdir(parents=True, exist_ok=True)

    index_file = save_dir / "index.faiss"
    if index_file.exists() and not force_rebuild:
        logger.info(f"schema_db 이미 존재: {save_dir}  (--force 로 재구축)")
        return True

    t0 = time.time()
    docs = build_schema_documents()

    if not docs:
        logger.error("생성된 Document 없음 → 벡터DB 구축 중단")
        return False

    try:
        # [Fix 1] 실제 경로: core.embeddings.get_embeddings_auto
        from core.embeddings import get_embeddings_auto
        from langchain_community.vectorstores import FAISS

        embedding_model = get_embeddings_auto()
        texts = [d.page_content for d in docs]
        metadatas = [d.metadata for d in docs]

        vector_store = FAISS.from_texts(
            texts=texts,
            embedding=embedding_model,
            metadatas=metadatas,
        )
        vector_store.save_local(str(save_dir))

        elapsed = time.time() - t0
        logger.info(
            f"schema_db 구축 완료: {len(docs)}개 Document → {save_dir}  ({elapsed:.1f}초)"
        )
        return True

    except Exception as exc:
        logger.error(f"schema_db FAISS 저장 실패: {exc}", exc_info=True)
        return False


def get_schema_context_for_question(
    question: str,
    k: int = 5,
) -> str:
    """
    질문과 관련된 스키마 Document 를 검색하여 컨텍스트 텍스트 반환.
    sql_generator._build_table_schema() 에서 호출됩니다.
    """
    try:
        from core.embeddings import get_embeddings_auto
        from langchain_community.vectorstores import FAISS

        if not (SCHEMA_DB_DIR / "index.faiss").exists():
            logger.info("schema_db 없음 → build_schema_vector_db() 자동 실행")
            build_schema_vector_db()

        if not (SCHEMA_DB_DIR / "index.faiss").exists():
            return ""

        embedding_model = get_embeddings_auto()
        vs = FAISS.load_local(
            str(SCHEMA_DB_DIR),
            embeddings=embedding_model,
            allow_dangerous_deserialization=True,
        )
        results = vs.similarity_search(question, k=k)
        return "\n\n---\n\n".join(doc.page_content for doc in results)

    except Exception as exc:
        logger.warning(f"스키마 벡터 검색 실패: {exc}")
        return ""


# ──────────────────────────────────────────────────────────────────────
#  CLI 진입점
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Oracle 스키마 → 벡터DB 구축")
    parser.add_argument("--force", action="store_true", help="기존 DB 재구축")
    parser.add_argument(
        "--show-docs", action="store_true", help="Document 미리보기 (저장 안 함)"
    )
    args = parser.parse_args()

    if args.show_docs:
        docs = build_schema_documents()
        print(f"\n생성된 Document: {len(docs)}개\n{'=' * 60}")
        for i, doc in enumerate(docs[:10]):
            dtype = doc.metadata.get("doc_type", "?")
            tbl = doc.metadata.get("table_name", "?")
            col = doc.metadata.get("column_name", "")
            label = f"{tbl}.{col}" if col else tbl
            print(f"\n[Doc #{i + 1}] {dtype} | {label}")
            print(doc.page_content[:400])
            if len(doc.page_content) > 400:
                print("  ...")
        print(f"\n(총 {len(docs)}개 중 상위 10개 표시)")
    else:
        print(f"저장 경로: {SCHEMA_DB_DIR}")
        ok = build_schema_vector_db(force_rebuild=args.force)
        if ok:
            print("✅ schema_db 구축 완료")
        else:
            print("❌ 실패 — 로그를 확인하세요")
