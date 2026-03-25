"""
db/knowledge_db_builder.py  ─  쿼리 예제 + 개발 문서 벡터 DB 빌더 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[전산팀 관점 설계 원칙]

전산팀이 보유한 두 가지 지식 자산을 SQL 생성 LLM에 연결합니다.

1. 쿼리 예제집 (query_db/)
   ─────────────────────────────
   · 문제: LLM이 처음부터 SQL을 만들면 조인 조건, 코드값, 날짜 포맷을 모름
   · 해결: 전산팀 검증 완료 SQL + 용도 설명을 벡터화
   · 효과: 유사 질문 시 기존 쿼리 패턴 참조 → 오류율 대폭 감소
   
   파일 형식 (.sql):
   ─────────────────
   -- [쿼리명] 응급환자 오늘 내원 현황
   -- [설명]   당일 응급실 내원 환자를 중증도/결과 기준으로 조회
   -- [태그]   응급환자, EMIHPTMI, 중증도, 내원
   SELECT PTMIINDT, PTMIKTS1, PTMIEMRT, COUNT(*)
   FROM JAIN_OCS.EMIHPTMI
   WHERE PTMIINDT = TO_CHAR(SYSDATE,'YYYYMMDD')
   GROUP BY PTMIINDT, PTMIKTS1, PTMIEMRT

2. 개발 문서 (doc_db/)
   ─────────────────────────────
   · 문제: 코드표(60=구급차, 11=귀가) 없으면 LLM이 코드값 의미 모름
   · 해결: 테이블 명세서, 코드표, ERD 설명 등을 벡터화
   · 효과: "구급차로 온 환자" → PTMIINMN=60 자동 변환

   파일 형식 (.md):
   ─────────────────
   ## EMIHPTMI 응급환자 마스터 테이블
   ### PTMIINMN (내원수단 코드)
   | 코드 | 의미       |
   |------|------------|
   | 60   | 구급차 내원 |
   | 30   | 도보/자가내원|
   | 20   | 자가용 이송 |

[벡터 통합 흐름]

   SQL 생성 LLM 프롬프트
   ┌─────────────────────────────────────────────────────────┐
   │ 1. RAG_ACCESS_CONFIG (테이블명/컬럼/마스킹) — DB 직접  │
   │ 2. schema_db/ (Oracle 컬럼 + 코멘트 벡터)              │
   │ 3. query_db/  (유사 쿼리 예제)           ← 이 모듈    │
   │ 4. doc_db/    (코드표 + 명세 문서)        ← 이 모듈    │
   └─────────────────────────────────────────────────────────┘

[보안 검토 — 전산팀장 확인사항]

   전송되는 정보:
   · 쿼리 예제: SQL 구조만 (실제 환자 데이터 없음) ✅ 안전
   · 개발 문서: 테이블 구조 + 코드표 (개인정보 없음) ✅ 안전
   · Oracle 코멘트: 컬럼 설명만 (데이터 아님) ✅ 안전
   
   전송 금지 항목 (이 모듈에서 자동 차단):
   · 실제 쿼리 결과 데이터 ❌
   · 환자명/주민번호 포함 내용 ❌
   · 접속 정보(IP/PW) ❌

[실행]
  python -m db.knowledge_db_builder            # 전체 구축
  python -m db.knowledge_db_builder --force    # 강제 재구축
  python -m db.knowledge_db_builder --query-only  # 쿼리 예제만
  python -m db.knowledge_db_builder --doc-only    # 개발 문서만
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  경로 상수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_BASE          = Path(settings.rag_db_path)                   # guidbot/vector_store/
_DOCS_ROOT     = _BASE.parent / "docs"                        # guidbot/docs/
_QUERY_LIB_DIR = _DOCS_ROOT / "query_library"                 # guidbot/docs/query_library/
_DB_SPEC_DIR   = _DOCS_ROOT / "db_specs"                      # guidbot/docs/db_specs/

_QUERY_DB_DIR  = _BASE / "query_db"                           # vector_store/query_db/
_DOC_DB_DIR    = _BASE / "doc_db"                             # vector_store/doc_db/

# 청크 설정 (쿼리 예제는 SQL 단위로 분리 — 작은 청크)
_QUERY_CHUNK_SIZE    = 600
_QUERY_CHUNK_OVERLAP = 80
_DOC_CHUNK_SIZE      = 900
_DOC_CHUNK_OVERLAP   = 120


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  쿼리 예제 파서
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_sql_file(path: Path) -> List[Dict[str, Any]]:
    """
    .sql 파일에서 쿼리 예제를 파싱합니다.

    [지원 형식 1] 헤더 주석 형식 (권장):
    ─────────────────────────────────────
    -- [쿼리명] 응급환자 오늘 내원 현황
    -- [설명]   당일 응급실 내원 환자 조회
    -- [태그]   응급환자, EMIHPTMI, 중증도
    SELECT ...

    [지원 형식 2] 일반 주석 (자동 제목 추출):
    ─────────────────────────────────────
    -- 응급환자 조회 쿼리
    SELECT ...

    Returns:
        [{"title": str, "description": str, "tags": list, "sql": str}]
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    entries = []

    # 구분자 (빈 줄 2개 이상 또는 --- 구분선)로 복수 쿼리 분리
    blocks = re.split(r"\n{3,}|^---+$", text, flags=re.MULTILINE)

    for block in blocks:
        block = block.strip()
        if not block or not re.search(r"\bSELECT\b", block, re.IGNORECASE):
            continue

        title = ""
        description = ""
        tags = []

        # 헤더 파싱
        for line in block.splitlines():
            line = line.strip()
            m_name = re.match(r"--\s*\[쿼리명\]\s*(.+)", line)
            m_desc = re.match(r"--\s*\[설명\]\s*(.+)", line)
            m_tags = re.match(r"--\s*\[태그\]\s*(.+)", line)
            m_gen  = re.match(r"--\s+(.+)", line)

            if m_name:   title       = m_name.group(1).strip()
            elif m_desc: description = m_desc.group(1).strip()
            elif m_tags: tags        = [t.strip() for t in m_tags.group(1).split(",")]
            elif m_gen and not title:
                title = m_gen.group(1).strip()[:60]

        # SQL 본문 추출 (주석 제거)
        sql_lines = [
            l for l in block.splitlines()
            if not l.strip().startswith("--")
        ]
        sql = "\n".join(sql_lines).strip()

        if not sql or not title:
            # 제목이 없으면 FROM 절 테이블명으로 자동 생성
            fm = re.search(r"\bFROM\s+([\w.]+)", sql, re.IGNORECASE)
            title = title or (f"{fm.group(1)} 조회" if fm else "쿼리 예제")

        # 테이블명 자동 태그
        table_matches = re.findall(r"\bFROM\s+([\w.]+)", sql, re.IGNORECASE)
        table_matches += re.findall(r"\bJOIN\s+([\w.]+)", sql, re.IGNORECASE)
        auto_tags = list({m.split(".")[-1].upper() for m in table_matches})
        tags = list(set(tags + auto_tags))

        entries.append({
            "title":       title,
            "description": description,
            "tags":        tags,
            "sql":         sql,
            "source_file": path.name,
        })

    return entries


