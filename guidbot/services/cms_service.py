"""
services/cms_service.py ─ RAG CMS 서비스 레이어 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[역할]
  벡터 DB 관리자 UI(vector_db_admin.py)와 실제 저장소 사이의
  비즈니스 로직 계층입니다.

[저장소 구성]
  ┌─────────────────────────────────────────────────────┐
  │  SQLite (cms.db)   — 문서 메타데이터, 버전, 청크 목록 │
  │  FAISS (faiss_db/) — 벡터 임베딩 (기존 파이프라인)    │
  │  파일시스템 (docs/) — 원본 PDF, 변환된 Markdown       │
  └─────────────────────────────────────────────────────┘

[핵심 설계 원칙]
  · Soft Delete: 물리 삭제 금지, status 컬럼으로 비활성화
  · 버전 관리: 문서 재업로드 시 신규 버전 생성 (기존 deprecated)
  · 중복 감지: MD5 해시 기반 파일 중복 판별
  · 감사 로그: 모든 변경 이력을 audit_log에 기록
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 내부 모듈 (선택적 임포트) ──────────────────────────────────────
try:
    from config.settings import settings
    _SETTINGS_OK = True
except ImportError:
    _SETTINGS_OK = False

import logging
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
#  경로 설정
# ══════════════════════════════════════════════════════════════════════

def _get_base_dir() -> Path:
    """프로젝트 루트 경로 반환."""
    if _SETTINGS_OK:
        try:
            return Path(settings.rag_db_path).parent.parent
        except Exception:
            pass
    return Path(__file__).parent.parent


BASE_DIR   = _get_base_dir()
CMS_DIR    = BASE_DIR / "cms_data"
DB_PATH    = CMS_DIR / "cms.db"
DOCS_DIR   = CMS_DIR / "documents"   # 원본 PDF 저장
MD_DIR     = CMS_DIR / "markdown"    # 변환된 Markdown 저장

for _d in [CMS_DIR, DOCS_DIR, MD_DIR]:
    _d.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════
#  데이터 클래스
# ══════════════════════════════════════════════════════════════════════

@dataclass
class DocumentMeta:
    """문서 메타데이터 — documents 테이블 매핑."""
    document_id: str
    title: str
    version: int
    file_name: str
    file_hash: str           # MD5 해시 (중복 감지)
    status: str              # active / inactive / deprecated
    department: str
    tags: List[str]          # JSON 직렬화 저장
    created_at: str
    updated_at: str
    parent_id: Optional[str] = None   # 이전 버전 document_id
    chunk_count: int = 0
    indexed: bool = False    # 벡터 DB 반영 여부
    description: str = ""

    @property
    def tags_str(self) -> str:
        return ", ".join(self.tags)

    @property
    def status_label(self) -> str:
        return {"active": "✅ 활성", "inactive": "⏸ 비활성",
                "deprecated": "🗑 사용 중단"}.get(self.status, self.status)


@dataclass
class ChunkRecord:
    """청크 레코드 — chunks 테이블 매핑."""
    chunk_id: str
    document_id: str
    chunk_index: int
    content: str
    char_count: int
    page: int
    article: str
    embedding_status: str    # pending / indexed / failed
    created_at: str
    updated_at: str
    is_deleted: bool = False


@dataclass
class AuditLog:
    """감사 로그 — audit_log 테이블 매핑."""
    log_id: str
    document_id: str
    action: str              # upload / version_up / activate / deprecate / delete_chunk / reindex
    detail: str
    performed_by: str
    created_at: str


@dataclass
class SearchResult:
    """검색 테스트 결과."""
    rank: int
    chunk_id: str
    document_id: str
    document_title: str
    chunk_index: int
    content: str
    page: int
    similarity_score: float
    embedding_status: str


@dataclass
class CMSStats:
    """CMS 전체 통계."""
    total_documents: int
    active_documents: int
    deprecated_documents: int
    total_chunks: int
    indexed_chunks: int
    pending_chunks: int
    total_versions: int
    db_size_mb: float
    last_updated: str


# ══════════════════════════════════════════════════════════════════════
#  SQLite 스키마
# ══════════════════════════════════════════════════════════════════════

_SCHEMA_SQL = """
-- 문서 테이블 (메타데이터)
CREATE TABLE IF NOT EXISTS documents (
    document_id  TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    file_name    TEXT NOT NULL,
    file_hash    TEXT NOT NULL,           -- MD5 해시 (중복 감지)
    status       TEXT NOT NULL DEFAULT 'active',  -- active/inactive/deprecated
    department   TEXT DEFAULT '',
    tags         TEXT DEFAULT '[]',       -- JSON 배열
    description  TEXT DEFAULT '',
    parent_id    TEXT,                    -- 이전 버전 document_id
    chunk_count  INTEGER DEFAULT 0,
    indexed      INTEGER DEFAULT 0,       -- 1=벡터DB 반영됨
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES documents(document_id)
);

