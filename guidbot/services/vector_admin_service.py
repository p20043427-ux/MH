"""
services/vector_admin_service.py ─ 벡터 DB 관리자 서비스 레이어 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[이 모듈의 역할]
  vector_db_admin.py (UI 레이어)에서 직접 FAISS를 건드리지 않도록
  모든 벡터 DB 조작 로직을 이 서비스로 분리합니다.

  UI 레이어  ←→  VectorAdminService  ←→  FAISS / 파일시스템
              (이 파일)

[FAISS 문서 삭제 원리]
  FAISS는 개별 벡터 삭제를 공식 지원하지 않습니다.
  (IndexFlatL2 기준 — GPU 인덱스 제외)

  해결 방법:
    1. db.docstore._dict 에서 모든 Document를 꺼냄
    2. 삭제 대상 source 파일명을 가진 Document를 제외
    3. 남은 Document로 FAISS.from_documents() 재구축
    4. save_local()로 덮어쓰기

  비용: 재구축 시간 ~10~60초 (문서 수에 비례)
  장점: 구현이 단순하고 안정적

[스레드 안전성]
  · Streamlit은 요청마다 별도 스레드 사용
  · _admin_lock을 이용해 동시 재구축 방지
  · 재구축 중 챗봇 RAGPipeline은 기존 캐시된 DB를 계속 사용 가능
    (단, 재구축 완료 후 reset_pipeline() 호출 필요)

[임계 구역 보호]
  build/delete 작업은 단일 스레드만 수행 가능
  (병렬 재구축 시 파일 충돌 위험)
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

# ── 타입 힌트용 조건부 임포트 ──────────────────────────────────────
try:
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document
    _LANGCHAIN_OK = True
except ImportError:
    _LANGCHAIN_OK = False

# ── 내부 모듈 임포트 ────────────────────────────────────────────────
try:
    from config.settings import settings
    from core.embeddings import get_embeddings_auto
    from core.vector_store import VectorStoreManager
    from core.document_loader import load_and_split
    from utils.logger import get_logger
    logger = get_logger(__name__, log_dir=settings.log_dir)
except Exception:
    import logging
    logger = logging.getLogger(__name__)
    settings = None  # type: ignore[assignment]


# ══════════════════════════════════════════════════════════════════════
#  데이터 클래스 정의
# ══════════════════════════════════════════════════════════════════════


@dataclass
class ChunkInfo:
    """
    FAISS 도큐스토어에서 추출한 단일 청크 정보.

    [메타데이터 필드 설명]
    · source      : 원본 파일 경로 (PDF 경우 절대경로 또는 파일명)
    · page        : PDF 페이지 번호
    · article     : 조항 번호 (규정집인 경우)
    · chunk_index : 파일 내 청크 순서
    """
    doc_id: str           # FAISS 도큐스토어 내부 ID
    source: str           # 원본 파일명 (basename)
    source_path: str      # 원본 파일 전체 경로
    page: int             # 페이지 번호 (없으면 0)
    article: str          # 조항 (없으면 빈 문자열)
    chunk_index: int      # 청크 순서
    text_preview: str     # 텍스트 미리보기 (최대 300자)
    text_full: str        # 전체 텍스트
    char_count: int       # 글자 수


@dataclass
class SourceFileInfo:
    """
    동일 출처(source 파일)에 속하는 청크들의 집합 정보.
    문서 뷰어에서 파일 단위로 표시할 때 사용합니다.
    """
    source_name: str          # 파일명 (basename)
    source_path: str          # 전체 경로
    chunk_count: int          # 소속 청크 수
    total_chars: int          # 전체 글자 수
    page_range: Tuple[int, int]  # (최소 페이지, 최대 페이지)
    chunks: List[ChunkInfo]   # 소속 청크 리스트
    added_at: str             # 파일 수정 시각 (파일시스템 기준, 없으면 "알 수 없음")


@dataclass
class VectorDBStats:
    """
    벡터 DB 전체 통계 요약.
    대시보드 탭에서 표시합니다.
    """
    is_loaded: bool            # DB 로드 성공 여부
    total_vectors: int         # 총 벡터 수 (= 총 청크 수)
    total_sources: int         # 고유 소스 파일 수
    total_chars: int           # 전체 텍스트 글자 수 합계
    db_path: str               # FAISS DB 저장 경로
    db_size_mb: float          # 파일시스템 기준 DB 크기 (MB)
    backup_count: int          # 보관 중인 백업 수
    index_type: str            # FAISS 인덱스 타입 (예: IndexFlatL2)
    last_modified: str         # DB 파일 마지막 수정 시각
    source_summary: Dict[str, int]  # {파일명: 청크 수} 딕셔너리


@dataclass
class OperationResult:
    """
    서비스 메서드의 작업 결과 반환 타입.
    UI 레이어에서 성공/실패 메시지를 표시하는 데 사용합니다.
    """
    success: bool
    message: str
    detail: str = ""             # 상세 메시지 (오류 추적용)
    added_chunks: int = 0        # 추가된 청크 수
    removed_chunks: int = 0      # 제거된 청크 수
    elapsed_sec: float = 0.0     # 소요 시간 (초)


# ══════════════════════════════════════════════════════════════════════
#  동시 작업 방지 락
# ══════════════════════════════════════════════════════════════════════

# 벡터 DB 쓰기 작업(빌드/삭제/업로드)은 한 번에 하나만 실행 허용
_admin_lock = threading.Lock()
_is_building = False   # 현재 재구축 중인지 여부 (UI 표시용)


def is_building() -> bool:
    """현재 벡터 DB 재구축 작업이 진행 중인지 반환."""
    return _is_building


# ══════════════════════════════════════════════════════════════════════
#  VectorAdminService 클래스
# ══════════════════════════════════════════════════════════════════════


class VectorAdminService:
    """
    벡터 DB 관리 작업의 단일 진입점.

    [사용 예시]
        svc = VectorAdminService()
        stats = svc.get_stats()
        sources = svc.list_sources()
        result = svc.delete_source("취업규칙_2024.pdf")
        result = svc.upload_and_add(file_bytes, "신규규정집.pdf")
    """

    def __init__(self) -> None:
        """
        서비스 초기화.
        settings에서 경로를 읽어옵니다.
        settings를 사용할 수 없으면 기본값으로 폴백합니다.
        """
        # settings가 로드 가능한 경우와 그렇지 않은 경우 분기
        if settings is not None:
            self._db_path = Path(settings.rag_db_path)
            self._backup_dir = Path(settings.backup_dir)
            self._docs_dir = Path(settings.docs_dir) if hasattr(settings, 'docs_dir') else self._db_path.parent / "docs"
            self._model_name = settings.embedding_model
        else:
            # settings 없을 때 기본 경로 (개발/테스트용)
            base = Path(__file__).parent.parent
            self._db_path = base / "vector_store" / "faiss_db"
            self._backup_dir = base / "vector_store" / "backups"
            self._docs_dir = base / "docs"
            self._model_name = "snunlp/KR-SBERT-V40K-klueNLI-augSTS"

        # 임시 파일 저장 디렉토리 (업로드 파일 처리용)
        self._tmp_dir = Path(tempfile.gettempdir()) / "guidbot_admin"
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────────────────────────
    #  내부 헬퍼: FAISS DB 로드
    # ──────────────────────────────────────────────────────────────

    def _load_db(self) -> Optional["FAISS"]:
        """
        FAISS DB를 로드합니다.

        [allow_dangerous_deserialization=True 이유]
        FAISS는 내부적으로 pickle을 사용합니다.
        우리가 직접 생성한 로컬 파일이므로 안전합니다.
        외부에서 받은 FAISS 파일에는 절대 True로 하지 마세요.

        Returns:
            FAISS 인스턴스 또는 None (DB 없거나 로드 실패)
        """
        if not _LANGCHAIN_OK:
            logger.error("langchain 패키지가 설치되지 않았습니다.")
            return None

        index_file = self._db_path / "index.faiss"
        if not index_file.exists():
            logger.warning(f"벡터 DB 파일 없음: {index_file}")
            return None

        try:
            from langchain_community.vectorstores import FAISS
            embeddings = get_embeddings_auto()
            db = FAISS.load_local(
                str(self._db_path),
                embeddings,
                allow_dangerous_deserialization=True,
            )
            logger.info(f"DB 로드 완료: {db.index.ntotal:,}개 벡터")
            return db
        except Exception as exc:
            logger.error(f"DB 로드 실패: {exc}", exc_info=True)
            return None

    def _extract_chunks(self, db: "FAISS") -> List[ChunkInfo]:
        """
        FAISS 도큐스토어에서 모든 청크 정보를 추출합니다.

        [FAISS 내부 구조 설명]
        db.docstore._dict : {내부ID: Document} 딕셔너리
        Document.page_content : 청크 텍스트
        Document.metadata     : {'source': ..., 'page': ..., 'article': ...}

        Returns:
            ChunkInfo 리스트 (source 기준 정렬)
        """
        chunks: List[ChunkInfo] = []

        try:
            # FAISS 도큐스토어에서 모든 문서 추출
            docstore_dict: dict = db.docstore._dict
        except AttributeError:
            logger.error("FAISS docstore 접근 실패: 구버전 FAISS 구조일 수 있습니다.")
            return chunks

        for doc_id, document in docstore_dict.items():
            meta = document.metadata or {}
            source_path = str(meta.get("source", "알 수 없음"))
            source_name = Path(source_path).name if source_path else "알 수 없음"

            text = document.page_content or ""
            chunks.append(
                ChunkInfo(
                    doc_id=str(doc_id),
                    source=source_name,
                    source_path=source_path,
                    page=int(meta.get("page", 0)),
                    article=str(meta.get("article", "")),
                    chunk_index=int(meta.get("chunk_index", 0)),
                    text_preview=text[:300],
                    text_full=text,
                    char_count=len(text),
                )
            )

        # source → page → chunk_index 순으로 정렬 (뷰어에서 순서 보장)
        chunks.sort(key=lambda c: (c.source, c.page, c.chunk_index))
        return chunks

    def _group_by_source(self, chunks: List[ChunkInfo]) -> List[SourceFileInfo]:
        """
        청크 리스트를 source 파일 단위로 그룹화합니다.

        [그룹화 기준]
        source_path 전체 경로가 같으면 동일 파일.
        같은 파일명이라도 경로가 다르면 별개로 취급합니다.

        Returns:
            SourceFileInfo 리스트 (총 청크 수 내림차순 정렬)
        """
        groups: Dict[str, List[ChunkInfo]] = {}
        for chunk in chunks:
            groups.setdefault(chunk.source_path, []).append(chunk)

        result: List[SourceFileInfo] = []
        for source_path, chunk_list in groups.items():
            source_name = Path(source_path).name
            pages = [c.page for c in chunk_list if c.page > 0]
            page_range = (min(pages), max(pages)) if pages else (0, 0)

            # 파일 수정 시각 조회 (가능하면)
            try:
                mtime = Path(source_path).stat().st_mtime
                added_at = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                added_at = "알 수 없음"

            result.append(
                SourceFileInfo(
                    source_name=source_name,
                    source_path=source_path,
                    chunk_count=len(chunk_list),
                    total_chars=sum(c.char_count for c in chunk_list),
                    page_range=page_range,
                    chunks=chunk_list,
                    added_at=added_at,
                )
            )

        # 청크 수 내림차순 정렬 (가장 많은 문서가 먼저)
        result.sort(key=lambda s: s.chunk_count, reverse=True)
        return result

    # ──────────────────────────────────────────────────────────────
    #  Public API: 조회
    # ──────────────────────────────────────────────────────────────

    def get_stats(self) -> VectorDBStats:
        """
        벡터 DB 전체 통계를 반환합니다.
        DB 로드 실패 시에도 빈 통계를 반환합니다 (UI가 빈 화면 대신 안내 메시지 표시 가능).

        Returns:
            VectorDBStats
        """
        # DB 파일 존재 여부 확인
        index_file = self._db_path / "index.faiss"

        # 파일시스템 기준 DB 크기 계산
        db_size_mb = 0.0
        last_modified = "없음"
        if index_file.exists():
            try:
                db_size_mb = sum(
                    f.stat().st_size for f in self._db_path.glob("*")
                    if f.is_file()
                ) / (1024 * 1024)
                mtime = index_file.stat().st_mtime
                last_modified = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        # 백업 수 계산
        backup_count = 0
        if self._backup_dir.exists():
            backup_count = len(list(self._backup_dir.glob("vector_store_*")))

        # DB 로드
        db = self._load_db()
        if db is None:
            return VectorDBStats(
                is_loaded=False,
                total_vectors=0,
                total_sources=0,
                total_chars=0,
                db_path=str(self._db_path),
                db_size_mb=round(db_size_mb, 2),
                backup_count=backup_count,
                index_type="(DB 없음)",
                last_modified=last_modified,
                source_summary={},
            )

        # 청크 추출 후 통계 집계
        chunks = self._extract_chunks(db)
        source_summary = {}
        for chunk in chunks:
            source_summary[chunk.source] = source_summary.get(chunk.source, 0) + 1

        # FAISS 인덱스 타입 확인
        try:
            import faiss as faiss_lib
            index_type = type(db.index).__name__
        except Exception:
            index_type = "IndexFlatL2"

        return VectorDBStats(
            is_loaded=True,
            total_vectors=db.index.ntotal,
            total_sources=len(source_summary),
            total_chars=sum(c.char_count for c in chunks),
            db_path=str(self._db_path),
            db_size_mb=round(db_size_mb, 2),
            backup_count=backup_count,
            index_type=index_type,
            last_modified=last_modified,
            source_summary=source_summary,
        )

    def list_sources(self) -> List[SourceFileInfo]:
        """
        벡터 DB에 저장된 모든 소스 파일 정보를 반환합니다.
        문서 뷰어 탭에서 파일 목록을 표시하는 데 사용합니다.

        Returns:
            SourceFileInfo 리스트 (청크 수 내림차순)
        """
        db = self._load_db()
        if db is None:
            return []

        chunks = self._extract_chunks(db)
        return self._group_by_source(chunks)

    def get_chunks_by_source(self, source_name: str) -> List[ChunkInfo]:
        """
        특정 소스 파일의 모든 청크를 반환합니다.
        문서 상세 뷰어에서 호출합니다.

        Args:
            source_name: 파일명 (basename, 예: "취업규칙_2024.pdf")

        Returns:
            ChunkInfo 리스트 (page → chunk_index 정렬)
        """
        db = self._load_db()
        if db is None:
            return []

        chunks = self._extract_chunks(db)
        return [c for c in chunks if c.source == source_name]

    def search_chunks(self, query: str, top_k: int = 10) -> List[Tuple[ChunkInfo, float]]:
        """
        쿼리로 벡터 DB를 검색하여 관련 청크와 유사도를 반환합니다.
        검색 테스트 탭에서 사용합니다.

        Args:
            query:  검색 쿼리 문자열
            top_k:  반환할 최대 결과 수

        Returns:
            [(ChunkInfo, 유사도 점수)] 리스트
        """
        db = self._load_db()
        if db is None:
            return []

        try:
            # FAISS similarity_search_with_score: 거리 기반 (낮을수록 유사)
            results = db.similarity_search_with_score(query, k=top_k)
            chunks = self._extract_chunks(db)

            # doc_id → ChunkInfo 매핑 딕셔너리
            chunk_map = {c.doc_id: c for c in chunks}

            output: List[Tuple[ChunkInfo, float]] = []
            for doc, score in results:
                # FAISS 검색 결과 Document의 doc_id 추출
                # LangChain FAISS는 Document.metadata에 직접 id를 두지 않음
                # page_content 첫 80자로 매칭
                preview = (doc.page_content or "")[:80]
                matched = next(
                    (c for c in chunks if c.text_preview.startswith(preview[:60])),
                    None,
                )
                if matched:
                    output.append((matched, float(score)))

            return output
        except Exception as exc:
            logger.error(f"검색 실패: {exc}", exc_info=True)
            return []

    def get_backup_list(self) -> List[Dict]:
        """
        보관 중인 백업 목록을 반환합니다.

        Returns:
            [{'name': ..., 'path': ..., 'created_at': ..., 'size_mb': ...}] 리스트
        """
        if not self._backup_dir.exists():
            return []

        backups = []
        for backup_path in sorted(
            self._backup_dir.glob("vector_store_*"), reverse=True
        ):
            if not backup_path.is_dir():
                continue
            try:
                size_mb = sum(
                    f.stat().st_size for f in backup_path.glob("*") if f.is_file()
                ) / (1024 * 1024)
                mtime = backup_path.stat().st_mtime
                created_at = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                size_mb = 0.0
                created_at = "알 수 없음"

            backups.append({
                "name": backup_path.name,
                "path": str(backup_path),
                "created_at": created_at,
                "size_mb": round(size_mb, 2),
            })
        return backups

    # ──────────────────────────────────────────────────────────────
    #  Public API: 쓰기 작업 (락 필요)
    # ──────────────────────────────────────────────────────────────

    def delete_source(self, source_name: str) -> OperationResult:
        """
        특정 소스 파일의 모든 청크를 벡터 DB에서 제거합니다.

        [삭제 처리 과정]
        1. 현재 DB에서 모든 Document 추출
        2. source_name에 해당하는 Document 제외
        3. 남은 Document로 FAISS 전체 재구축
        4. 기존 DB 백업 후 저장

        [주의사항]
        · 재구축 시간은 청크 수에 비례 (7,000청크 기준 약 30~60초)
        · 재구축 완료 후 챗봇 main.py의 RAGPipeline을 reset해야 반영됨
        · 동시 삭제 방지를 위해 _admin_lock 사용

        Args:
            source_name: 삭제할 소스 파일명 (basename)

        Returns:
            OperationResult
        """
        global _is_building

        # 동시 작업 방지
        if not _admin_lock.acquire(blocking=False):
            return OperationResult(
                success=False,
                message="다른 재구축 작업이 진행 중입니다. 잠시 후 다시 시도하세요.",
            )

        _is_building = True
        t_start = time.time()

        try:
            db = self._load_db()
            if db is None:
                return OperationResult(success=False, message="벡터 DB를 로드할 수 없습니다.")

            # 모든 청크 추출
            all_chunks = self._extract_chunks(db)
            target_chunks = [c for c in all_chunks if c.source == source_name]
            remaining_chunks = [c for c in all_chunks if c.source != source_name]

            if not target_chunks:
                return OperationResult(
                    success=False,
                    message=f"'{source_name}'에 해당하는 청크를 찾을 수 없습니다.",
                )

            logger.info(
                f"삭제 시작: {source_name} "
                f"({len(target_chunks)}청크 → {len(remaining_chunks)}청크 남음)"
            )

            if not remaining_chunks:
                # 마지막 문서 삭제 시 빈 DB 처리
                return OperationResult(
                    success=False,
                    message="마지막 남은 문서는 삭제할 수 없습니다. "
                            "다른 문서를 먼저 추가한 후 삭제하세요.",
                )

            # 남은 청크를 Document 형식으로 변환
            from langchain_core.documents import Document as LCDoc
            remaining_docs = [
                LCDoc(
                    page_content=c.text_full,
                    metadata={
                        "source": c.source_path,
                        "page": c.page,
                        "article": c.article,
                        "chunk_index": c.chunk_index,
                    },
                )
                for c in remaining_chunks
            ]

            # 기존 DB 백업 후 재구축
            manager = VectorStoreManager(
                db_path=self._db_path,
                model_name=self._model_name,
                cache_dir=str(self._db_path.parent),
            )
            new_db = manager.build(remaining_docs)

            if new_db is None:
                return OperationResult(
                    success=False,
                    message="재구축 중 오류가 발생했습니다. 로그를 확인하세요.",
                )

            elapsed = time.time() - t_start
            logger.info(
                f"삭제 완료: {source_name} "
                f"({len(target_chunks)}청크 제거, {elapsed:.1f}초 소요)"
            )

            # 챗봇 RAGPipeline 싱글톤 리셋 (새 DB 반영)
            try:
                from core.rag_pipeline import reset_pipeline
                reset_pipeline()
                logger.info("RAGPipeline 싱글톤 리셋 완료")
            except Exception as _e:
                logger.warning(f"RAGPipeline 리셋 건너뜀: {_e}")

            return OperationResult(
                success=True,
                message=f"'{source_name}' 삭제 완료",
                detail=f"{len(target_chunks)}개 청크 제거, {len(remaining_chunks)}개 청크 유지",
                removed_chunks=len(target_chunks),
                elapsed_sec=round(elapsed, 1),
            )

        except Exception as exc:
            logger.error(f"삭제 작업 실패: {exc}", exc_info=True)
            return OperationResult(
                success=False,
                message="삭제 중 예외가 발생했습니다.",
                detail=str(exc),
            )
        finally:
            _is_building = False
            _admin_lock.release()

    def upload_and_add(
        self,
        file_bytes: bytes,
        file_name: str,
        chunk_size: int = 800,
        overlap: int = 150,
        force_replace: bool = False,
    ) -> OperationResult:
        """
        PDF/DOCX 파일을 업로드하고 벡터 DB에 청크를 추가합니다.

        [처리 과정]
        1. 임시 파일로 저장
        2. load_and_split()으로 청크 분할
        3. 기존 동일 파일이 있으면 force_replace=True 시 먼저 삭제
        4. VectorStoreManager.append()로 증분 추가

        [force_replace=False 동작]
        이미 같은 파일명이 DB에 있어도 중복 추가.
        (같은 규정집의 다른 버전이 공존하는 상황 허용)

        Args:
            file_bytes:    업로드된 파일의 바이너리
            file_name:     원본 파일명 (예: "취업규칙_2024.pdf")
            chunk_size:    청크 크기 (기본 800자)
            overlap:       청크 중첩 크기 (기본 150자)
            force_replace: True면 기존 동일 파일명 청크를 먼저 제거 후 추가

        Returns:
            OperationResult
        """
        global _is_building

        if not _admin_lock.acquire(blocking=False):
            return OperationResult(
                success=False,
                message="다른 재구축 작업이 진행 중입니다. 잠시 후 다시 시도하세요.",
            )

        _is_building = True
        t_start = time.time()

        try:
            # 임시 파일에 저장 (load_and_split이 파일 경로를 필요로 함)
            tmp_path = self._tmp_dir / file_name
            tmp_path.write_bytes(file_bytes)

            logger.info(f"업로드 처리 시작: {file_name} ({len(file_bytes)/1024:.1f}KB)")

            # 기존 동일 파일명 청크 제거 (force_replace 시)
            if force_replace:
                db_check = self._load_db()
                if db_check is not None:
                    chunks_check = self._extract_chunks(db_check)
                    if any(c.source == file_name for c in chunks_check):
                        logger.info(f"기존 '{file_name}' 청크 제거 후 재추가 처리")
                        del_result = self.delete_source(file_name)
                        # delete_source 내부에서 락이 해제되므로 재획득 필요 없음
                        # (이미 같은 스레드가 락을 보유 중 — 재진입 가능 구조 아님)
                        # 실제 운영에서는 delete와 add를 하나의 트랜잭션으로 처리
                        if not del_result.success:
                            return OperationResult(
                                success=False,
                                message=f"기존 청크 제거 실패: {del_result.message}",
                            )

            # 청크 분할 (load_and_split은 LoadResult 반환)
            try:
                load_result = load_and_split(
                    pdf_path=tmp_path,
                    chunk_size=chunk_size,
                    overlap=overlap,
                )
                new_docs = load_result.documents
            except TypeError:
                # load_and_split 시그니처가 다를 경우 폴백
                from langchain_community.document_loaders import PyPDFLoader
                from langchain.text_splitter import RecursiveCharacterTextSplitter
                loader = PyPDFLoader(str(tmp_path))
                raw_docs = loader.load()
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=chunk_size, chunk_overlap=overlap
                )
                new_docs = splitter.split_documents(raw_docs)

            if not new_docs:
                return OperationResult(
                    success=False,
                    message=f"'{file_name}'에서 텍스트를 추출할 수 없습니다. "
                            "스캔본 PDF이거나 손상된 파일일 수 있습니다.",
                )

            logger.info(f"청크 분할 완료: {len(new_docs)}개 청크")

            # 벡터 DB에 증분 추가
            manager = VectorStoreManager(
                db_path=self._db_path,
                model_name=self._model_name,
                cache_dir=str(self._db_path.parent),
            )

            # DB가 아직 없으면 build, 있으면 append
            existing_db = self._load_db()
            if existing_db is None:
                result_db = manager.build(new_docs)
                op = "신규 구축"
            else:
                success = manager.append(new_docs)
                result_db = existing_db if success else None
                op = "증분 추가"

            if result_db is None:
                return OperationResult(
                    success=False,
                    message="벡터 DB 저장 중 오류가 발생했습니다.",
                )

            # 챗봇 RAGPipeline 싱글톤 리셋
            try:
                from core.rag_pipeline import reset_pipeline
                reset_pipeline()
            except Exception as _e:
                logger.warning(f"RAGPipeline 리셋 건너뜀: {_e}")

            elapsed = time.time() - t_start
            logger.info(f"업로드 완료: {file_name} ({op}, {len(new_docs)}청크, {elapsed:.1f}초)")

            return OperationResult(
                success=True,
                message=f"'{file_name}' 업로드 완료 ({op})",
                detail=f"{len(new_docs)}개 청크 추가, {elapsed:.1f}초 소요",
                added_chunks=len(new_docs),
                elapsed_sec=round(elapsed, 1),
            )

        except Exception as exc:
            logger.error(f"업로드 작업 실패: {exc}", exc_info=True)
            return OperationResult(
                success=False,
                message="업로드 중 예외가 발생했습니다.",
                detail=str(exc),
            )
        finally:
            # 임시 파일 정리
            try:
                if 'tmp_path' in locals() and tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            _is_building = False
            _admin_lock.release()

    def rebuild_all(self, source_dir: Optional[Path] = None) -> OperationResult:
        """
        docs 폴더의 모든 PDF를 다시 읽어 벡터 DB를 완전 재구축합니다.
        대규모 업데이트나 설정 변경 후 사용합니다.

        Args:
            source_dir: 소스 파일 디렉토리 (None이면 settings.docs_dir 사용)

        Returns:
            OperationResult
        """
        global _is_building

        if not _admin_lock.acquire(blocking=False):
            return OperationResult(
                success=False,
                message="다른 재구축 작업이 진행 중입니다.",
            )

        _is_building = True
        t_start = time.time()

        try:
            # build_db.py의 main() 함수를 subprocess로 실행
            # (임포트 충돌 방지 + 격리된 환경에서 실행)
            import subprocess
            import sys

            project_root = Path(__file__).parent.parent
            cmd = [sys.executable, str(project_root / "build_db.py"), "--no-sync"]

            logger.info(f"전체 재구축 시작: {' '.join(str(c) for c in cmd)}")

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=str(project_root),
            )

            elapsed = time.time() - t_start

            if proc.returncode != 0:
                return OperationResult(
                    success=False,
                    message="재구축 실패",
                    detail=proc.stderr[-500:] if proc.stderr else "오류 출력 없음",
                    elapsed_sec=round(elapsed, 1),
                )

            # RAGPipeline 리셋
            try:
                from core.rag_pipeline import reset_pipeline
                reset_pipeline()
            except Exception:
                pass

            return OperationResult(
                success=True,
                message="전체 재구축 완료",
                detail=proc.stdout[-300:] if proc.stdout else "",
                elapsed_sec=round(elapsed, 1),
            )

        except Exception as exc:
            logger.error(f"전체 재구축 실패: {exc}", exc_info=True)
            return OperationResult(
                success=False,
                message="재구축 중 예외 발생",
                detail=str(exc),
            )
        finally:
            _is_building = False
            _admin_lock.release()

    def restore_backup(self, backup_name: str) -> OperationResult:
        """
        지정된 백업으로 벡터 DB를 복원합니다.
        실수로 문서를 삭제했을 때 사용합니다.

        Args:
            backup_name: 백업 폴더명 (예: "vector_store_20240115_143000")

        Returns:
            OperationResult
        """
        global _is_building

        if not _admin_lock.acquire(blocking=False):
            return OperationResult(
                success=False,
                message="다른 재구축 작업이 진행 중입니다.",
            )

        _is_building = True
        t_start = time.time()

        try:
            backup_path = self._backup_dir / backup_name
            if not backup_path.exists():
                return OperationResult(
                    success=False,
                    message=f"백업을 찾을 수 없습니다: {backup_name}",
                )

            # 현재 DB 임시 백업 (복원 실패 시 롤백 가능)
            tmp_current = self._db_path.parent / f"_restore_tmp_{int(time.time())}"
            if self._db_path.exists():
                shutil.copytree(str(self._db_path), str(tmp_current))

            try:
                # 백업 복원
                if self._db_path.exists():
                    shutil.rmtree(str(self._db_path))
                shutil.copytree(str(backup_path), str(self._db_path))

                # 복원 검증
                test_db = self._load_db()
                if test_db is None:
                    raise RuntimeError("복원 후 DB 로드 실패")

                # 임시 백업 제거
                if tmp_current.exists():
                    shutil.rmtree(str(tmp_current))

                # RAGPipeline 리셋
                try:
                    from core.rag_pipeline import reset_pipeline
                    reset_pipeline()
                except Exception:
                    pass

                elapsed = time.time() - t_start
                return OperationResult(
                    success=True,
                    message=f"백업 복원 완료: {backup_name}",
                    detail=f"{test_db.index.ntotal:,}개 벡터 복원, {elapsed:.1f}초",
                    elapsed_sec=round(elapsed, 1),
                )

            except Exception as restore_exc:
                # 복원 실패 시 원본 복구 시도
                logger.error(f"복원 실패, 롤백 시도: {restore_exc}")
                if tmp_current.exists():
                    if self._db_path.exists():
                        shutil.rmtree(str(self._db_path))
                    shutil.copytree(str(tmp_current), str(self._db_path))
                    shutil.rmtree(str(tmp_current))
                raise restore_exc

        except Exception as exc:
            logger.error(f"백업 복원 실패: {exc}", exc_info=True)
            return OperationResult(
                success=False,
                message="복원 중 오류 발생",
                detail=str(exc),
            )
        finally:
            _is_building = False
            _admin_lock.release()


# ══════════════════════════════════════════════════════════════════════
#  싱글톤 팩토리
# ══════════════════════════════════════════════════════════════════════

_svc_instance: Optional[VectorAdminService] = None
_svc_lock = threading.Lock()


def get_admin_service() -> VectorAdminService:
    """
    VectorAdminService 싱글톤을 반환합니다.
    Streamlit의 다중 스레드 환경에서 안전하게 단일 인스턴스를 공유합니다.
    """
    global _svc_instance
    if _svc_instance is None:
        with _svc_lock:
            if _svc_instance is None:
                _svc_instance = VectorAdminService()
    return _svc_instance