def _build_query_documents(query_dir: Path) -> List[Document]:
    """
    query_library/ 디렉토리의 .sql, .md 파일에서 Document 생성.

    [Document 구조]
    page_content = 검색에 사용될 텍스트
                   "쿼리명: ...\n설명: ...\n태그: ...\nSQL:\n..."
    metadata     = {source, type, title, tags, tables}
    """
    docs = []
    sql_files = list(query_dir.glob("**/*.sql")) + list(query_dir.glob("**/*.md"))

    if not sql_files:
        logger.warning(f"쿼리 예제 파일 없음: {query_dir}")
        return []

    for fpath in sql_files:
        if fpath.suffix == ".sql":
            entries = _parse_sql_file(fpath)
            for entry in entries:
                content = (
                    f"쿼리명: {entry['title']}\n"
                    f"설명: {entry['description']}\n"
                    f"태그: {', '.join(entry['tags'])}\n"
                    f"관련 테이블: {', '.join([t for t in entry['tags'] if t.isupper()])}\n"
                    f"\nSQL:\n{entry['sql']}"
                )
                docs.append(Document(
                    page_content=content,
                    metadata={
                        "source":    str(fpath),
                        "type":      "query_example",
                        "title":     entry["title"],
                        "tags":      entry["tags"],
                        "tables":    [t for t in entry["tags"] if t.isupper()],
                        "file_name": fpath.name,
                    }
                ))
        else:
            # .md 파일은 헤더 기반 청크
            from langchain_text_splitters import MarkdownHeaderTextSplitter
            text = fpath.read_text(encoding="utf-8", errors="replace")
            splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=[("##", "section"), ("###", "subsection")]
            )
            md_docs = splitter.split_text(text)
            for d in md_docs:
                d.metadata.update({"source": str(fpath), "type": "query_doc"})
                docs.append(d)

    logger.info(f"쿼리 예제 Document: {len(docs)}개 ({len(sql_files)}개 파일)")
    return docs