-- 청크 테이블
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id         TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL,
    chunk_index      INTEGER NOT NULL,
    content          TEXT NOT NULL,
    char_count       INTEGER DEFAULT 0,
    page             INTEGER DEFAULT 0,
    article          TEXT DEFAULT '',
    embedding_status TEXT DEFAULT 'pending',  -- pending/indexed/failed
    is_deleted       INTEGER DEFAULT 0,       -- soft delete
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(document_id)
);

-- 감사 로그
CREATE TABLE IF NOT EXISTS audit_log (
    log_id       TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL,
    action       TEXT NOT NULL,
    detail       TEXT DEFAULT '',
    performed_by TEXT DEFAULT 'admin',
    created_at   TEXT NOT NULL
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_docs_status   ON documents(status);
CREATE INDEX IF NOT EXISTS idx_docs_hash     ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_doc    ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(embedding_status);
CREATE INDEX IF NOT EXISTS idx_audit_doc     ON audit_log(document_id);
"""


# ══════════════════════════════════════════════════════════════════════
#  CMSService 클래스
# ══════════════════════════════════════════════════════════════════════

class CMSService:
    """
    RAG CMS 핵심 서비스.

    [스레드 안전성]
    SQLite는 check_same_thread=False + threading.Lock으로 보호.
    Streamlit은 요청마다 별도 스레드를 사용하므로 필수.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ──────────────────────────────────────────────────────────────
    #  DB 초기화
    # ──────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """SQLite DB 초기화 및 스키마 생성."""
        with self._lock:
            conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
            self._conn = conn
            logger.info(f"CMS DB 초기화: {DB_PATH}")

    def _db(self) -> sqlite3.Connection:
        """DB 연결 반환 (없으면 재연결)."""
        if self._conn is None:
            self._init_db()
        return self._conn  # type: ignore[return-value]

    @staticmethod
    def _now() -> str:
        """현재 시각 ISO 문자열."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _new_id() -> str:
        """UUID 기반 신규 ID (8자 축약)."""
        return str(uuid.uuid4()).replace("-", "")[:16]

    @staticmethod
    def _md5(data: bytes) -> str:
        """MD5 해시 계산."""
        return hashlib.md5(data).hexdigest()

    # ──────────────────────────────────────────────────────────────
    #  내부 헬퍼: Row → Dataclass
    # ──────────────────────────────────────────────────────────────

    def _row_to_doc(self, row: sqlite3.Row) -> DocumentMeta:
        """DB Row → DocumentMeta 변환."""
        return DocumentMeta(
            document_id=row["document_id"],
            title=row["title"],
            version=row["version"],
            file_name=row["file_name"],
            file_hash=row["file_hash"],
            status=row["status"],
            department=row["department"] or "",
            tags=json.loads(row["tags"] or "[]"),
            description=row["description"] or "",
            parent_id=row["parent_id"],
            chunk_count=row["chunk_count"] or 0,
            indexed=bool(row["indexed"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_chunk(self, row: sqlite3.Row) -> ChunkRecord:
        """DB Row → ChunkRecord 변환."""
        return ChunkRecord(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            chunk_index=row["chunk_index"],
            content=row["content"],
            char_count=row["char_count"] or 0,
            page=row["page"] or 0,
            article=row["article"] or "",
            embedding_status=row["embedding_status"],
            is_deleted=bool(row["is_deleted"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ──────────────────────────────────────────────────────────────
    #  감사 로그
    # ──────────────────────────────────────────────────────────────

    def _log(
        self,
        document_id: str,
        action: str,
        detail: str = "",
        by: str = "admin",
    ) -> None:
        """감사 로그 기록 (내부 전용)."""
        try:
            self._db().execute(
                "INSERT INTO audit_log VALUES (?,?,?,?,?,?)",
                (self._new_id(), document_id, action, detail, by, self._now()),
            )
            self._db().commit()
        except Exception as e:
            logger.warning(f"감사 로그 기록 실패: {e}")

    # ──────────────────────────────────────────────────────────────
    #  Public API: 통계
    # ──────────────────────────────────────────────────────────────

    def get_stats(self) -> CMSStats:
        """CMS 전체 통계 반환."""
        with self._lock:
            db = self._db()
            total_docs   = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            active_docs  = db.execute("SELECT COUNT(*) FROM documents WHERE status='active'").fetchone()[0]
            deprecated   = db.execute("SELECT COUNT(*) FROM documents WHERE status='deprecated'").fetchone()[0]
            total_chunks = db.execute("SELECT COUNT(*) FROM chunks WHERE is_deleted=0").fetchone()[0]
            indexed      = db.execute("SELECT COUNT(*) FROM chunks WHERE embedding_status='indexed' AND is_deleted=0").fetchone()[0]
            pending      = db.execute("SELECT COUNT(*) FROM chunks WHERE embedding_status='pending' AND is_deleted=0").fetchone()[0]

            # 버전 수 = parent_id가 있는 문서 수 + 1 (초기 버전)
            total_ver = db.execute("SELECT COUNT(DISTINCT file_hash) FROM documents").fetchone()[0]

        # DB 파일 크기
        try:
            db_mb = DB_PATH.stat().st_size / 1024 / 1024
        except Exception:
            db_mb = 0.0

        return CMSStats(
            total_documents=total_docs,
            active_documents=active_docs,
            deprecated_documents=deprecated,
            total_chunks=total_chunks,
            indexed_chunks=indexed,
            pending_chunks=pending,
            total_versions=total_ver,
            db_size_mb=round(db_mb, 2),
            last_updated=self._now(),
        )

    # ──────────────────────────────────────────────────────────────
    #  Public API: 문서 조회
    # ──────────────────────────────────────────────────────────────

    def list_documents(
        self,
        status_filter: str = "all",
        department_filter: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[DocumentMeta], int]:
        """
        문서 목록 반환 (페이지네이션 포함).

        Returns:
            (문서 리스트, 전체 건수)
        """
        conditions = []
        params: List = []

        if status_filter != "all":
            conditions.append("status = ?")
            params.append(status_filter)

        if department_filter:
            conditions.append("department = ?")
            params.append(department_filter)

        if search:
            conditions.append("(title LIKE ? OR tags LIKE ? OR description LIKE ?)")
            kw = f"%{search}%"
            params.extend([kw, kw, kw])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql_count = f"SELECT COUNT(*) FROM documents {where}"
        sql_data  = (
            f"SELECT * FROM documents {where} "
            f"ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        )

        with self._lock:
            total = self._db().execute(sql_count, params).fetchone()[0]
            rows  = self._db().execute(
                sql_data, params + [page_size, (page - 1) * page_size]
            ).fetchall()

        return [self._row_to_doc(r) for r in rows], total

    def get_document(self, document_id: str) -> Optional[DocumentMeta]:
        """단일 문서 조회."""
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return self._row_to_doc(row) if row else None

    def get_version_history(self, document_id: str) -> List[DocumentMeta]:
        """
        문서의 전체 버전 이력 반환.
        현재 문서를 시작으로 parent_id를 따라 올라갑니다.
        """
        history = []
        current_id = document_id
        visited = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            doc = self.get_document(current_id)
            if doc is None:
                break
            history.append(doc)
            current_id = doc.parent_id  # type: ignore[assignment]

        return history

    def get_departments(self) -> List[str]:
        """등록된 부서 목록 반환."""
        with self._lock:
            rows = self._db().execute(
                "SELECT DISTINCT department FROM documents WHERE department != '' ORDER BY department"
            ).fetchall()
        return [r[0] for r in rows]

    # ──────────────────────────────────────────────────────────────
    #  Public API: 문서 업로드 (핵심)
    # ──────────────────────────────────────────────────────────────

    def upload_document(
        self,
        file_bytes: bytes,
        file_name: str,
        title: str,
        department: str = "",
        tags: Optional[List[str]] = None,
        description: str = "",
        performed_by: str = "admin",
        force_new_version: bool = False,
    ) -> Dict:
        """
        문서 업로드 처리.

        [처리 흐름]
        1. MD5 해시 계산 → 완전 동일 파일 감지
        2. 기존 동일 제목 문서 검색 → 버전업 여부 결정
        3. 파일 저장 (원본 PDF)
        4. documents 테이블 INSERT
        5. 기존 문서 deprecated 처리 (버전업인 경우)
        6. 감사 로그 기록

        Returns:
            {'success': bool, 'document_id': str, 'is_new_version': bool,
             'message': str, 'duplicate': bool}
        """
        file_hash = self._md5(file_bytes)
        tags = tags or []
        now  = self._now()

        with self._lock:
            # ── 1. 완전 동일 파일 중복 감지 ────────────────────────
            dup_row = self._db().execute(
                "SELECT document_id, title, version, status FROM documents WHERE file_hash = ?",
                (file_hash,),
            ).fetchone()

            if dup_row and not force_new_version:
                return {
                    "success": False,
                    "duplicate": True,
                    "document_id": dup_row["document_id"],
                    "message": (
                        f"동일한 파일이 이미 등록되어 있습니다.\n"
                        f"제목: {dup_row['title']}  v{dup_row['version']}  ({dup_row['status']})\n"
                        f"강제 신규 버전으로 등록하려면 '새 버전으로 등록' 옵션을 사용하세요."
                    ),
                }

            # ── 2. 동일 제목의 기존 버전 확인 ──────────────────────
            prev_row = self._db().execute(
                "SELECT document_id, version FROM documents WHERE title = ? AND status = 'active' ORDER BY version DESC LIMIT 1",
                (title,),
            ).fetchone()

            is_new_version = prev_row is not None
            parent_id      = prev_row["document_id"] if is_new_version else None
            new_version    = (prev_row["version"] + 1) if is_new_version else 1
            new_id         = self._new_id()

            # ── 3. 파일 저장 ────────────────────────────────────────
            # 폴더: cms_data/documents/{document_id}/
            doc_dir = DOCS_DIR / new_id
            doc_dir.mkdir(parents=True, exist_ok=True)
            (doc_dir / file_name).write_bytes(file_bytes)

            # ── 4. documents INSERT ─────────────────────────────────
            self._db().execute(
                """INSERT INTO documents
                   (document_id, title, version, file_name, file_hash, status,
                    department, tags, description, parent_id, chunk_count,
                    indexed, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    new_id, title, new_version, file_name, file_hash,
                    "active", department, json.dumps(tags, ensure_ascii=False),
                    description, parent_id, 0, 0, now, now,
                ),
            )

            # ── 5. 기존 버전 deprecated 처리 ────────────────────────
            if is_new_version and parent_id:
                self._db().execute(
                    "UPDATE documents SET status='deprecated', updated_at=? WHERE document_id=?",
                    (now, parent_id),
                )
                self._log(
                    parent_id, "deprecated",
                    f"v{new_version - 1} → v{new_version} 버전업으로 인해 사용 중단 처리",
                    by=performed_by,
                )

            self._db().commit()

            # ── 6. 감사 로그 ────────────────────────────────────────
            action = "version_up" if is_new_version else "upload"
            self._log(
                new_id, action,
                f"{file_name} | v{new_version} | {department}",
                by=performed_by,
            )

        return {
            "success": True,
            "duplicate": False,
            "document_id": new_id,
            "is_new_version": is_new_version,
            "version": new_version,
            "message": (
                f"버전 {new_version}으로 업로드 완료" if is_new_version
                else "신규 문서로 등록 완료"
            ),
        }

    # ──────────────────────────────────────────────────────────────
    #  Public API: 청크 관리
    # ──────────────────────────────────────────────────────────────

    def save_chunks(
        self,
        document_id: str,
        chunks: List[Dict],
        performed_by: str = "admin",
    ) -> int:
        """
        청크 목록 저장.

        Args:
            chunks: [{'content': str, 'page': int, 'article': str, 'chunk_index': int}]

        Returns:
            저장된 청크 수
        """
        now = self._now()
        saved = 0

        with self._lock:
            # 기존 청크 soft delete
            self._db().execute(
                "UPDATE chunks SET is_deleted=1, updated_at=? WHERE document_id=?",
                (now, document_id),
            )

            for c in chunks:
                chunk_id = self._new_id()
                content  = c.get("content", "")
                self._db().execute(
                    """INSERT INTO chunks
                       (chunk_id, document_id, chunk_index, content, char_count,
                        page, article, embedding_status, is_deleted, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        chunk_id, document_id, c.get("chunk_index", saved),
                        content, len(content),
                        c.get("page", 0), c.get("article", ""),
                        "pending", 0, now, now,
                    ),
                )
                saved += 1

            # 문서 청크 수 업데이트
            self._db().execute(
                "UPDATE documents SET chunk_count=?, updated_at=? WHERE document_id=?",
                (saved, now, document_id),
            )
            self._db().commit()

            self._log(
                document_id, "chunk_save",
                f"{saved}개 청크 저장",
                by=performed_by,
            )

        return saved

    def get_chunks(
        self,
        document_id: str,
        include_deleted: bool = False,
    ) -> List[ChunkRecord]:
        """문서의 청크 목록 반환."""
        sql = "SELECT * FROM chunks WHERE document_id=?"
        params: List = [document_id]
        if not include_deleted:
            sql += " AND is_deleted=0"
        sql += " ORDER BY chunk_index"

        with self._lock:
            rows = self._db().execute(sql, params).fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def update_chunk(
        self,
        chunk_id: str,
        new_content: str,
        performed_by: str = "admin",
    ) -> bool:
        """청크 내용 수정 (embedding_status → pending으로 초기화)."""
        now = self._now()
        with self._lock:
            self._db().execute(
                """UPDATE chunks
                   SET content=?, char_count=?, embedding_status='pending', updated_at=?
                   WHERE chunk_id=?""",
                (new_content, len(new_content), now, chunk_id),
            )
            # 해당 청크의 document_id 가져와서 로그
            row = self._db().execute(
                "SELECT document_id FROM chunks WHERE chunk_id=?", (chunk_id,)
            ).fetchone()
            self._db().commit()

        if row:
            self._log(row["document_id"], "chunk_edit",
                      f"chunk_id={chunk_id} 수정", by=performed_by)
        return True

    def delete_chunk(
        self,
        chunk_id: str,
        performed_by: str = "admin",
    ) -> bool:
        """청크 소프트 삭제."""
        now = self._now()
        with self._lock:
            row = self._db().execute(
                "SELECT document_id FROM chunks WHERE chunk_id=?", (chunk_id,)
            ).fetchone()
            self._db().execute(
                "UPDATE chunks SET is_deleted=1, updated_at=? WHERE chunk_id=?",
                (now, chunk_id),
            )
            # 문서 청크 수 감소
            if row:
                self._db().execute(
                    "UPDATE documents SET chunk_count=chunk_count-1, updated_at=? WHERE document_id=?",
                    (now, row["document_id"]),
                )
            self._db().commit()

        if row:
            self._log(row["document_id"], "chunk_delete",
                      f"chunk_id={chunk_id} 소프트 삭제", by=performed_by)
        return True

    def mark_chunks_indexed(
        self,
        document_id: str,
        chunk_ids: Optional[List[str]] = None,
    ) -> int:
        """청크 embedding_status → indexed 로 업데이트."""
        now = self._now()
        with self._lock:
            if chunk_ids:
                placeholders = ",".join("?" * len(chunk_ids))
                self._db().execute(
                    f"UPDATE chunks SET embedding_status='indexed', updated_at=? "
                    f"WHERE chunk_id IN ({placeholders})",
                    [now] + chunk_ids,
                )
            else:
                self._db().execute(
                    "UPDATE chunks SET embedding_status='indexed', updated_at=? WHERE document_id=?",
                    (now, document_id),
                )
            # 문서 indexed 플래그
            self._db().execute(
                "UPDATE documents SET indexed=1, updated_at=? WHERE document_id=?",
                (now, document_id),
            )
            affected = self._db().execute(
                "SELECT changes()"
            ).fetchone()[0]
            self._db().commit()

        self._log(document_id, "reindex", f"{affected}개 청크 인덱싱 완료")
        return affected

    # ──────────────────────────────────────────────────────────────
    #  Public API: 문서 상태 관리
    # ──────────────────────────────────────────────────────────────

    def set_document_status(
        self,
        document_id: str,
        status: str,
        performed_by: str = "admin",
    ) -> bool:
        """문서 상태 변경 (active / inactive / deprecated)."""
        assert status in ("active", "inactive", "deprecated")
        now = self._now()
        with self._lock:
            self._db().execute(
                "UPDATE documents SET status=?, updated_at=? WHERE document_id=?",
                (status, now, document_id),
            )
            self._db().commit()
        self._log(document_id, f"status_{status}",
                  f"상태 변경 → {status}", by=performed_by)
        return True

    def rollback_to_version(
        self,
        old_document_id: str,
        performed_by: str = "admin",
    ) -> bool:
        """
        이전 버전으로 롤백.
        지정 버전을 active로 복원, 현재 active 버전을 deprecated 처리.
        """
        old_doc = self.get_document(old_document_id)
        if old_doc is None:
            return False

        now = self._now()
        with self._lock:
            # 현재 active 버전 deprecated
            self._db().execute(
                "UPDATE documents SET status='deprecated', updated_at=? WHERE title=? AND status='active'",
                (now, old_doc.title),
            )
            # 지정 버전 active 복원
            self._db().execute(
                "UPDATE documents SET status='active', updated_at=? WHERE document_id=?",
                (now, old_document_id),
            )
            self._db().commit()

        self._log(
            old_document_id, "rollback",
            f"v{old_doc.version}으로 롤백", by=performed_by,
        )
        return True

    # ──────────────────────────────────────────────────────────────
    #  Public API: 감사 로그 조회
    # ──────────────────────────────────────────────────────────────

    def get_audit_logs(
        self,
        document_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[AuditLog]:
        """감사 로그 조회 (최신순)."""
        if document_id:
            sql = "SELECT * FROM audit_log WHERE document_id=? ORDER BY created_at DESC LIMIT ?"
            params = (document_id, limit)
        else:
            sql = "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?"
            params = (limit,)

        with self._lock:
            rows = self._db().execute(sql, params).fetchall()

        return [
            AuditLog(
                log_id=r["log_id"],
                document_id=r["document_id"],
                action=r["action"],
                detail=r["detail"] or "",
                performed_by=r["performed_by"] or "admin",
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ──────────────────────────────────────────────────────────────
    #  Public API: FAISS 연동 (청크 → 벡터 DB)
    # ──────────────────────────────────────────────────────────────

    def build_faiss_from_document(
        self,
        document_id: str,
        performed_by: str = "admin",
    ) -> Dict:
        """
        특정 문서의 청크를 FAISS에 인덱싱.

        [처리 흐름]
        1. pending 청크 조회
        2. LangChain Document로 변환
        3. VectorStoreManager.append()로 증분 추가
        4. embedding_status → indexed 업데이트
        """
        from langchain_core.documents import Document as LCDoc

        doc_meta = self.get_document(document_id)
        if doc_meta is None:
            return {"success": False, "message": "문서를 찾을 수 없습니다."}

        chunks = [
            c for c in self.get_chunks(document_id)
            if c.embedding_status == "pending"
        ]

        if not chunks:
            return {"success": True, "message": "인덱싱할 청크가 없습니다 (이미 완료됨).", "count": 0}

        # LangChain Document 변환
        lc_docs = [
            LCDoc(
                page_content=c.content,
                metadata={
                    "source": doc_meta.file_name,
                    "document_id": document_id,
                    "chunk_id": c.chunk_id,
                    "page": c.page,
                    "article": c.article,
                    "chunk_index": c.chunk_index,
                    "title": doc_meta.title,
                    "department": doc_meta.department,
                    "version": doc_meta.version,
                },
            )
            for c in chunks
        ]

        try:
            from core.vector_store import VectorStoreManager
            if _SETTINGS_OK:
                manager = VectorStoreManager(
                    db_path=settings.rag_db_path,
                    model_name=settings.embedding_model,
                    cache_dir=str(settings.rag_db_path.parent),
                )
                success = manager.append(lc_docs)
            else:
                success = False
        except Exception as e:
            logger.error(f"FAISS 인덱싱 실패: {e}")
            return {"success": False, "message": str(e), "count": 0}

        if success:
            indexed_ids = [c.chunk_id for c in chunks]
            self.mark_chunks_indexed(document_id, indexed_ids)
            self._log(document_id, "faiss_index",
                      f"{len(chunks)}개 청크 FAISS 인덱싱", by=performed_by)
            return {
                "success": True,
                "message": f"{len(chunks)}개 청크 인덱싱 완료",
                "count": len(chunks),
            }

        return {"success": False, "message": "FAISS append 실패", "count": 0}

    def remove_from_faiss(self, document_id: str, performed_by: str = "admin") -> Dict:
        """
        특정 문서의 청크를 FAISS에서 제거.
        (FAISS 전체 재구축 방식 사용)
        """
        try:
            from services.vector_admin_service import get_admin_service
            svc = get_admin_service()
            doc = self.get_document(document_id)
            if doc is None:
                return {"success": False, "message": "문서를 찾을 수 없습니다."}

            result = svc.delete_source(doc.file_name)
            if result.success:
                # embedding_status → pending 으로 초기화
                now = self._now()
                with self._lock:
                    self._db().execute(
                        "UPDATE chunks SET embedding_status='pending', updated_at=? WHERE document_id=?",
                        (now, document_id),
                    )
                    self._db().execute(
                        "UPDATE documents SET indexed=0, updated_at=? WHERE document_id=?",
                        (now, document_id),
                    )
                    self._db().commit()
                self._log(document_id, "faiss_remove",
                          f"FAISS에서 제거", by=performed_by)
            return {"success": result.success, "message": result.message}
        except Exception as e:
            return {"success": False, "message": str(e)}

    # ──────────────────────────────────────────────────────────────
    #  Public API: FAISS → CMS 동기화
    # ──────────────────────────────────────────────────────────────

    def sync_from_faiss(
        self,
        sources: List,   # List[SourceFileInfo] — import 순환 방지용 Any
        performed_by: str = "admin",
    ) -> Dict:
        """
        기존 FAISS 소스 파일들을 CMS 문서로 일괄 등록합니다.

        [왜 이 기능이 필요한가]
        · build_db.py 로 직접 구축된 FAISS DB에는 216개 소스가 있지만
          CMS SQLite에는 아무것도 등록되지 않은 상태.
        · 이 함수를 실행하면 FAISS 소스 파일 → CMS 문서 레코드 자동 생성.
        · 이미 CMS에 등록된 파일명은 건너뜁니다 (중복 등록 방지).

        [처리 흐름]
        1. 현재 CMS에 등록된 file_name 목록 조회
        2. FAISS 소스 중 미등록 파일만 선별
        3. 각 소스에 대해 documents + chunks INSERT
        4. indexed=1, embedding_status='indexed' 로 바로 마킹

        Args:
            sources: vector_admin_service.list_sources() 반환값

        Returns:
            {'success': bool, 'created': int, 'skipped': int, 'message': str}
        """
        now = self._now()

        # 기존 CMS 등록 파일명 조회
        with self._lock:
            rows = self._db().execute(
                "SELECT file_name FROM documents"
            ).fetchall()
        existing_names = {r["file_name"] for r in rows}

        created = 0
        skipped = 0

        for src in sources:
            fname = src.source_name
            if fname in existing_names:
                skipped += 1
                continue

            doc_id    = self._new_id()
            title     = fname.replace(".pdf", "").replace("_", " ")
            fake_hash = hashlib.md5(fname.encode()).hexdigest()  # 원본 없어서 파일명 기반

            with self._lock:
                # 1. documents INSERT
                self._db().execute(
                    """INSERT INTO documents
                       (document_id, title, version, file_name, file_hash, status,
                        department, tags, description, parent_id, chunk_count,
                        indexed, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        doc_id, title, 1, fname, fake_hash,
                        "active", "", "[]",
                        "FAISS 동기화로 자동 등록된 문서",
                        None,
                        src.chunk_count,
                        1,   # indexed=True (이미 FAISS에 있으므로)
                        now, now,
                    ),
                )

                # 2. chunks INSERT (SourceFileInfo.chunks 사용)
                chunk_list = getattr(src, "chunks", [])
                for chunk in chunk_list:
                    chunk_id = self._new_id()
                    content  = getattr(chunk, "text_full", "") or getattr(chunk, "text_preview", "")
                    self._db().execute(
                        """INSERT INTO chunks
                           (chunk_id, document_id, chunk_index, content, char_count,
                            page, article, embedding_status, is_deleted, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            chunk_id, doc_id,
                            getattr(chunk, "chunk_index", 0),
                            content, len(content),
                            getattr(chunk, "page", 0),
                            getattr(chunk, "article", ""),
                            "indexed",  # 이미 FAISS에 있으므로
                            0, now, now,
                        ),
                    )

                self._db().commit()

            self._log(doc_id, "faiss_sync",
                      f"{fname} — {src.chunk_count}청크 동기화", by=performed_by)
            existing_names.add(fname)
            created += 1

        return {
            "success": True,
            "created": created,
            "skipped": skipped,
            "message": f"동기화 완료: 신규 {created}개 등록, {skipped}개 이미 존재 (건너뜀)",
        }

    def create_backup_manual(self, performed_by: str = "admin") -> Dict:
        """
        벡터 DB 수동 백업 생성.

        [자동 백업이 생성되는 시점]
        · delete_source() 호출 시 (파일 단위 삭제 + 재구축)
        · rebuild_all() 호출 시 (전체 재구축)

        [이 함수의 용도]
        위 두 작업 없이도 관리자가 직접 백업 시점을 지정할 수 있도록 합니다.
        """
        try:
            import shutil
            from datetime import datetime

            if _SETTINGS_OK:
                db_path    = Path(settings.rag_db_path)
                backup_dir = Path(settings.backup_dir)
            else:
                return {"success": False, "message": "settings 로드 실패"}

            index_file = db_path / "index.faiss"
            if not index_file.exists():
                return {"success": False, "message": "FAISS DB 파일이 없습니다."}

            backup_dir.mkdir(parents=True, exist_ok=True)
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = backup_dir / f"vector_store_{ts}"
            shutil.copytree(str(db_path), str(dest))

            # 최근 5개 유지
            backups = sorted(backup_dir.glob("vector_store_*"))
            if len(backups) > 5:
                for old in backups[:-5]:
                    shutil.rmtree(old)

            self._log("manual_backup", "backup_create",
                      f"수동 백업: {dest.name}", by=performed_by)

            return {"success": True, "message": f"백업 완료: {dest.name}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    # ──────────────────────────────────────────────────────────────
    #  Public API: 검색 테스트
    # ──────────────────────────────────────────────────────────────

    def search_test(
        self,
        query: str,
        top_k: int = 5,
        department_filter: str = "",
    ) -> List[SearchResult]:
        """
        벡터 검색 테스트.
        FAISS 검색 결과를 CMS 메타데이터와 결합하여 반환.
        """
        try:
            from services.vector_admin_service import get_admin_service
            svc = get_admin_service()
            raw_results = svc.search_chunks(query, top_k=top_k * 2)
        except Exception as e:
            logger.error(f"검색 실패: {e}")
            return []

        results = []
        for rank, (chunk, score) in enumerate(raw_results, start=1):
            if len(results) >= top_k:
                break

            # department 필터
            if department_filter and chunk.source not in ("", department_filter):
                pass  # 소스 기반 필터 (추후 확장)

            # chunk_id로 CMS 메타데이터 조회
            doc_title  = chunk.source
            doc_id_    = ""
            emb_status = "indexed"

            with self._lock:
                row = self._db().execute(
                    "SELECT c.chunk_id, c.document_id, c.embedding_status, d.title "
                    "FROM chunks c JOIN documents d ON c.document_id=d.document_id "
                    "WHERE c.content LIKE ? AND c.is_deleted=0 LIMIT 1",
                    (f"{chunk.text_preview[:40]}%",),
                ).fetchone()
                if row:
                    doc_title  = row["title"]
                    doc_id_    = row["document_id"]
                    emb_status = row["embedding_status"]

            results.append(SearchResult(
                rank=rank,
                chunk_id=getattr(chunk, "doc_id", ""),
                document_id=doc_id_,
                document_title=doc_title,
                chunk_index=chunk.chunk_index,
                content=chunk.text_full,
                page=chunk.page,
                similarity_score=float(score),
                embedding_status=emb_status,
            ))

        return results

    # ──────────────────────────────────────────────────────────────
    #  Public API: Markdown 저장/조회
    # ──────────────────────────────────────────────────────────────

    def save_markdown(self, document_id: str, content: str) -> None:
        """Markdown 내용 저장."""
        md_path = MD_DIR / f"{document_id}.md"
        md_path.write_text(content, encoding="utf-8")
        now = self._now()
        with self._lock:
            self._db().execute(
                "UPDATE documents SET updated_at=? WHERE document_id=?",
                (now, document_id),
            )
            self._db().commit()
        self._log(document_id, "markdown_save", "Markdown 저장")

    def load_markdown(self, document_id: str) -> str:
        """Markdown 내용 로드."""
        md_path = MD_DIR / f"{document_id}.md"
        if md_path.exists():
            return md_path.read_text(encoding="utf-8")
        return ""

    def get_pdf_path(self, document_id: str, file_name: str) -> Optional[Path]:
        """PDF 파일 경로 반환."""
        p = DOCS_DIR / document_id / file_name
        return p if p.exists() else None


# ══════════════════════════════════════════════════════════════════════
#  PDF → Markdown 변환 유틸
# ══════════════════════════════════════════════════════════════════════

def pdf_to_markdown(file_bytes: bytes, file_name: str) -> str:
    """
    PDF 파일을 Markdown 텍스트로 변환합니다.

    [변환 방식]
    1. pypdf 또는 pdfplumber로 텍스트 추출
    2. 페이지별 구분선 삽입
    3. 기본 Markdown 형식 적용

    Returns:
        변환된 Markdown 문자열
    """
    lines = []
    lines.append(f"# {file_name.replace('.pdf', '')}\n")

    # pypdf 시도
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                lines.append(f"\n---\n*페이지 {i}*\n")
                lines.append(text.strip())
        return "\n".join(lines)
    except ImportError:
        pass

    # pdfplumber 시도
    try:
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    lines.append(f"\n---\n*페이지 {i}*\n")
                    lines.append(text.strip())
        return "\n".join(lines)
    except ImportError:
        pass

    return f"# {file_name}\n\n> ⚠️ PDF 변환 라이브러리(pypdf/pdfplumber)를 설치해야 합니다.\n> `pip install pypdf pdfplumber`"


def markdown_to_chunks(
    markdown: str,
    chunk_size: int = 800,
    overlap: int = 150,
    document_id: str = "",
) -> List[Dict]:
    """
    Markdown 텍스트를 청크로 분할합니다.

    [전략]
    1. 페이지 구분선(---) 기준으로 1차 분할
    2. 각 섹션을 chunk_size 기준으로 2차 분할
    3. overlap 만큼 앞 청크와 내용 중첩

    Returns:
        [{'content': str, 'page': int, 'article': str, 'chunk_index': int}]
    """
    import re
    chunks = []
    idx = 0

    # 페이지 분리
    page_pattern = re.compile(r"\n---\n\*페이지 (\d+)\*\n")
    sections = page_pattern.split(markdown)

    current_page = 0
    buffer = ""

    def _flush(buf: str, page: int) -> None:
        nonlocal idx
        buf = buf.strip()
        if not buf:
            return
        # chunk_size 초과 시 추가 분할
        while len(buf) > chunk_size:
            chunks.append({
                "chunk_index": idx,
                "content": buf[:chunk_size],
                "page": page,
                "article": "",
            })
            idx += 1
            buf = buf[chunk_size - overlap:]
        if buf:
            chunks.append({
                "chunk_index": idx,
                "content": buf,
                "page": page,
                "article": "",
            })
            idx += 1

    for part in sections:
        if part.isdigit():
            # 페이지 번호
            _flush(buffer, current_page)
            buffer = ""
            current_page = int(part)
        else:
            buffer += "\n" + part

    _flush(buffer, current_page)
    return chunks


# ══════════════════════════════════════════════════════════════════════
#  싱글톤 팩토리
# ══════════════════════════════════════════════════════════════════════

_cms_instance: Optional[CMSService] = None
_cms_lock = threading.Lock()


def get_cms_service() -> CMSService:
    """CMSService 싱글톤 반환 (스레드 안전)."""
    global _cms_instance
    if _cms_instance is None:
        with _cms_lock:
            if _cms_instance is None:
                _cms_instance = CMSService()
    return _cms_instance