def _build_doc_documents(doc_dir: Path) -> List[Document]:
    """
    db_specs/ 디렉토리의 개발 문서에서 Document 생성.

    [지원 형식]
    · .md  — 테이블 명세서, 코드표, ERD 설명
    · .txt — 일반 텍스트 문서
    · .sql — SQL 스키마 정의 (CREATE TABLE 등)
    """
    from langchain_text_splitters import (
        MarkdownHeaderTextSplitter,
        RecursiveCharacterTextSplitter,
    )
    docs = []
    files = (
        list(doc_dir.glob("**/*.md"))
        + list(doc_dir.glob("**/*.txt"))
        + list(doc_dir.glob("**/*.sql"))
    )

    if not files:
        logger.warning(f"개발 문서 파일 없음: {doc_dir}")
        return []

    for fpath in files:
        text = fpath.read_text(encoding="utf-8", errors="replace")

        if fpath.suffix == ".md":
            splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=[("##", "table"), ("###", "column")]
            )
            md_docs = splitter.split_text(text)
            for d in md_docs:
                d.metadata.update({
                    "source":    str(fpath),
                    "type":      "db_spec",
                    "file_name": fpath.name,
                })
                docs.append(d)
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=_DOC_CHUNK_SIZE,
                chunk_overlap=_DOC_CHUNK_OVERLAP,
            )
            chunks = splitter.create_documents(
                [text],
                metadatas=[{"source": str(fpath), "type": "db_spec", "file_name": fpath.name}],
            )
            docs.extend(chunks)

    logger.info(f"개발 문서 Document: {len(docs)}개 ({len(files)}개 파일)")
    return docs


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  벡터 DB 저장
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _save_faiss(docs: List[Document], save_dir: Path, label: str) -> bool:
    """Document 리스트를 FAISS 인덱스로 저장."""
    if not docs:
        logger.warning(f"{label}: 저장할 Document 없음")
        return False
    try:
        from core.embeddings import get_embeddings_auto
        from langchain_community.vectorstores import FAISS

        save_dir.mkdir(parents=True, exist_ok=True)
        embeddings = get_embeddings_auto()
        vdb = FAISS.from_documents(docs, embeddings)
        vdb.save_local(str(save_dir))
        logger.info(f"{label} 저장 완료: {len(docs)}개 → {save_dir}")
        return True
    except Exception as exc:
        logger.error(f"{label} 저장 실패: {exc}", exc_info=True)
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공개 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_query_db(force: bool = False) -> bool:
    """
    쿼리 예제 벡터 DB 구축 (query_db/).

    Args:
        force: True 이면 기존 인덱스 삭제 후 재구축

    Returns:
        성공 여부
    """
    _QUERY_LIB_DIR.mkdir(parents=True, exist_ok=True)

    if not force and (_QUERY_DB_DIR / "index.faiss").exists():
        logger.info(f"query_db 이미 존재 (--force 로 재구축): {_QUERY_DB_DIR}")
        return True

    docs = _build_query_documents(_QUERY_LIB_DIR)
    if not docs:
        logger.warning("쿼리 예제 없음 — query_library/ 폴더에 .sql 파일을 추가하세요")
        return False

    return _save_faiss(docs, _QUERY_DB_DIR, "query_db")


def build_doc_db(force: bool = False) -> bool:
    """
    개발 문서 벡터 DB 구축 (doc_db/).

    Args:
        force: True 이면 기존 인덱스 삭제 후 재구축

    Returns:
        성공 여부
    """
    _DB_SPEC_DIR.mkdir(parents=True, exist_ok=True)

    if not force and (_DOC_DB_DIR / "index.faiss").exists():
        logger.info(f"doc_db 이미 존재 (--force 로 재구축): {_DOC_DB_DIR}")
        return True

    docs = _build_doc_documents(_DB_SPEC_DIR)
    if not docs:
        logger.warning("개발 문서 없음 — db_specs/ 폴더에 .md 파일을 추가하세요")
        return False

    return _save_faiss(docs, _DOC_DB_DIR, "doc_db")


def search_query_examples(question: str, k: int = 3) -> str:
    """
    질문과 유사한 쿼리 예제를 검색합니다.
    
    SQL 생성 LLM 프롬프트에 포함할 예제를 반환합니다.

    Returns:
        마크다운 형식의 쿼리 예제 텍스트 (없으면 빈 문자열)
    """
    try:
        from core.embeddings import get_embeddings_auto
        from langchain_community.vectorstores import FAISS

        if not (_QUERY_DB_DIR / "index.faiss").exists():
            return ""

        embeddings = get_embeddings_auto()
        vdb = FAISS.load_local(
            str(_QUERY_DB_DIR), embeddings,
            allow_dangerous_deserialization=True,
        )
        results = vdb.similarity_search_with_score(question, k=k)

        if not results:
            return ""

        lines = ["## 유사 쿼리 예제 (참고용)"]
        for doc, score in results:
            if score > 1.5:  # 유사도 임계값 (낮을수록 유사)
                continue
            lines.append(f"\n### {doc.metadata.get('title', '쿼리 예제')}")
            lines.append(doc.page_content)
            lines.append("")

        return "\n".join(lines) if len(lines) > 1 else ""

    except Exception as exc:
        logger.debug(f"query_db 검색 실패 (무시): {exc}")
        return ""


def search_doc_knowledge(question: str, k: int = 3) -> str:
    """
    질문과 관련된 개발 문서(코드표, 명세서)를 검색합니다.

    Returns:
        마크다운 형식의 문서 텍스트 (없으면 빈 문자열)
    """
    try:
        from core.embeddings import get_embeddings_auto
        from langchain_community.vectorstores import FAISS

        if not (_DOC_DB_DIR / "index.faiss").exists():
            return ""

        embeddings = get_embeddings_auto()
        vdb = FAISS.load_local(
            str(_DOC_DB_DIR), embeddings,
            allow_dangerous_deserialization=True,
        )
        results = vdb.similarity_search_with_score(question, k=k)

        if not results:
            return ""

        lines = ["## 관련 개발 문서 (코드표/명세)"]
        for doc, score in results:
            if score > 1.8:
                continue
            src = Path(doc.metadata.get("source", "")).name
            lines.append(f"\n> 출처: {src}")
            lines.append(doc.page_content)
            lines.append("")

        return "\n".join(lines) if len(lines) > 1 else ""

    except Exception as exc:
        logger.debug(f"doc_db 검색 실패 (무시): {exc}")
        return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CLI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="쿼리 예제 + 개발 문서 벡터 DB 구축")
    parser.add_argument("--force",      action="store_true", help="강제 재구축")
    parser.add_argument("--query-only", action="store_true", help="쿼리 예제만")
    parser.add_argument("--doc-only",   action="store_true", help="개발 문서만")
    args = parser.parse_args()

    if not args.doc_only:
        ok_q = build_query_db(force=args.force)
        print(f"query_db: {'✅ 완료' if ok_q else '⚠️ 파일 없음'}")

    if not args.query_only:
        ok_d = build_doc_db(force=args.force)
        print(f"doc_db:   {'✅ 완료' if ok_d else '⚠️ 파일 없음'}")