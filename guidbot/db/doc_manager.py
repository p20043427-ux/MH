"""
db/doc_manager.py ─ 엔터프라이즈 문서 관리 시스템 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[PM 설계 배경]

병원 RAG 시스템은 3종류의 지식 소스를 다룹니다:

  1. 업무 규정집 (PDF)       → 현재 faiss_db/ 에 저장
  2. 테이블 명세서 (문서)   → RAG_ACCESS_CONFIG + schema_db/
  3. 쿼리 예제집 (SQL)       → ❌ 현재 관리 체계 없음

[문제점 분석]

  · 쿼리 예제와 테이블 문서를 파일로 관리하면 버전 추적 불가
  · 같은 테이블에 대해 RAG_ACCESS_CONFIG, schema_db, 업로드 파일이 따로 놈
  · 새 문서 업로드 시 어떤 인덱스를 재구축해야 하는지 불명확
  · 검색 시 어떤 소스를 우선해야 하는지 기준 없음

[설계 원칙 — 단일 진실 공급원 (Single Source of Truth)]

                  ┌─────────────────────────────────┐
                  │      DocManager (이 모듈)        │
                  │  · 문서 등록/수정/삭제            │
                  │  · 카테고리/태그 관리             │
                  │  · 버전 이력 추적                 │
                  │  · 벡터 인덱스 자동 동기화        │
                  └──────────────┬──────────────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            ▼                    ▼                    ▼
     ┌─────────────┐   ┌─────────────────┐   ┌─────────────┐
     │  규정집      │   │  테이블 명세서   │   │  쿼리 예제   │
     │  (PDF→텍스트)│   │  (Markdown/SQL) │   │  (SQL+설명) │
     │  faiss_db/  │   │  schema_db/     │   │  query_db/  │
     └─────────────┘   └─────────────────┘   └─────────────┘

[파일 시스템 구조]

  guidbot/
  ├── docs/                        ← 원본 문서 저장소
  │   ├── regulations/             ← 업무 규정집 (PDF, DOCX)
  │   │   └── 원무과_업무규정_v2024.pdf
  │   ├── db_specs/                ← 테이블 명세서 (Markdown, XLSX)
  │   │   └── EMIHPTMI_응급환자테이블.md
  │   └── query_library/           ← 쿼리 예제집 (SQL, MD)
  │       └── 응급실_통계쿼리_예제.sql
  │
  ├── vector_store/
  │   ├── faiss_db/                ← 규정집 벡터 인덱스
  │   ├── schema_db/               ← 테이블 명세 벡터 인덱스
  │   └── query_db/                ← 쿼리 예제 벡터 인덱스
  │
  └── doc_registry.json            ← 문서 등록 메타데이터 (이 모듈이 관리)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  문서 카테고리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class DocCategory(str, Enum):
    REGULATION = "regulation"  # 업무 규정집
    DB_SPEC = "db_spec"  # DB 테이블 명세서
    QUERY_LIB = "query_library"  # SQL 쿼리 예제집
    SYSTEM_GUIDE = "system_guide"  # 시스템 사용 안내
    OTHER = "other"  # 기타


# 카테고리 → 저장 디렉토리, 벡터 인덱스 타입 매핑
_CAT_CONFIG: Dict[str, Dict] = {
    DocCategory.REGULATION: {"dir": "regulations", "vector": "faiss_db"},
    DocCategory.DB_SPEC: {"dir": "db_specs", "vector": "schema_db"},
    DocCategory.QUERY_LIB: {"dir": "query_library", "vector": "query_db"},
    DocCategory.SYSTEM_GUIDE: {"dir": "system_guide", "vector": "faiss_db"},
    DocCategory.OTHER: {"dir": "other", "vector": "faiss_db"},
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  데이터 클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class DocMeta:
    """
    등록된 문서의 메타데이터.

    doc_registry.json 에 저장되며 문서의 전체 생명주기를 추적합니다.
    """

    doc_id: str  # UUID 기반 고유 ID
    title: str  # 문서 제목 (화면 표시용)
    category: str  # DocCategory 값
    file_path: str  # 저장된 파일 경로 (상대 경로)
    file_name: str  # 원본 파일명
    file_size: int  # 파일 크기 (bytes)
    file_hash: str  # SHA-256 해시 (중복 감지 + 변경 감지)
    tags: List[str]  # 검색용 태그 (테이블명, 부서명 등)
    description: str  # 문서 설명
    created_at: str  # 최초 등록 시각 (ISO 형식)
    updated_at: str  # 최종 수정 시각
    uploaded_by: str  # 업로더 (직원번호 또는 "admin")
    is_active: bool  # 활성 여부 (비활성 = 검색 제외)
    version: int  # 버전 번호 (1부터 시작)
    vector_indexed: bool  # 벡터 인덱스 반영 여부
    vector_indexed_at: str  # 인덱싱 시각


@dataclass
class DocUploadResult:
    """문서 업로드 결과."""

    success: bool
    doc_id: str = ""
    message: str = ""
    duplicate: bool = False  # 동일 파일 이미 존재
    old_doc_id: str = ""  # 중복 시 기존 doc_id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DocManager
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class DocManager:
    """
    병원 가이드봇 문서 관리 시스템.

    [핵심 기능]
    1. 문서 업로드 — 카테고리별 저장 + 해시 중복 감지
    2. 메타데이터 관리 — doc_registry.json 자동 갱신
    3. 벡터 인덱스 동기화 — 카테고리별 자동 재구축 스케줄링
    4. 버전 관리 — 동일 제목 문서 교체 시 버전 번호 증가
    5. 검색 — 제목/태그/카테고리 기반 빠른 조회
    """

    def __init__(self) -> None:
        # 기본 경로 설정
        _base = Path(settings.rag_db_path).parent  # guidbot/vector_store 의 부모
        self._docs_root = _base / "docs"
        self._registry_file = _base / "doc_registry.json"
        self._registry: Dict[str, DocMeta] = {}
        self._loaded_at: float = 0.0

        # 디렉토리 초기화
        for cat_cfg in _CAT_CONFIG.values():
            (self._docs_root / cat_cfg["dir"]).mkdir(parents=True, exist_ok=True)

        self._load_registry()

    # ── 레지스트리 로드/저장 ──────────────────────────────────────

    def _load_registry(self) -> None:
        """doc_registry.json 에서 메타데이터 로드."""
        if not self._registry_file.exists():
            self._registry = {}
            return
        try:
            with open(self._registry_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._registry = {k: DocMeta(**v) for k, v in raw.items()}
            self._loaded_at = time.time()
            logger.debug(f"문서 레지스트리 로드: {len(self._registry)}개 문서")
        except Exception as exc:
            logger.error(f"레지스트리 로드 실패: {exc}")
            self._registry = {}

    def _save_registry(self) -> None:
        """메타데이터를 doc_registry.json 에 저장."""
        try:
            with open(self._registry_file, "w", encoding="utf-8") as f:
                json.dump(
                    {k: asdict(v) for k, v in self._registry.items()},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            logger.error(f"레지스트리 저장 실패: {exc}")

    # ── 유틸리티 ──────────────────────────────────────────────────

    @staticmethod
    def _compute_hash(data: bytes) -> str:
        """파일 내용의 SHA-256 해시 계산."""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _make_doc_id() -> str:
        """타임스탬프 기반 고유 ID 생성 (UUID 대신 — 가독성 우선)."""
        import uuid

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        short = str(uuid.uuid4())[:8]
        return f"DOC_{ts}_{short}"

    def _find_by_hash(self, file_hash: str) -> Optional[DocMeta]:
        """파일 해시로 기존 문서 탐색."""
        for doc in self._registry.values():
            if doc.file_hash == file_hash and doc.is_active:
                return doc
        return None

    def _find_by_title_category(self, title: str, category: str) -> Optional[DocMeta]:
        """같은 제목 + 카테고리의 기존 문서 탐색 (버전 업 대상)."""
        for doc in self._registry.values():
            if doc.title == title and doc.category == category and doc.is_active:
                return doc
        return None

    # ── 업로드 ────────────────────────────────────────────────────

    def upload(
        self,
        file_data: bytes,
        file_name: str,
        title: str,
        category: str,
        tags: List[str] = None,
        description: str = "",
        uploaded_by: str = "admin",
        force_update: bool = False,
    ) -> DocUploadResult:
        """
        문서를 등록합니다.

        [처리 흐름]
        1. SHA-256 해시 계산 → 동일 파일 중복 체크
        2. 같은 제목 문서 있으면 → 버전 업(기존 비활성화, 새 버전 등록)
        3. 카테고리별 디렉토리에 저장
        4. 레지스트리 갱신
        5. 벡터 인덱스 재구축 플래그 설정

        Args:
            file_data:    파일 바이너리
            file_name:    원본 파일명 (확장자 포함)
            title:        표시용 문서 제목
            category:     DocCategory 값
            tags:         검색용 태그 목록 (테이블명, 부서 등)
            description:  문서 설명
            uploaded_by:  업로더 식별자
            force_update: True 이면 동일 파일도 재등록

        Returns:
            DocUploadResult
        """
        tags = tags or []
        file_hash = self._compute_hash(file_data)

        # 중복 체크
        existing = self._find_by_hash(file_hash)
        if existing and not force_update:
            logger.info(f"중복 파일 감지: {file_name} = {existing.doc_id}")
            return DocUploadResult(
                success=False,
                doc_id=existing.doc_id,
                message=f"동일한 파일이 이미 등록되어 있습니다: [{existing.title}]",
                duplicate=True,
                old_doc_id=existing.doc_id,
            )

        # 버전 관리: 같은 제목+카테고리 문서 이전 버전 비활성화
        version = 1
        old_doc = self._find_by_title_category(title, category)
        if old_doc:
            old_doc.is_active = False
            old_doc.updated_at = datetime.now().isoformat()
            version = old_doc.version + 1
            logger.info(f"버전 업: [{title}] v{old_doc.version} → v{version}")

        # 파일 저장 경로 결정
        cat_cfg = _CAT_CONFIG.get(category, _CAT_CONFIG[DocCategory.OTHER])
        save_dir = self._docs_root / cat_cfg["dir"]
        # 파일명 충돌 방지: 제목_v버전_원본파일명
        safe_title = "".join(c if c.isalnum() or c in ("-_") else "_" for c in title)
        save_name = f"{safe_title}_v{version}_{file_name}"
        save_path = save_dir / save_name

        try:
            with open(save_path, "wb") as f:
                f.write(file_data)
        except Exception as exc:
            return DocUploadResult(success=False, message=f"파일 저장 실패: {exc}")

        # 메타데이터 등록
        now = datetime.now().isoformat()
        doc_id = self._make_doc_id()
        meta = DocMeta(
            doc_id=doc_id,
            title=title,
            category=category,
            file_path=str(save_path.relative_to(self._docs_root)),
            file_name=file_name,
            file_size=len(file_data),
            file_hash=file_hash,
            tags=tags,
            description=description,
            created_at=now,
            updated_at=now,
            uploaded_by=uploaded_by,
            is_active=True,
            version=version,
            vector_indexed=False,  # 업로드 직후는 미인덱싱
            vector_indexed_at="",
        )
        self._registry[doc_id] = meta
        self._save_registry()

        logger.info(
            f"문서 등록 완료: [{title}] v{version} "
            f"({len(file_data):,}bytes, {category}, id={doc_id})"
        )
        return DocUploadResult(
            success=True, doc_id=doc_id, message=f"등록 완료: [{title}] v{version}"
        )

    # ── 조회 ──────────────────────────────────────────────────────

    def list_docs(
        self,
        category: str = "",
        tags: List[str] = None,
        active_only: bool = True,
        search: str = "",
    ) -> List[DocMeta]:
        """
        등록된 문서 목록을 반환합니다.

        Args:
            category:    카테고리 필터 (빈 문자열 = 전체)
            tags:        태그 필터 (OR 조건 — 하나라도 포함)
            active_only: True 이면 비활성 문서 제외
            search:      제목/설명 전문 검색 (소문자 포함 검사)

        Returns:
            최신 업데이트 순 정렬된 DocMeta 리스트
        """
        result = []
        for doc in self._registry.values():
            if active_only and not doc.is_active:
                continue
            if category and doc.category != category:
                continue
            if tags and not any(
                t.lower() in [x.lower() for x in doc.tags] for t in tags
            ):
                continue
            if search:
                q = search.lower()
                if q not in doc.title.lower() and q not in doc.description.lower():
                    continue
            result.append(doc)
        return sorted(result, key=lambda d: d.updated_at, reverse=True)

    def get_doc(self, doc_id: str) -> Optional[DocMeta]:
        """doc_id 로 단일 문서 조회."""
        return self._registry.get(doc_id)

    def get_file_path(self, doc_id: str) -> Optional[Path]:
        """doc_id 의 실제 파일 경로 반환."""
        doc = self.get_doc(doc_id)
        if doc:
            return self._docs_root / doc.file_path
        return None

    # ── 삭제/비활성화 ─────────────────────────────────────────────

    def deactivate(self, doc_id: str, reason: str = "") -> bool:
        """
        문서를 비활성화합니다 (물리적 삭제 없음 — 복구 가능).

        RAG 검색에서 제외되지만 파일은 보존됩니다.
        """
        doc = self._registry.get(doc_id)
        if not doc:
            return False
        doc.is_active = False
        doc.updated_at = datetime.now().isoformat()
        doc.description += f" [비활성: {reason or '관리자 처리'}]"
        self._save_registry()
        logger.info(f"문서 비활성화: {doc_id} ({doc.title})")
        return True

    # ── 벡터 인덱스 동기화 ────────────────────────────────────────

    def get_pending_index_docs(self, category: str = "") -> List[DocMeta]:
        """
        벡터 인덱스 미반영 문서 목록 반환.

        build_db.py 또는 관리자 UI 에서 호출하여 재구축 대상 파악.
        """
        pending = []
        for doc in self._registry.values():
            if not doc.is_active:
                continue
            if category and doc.category != category:
                continue
            if not doc.vector_indexed:
                pending.append(doc)
        return pending

    def mark_indexed(self, doc_ids: List[str]) -> None:
        """벡터 인덱스 반영 완료 표시."""
        now = datetime.now().isoformat()
        for doc_id in doc_ids:
            if doc_id in self._registry:
                self._registry[doc_id].vector_indexed = True
                self._registry[doc_id].vector_indexed_at = now
        self._save_registry()
        logger.info(f"인덱싱 완료 표시: {len(doc_ids)}개 문서")

    def needs_reindex(self, category: str = "") -> bool:
        """해당 카테고리에 인덱스 미반영 문서가 있는지 확인."""
        return len(self.get_pending_index_docs(category)) > 0

    # ── 통계 ──────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """문서 등록 현황 통계."""
        active = [d for d in self._registry.values() if d.is_active]
        by_cat: Dict[str, int] = {}
        for doc in active:
            by_cat[doc.category] = by_cat.get(doc.category, 0) + 1
        total_size = sum(d.file_size for d in active)

        return {
            "total": len(active),
            "by_category": by_cat,
            "unindexed": len(self.get_pending_index_docs()),
            "total_size_mb": round(total_size / 1024 / 1024, 2),
        }

    # ── 쿼리 예제 특화 기능 ───────────────────────────────────────

    def search_query_examples(self, question: str, top_k: int = 3) -> List[Dict]:
        """
        질문과 관련된 SQL 쿼리 예제를 반환합니다.

        벡터 검색 미구축 시 태그/제목 기반 키워드 매칭으로 폴백.
        """
        # 우선 벡터 검색 시도
        try:
            from db.schema_vector_store import search_schema_context

            results = search_schema_context(question, k_tables=top_k)
            if results:
                return [{"source": "vector", "content": results}]
        except Exception:
            pass

        # 폴백: 태그/제목 키워드 매칭
        q_tokens = set(question.lower().split())
        scored: List[Tuple[float, DocMeta]] = []
        for doc in self.list_docs(category=DocCategory.QUERY_LIB):
            score = 0.0
            # 제목 매칭
            for tok in q_tokens:
                if tok in doc.title.lower():
                    score += 2.0
            # 태그 매칭
            for tag in doc.tags:
                if any(tok in tag.lower() for tok in q_tokens):
                    score += 1.5
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "source": "keyword",
                "score": s,
                "doc_id": d.doc_id,
                "title": d.title,
                "tags": d.tags,
                "file_path": str(self.get_file_path(d.doc_id)),
            }
            for s, d in scored[:top_k]
        ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  싱글톤 인스턴스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DOC_MANAGER: Optional[DocManager] = None


def get_doc_manager() -> DocManager:
    """DocManager 싱글톤 반환 (최초 호출 시 초기화)."""
    global _DOC_MANAGER
    if _DOC_MANAGER is None:
        _DOC_MANAGER = DocManager()
    return _DOC_MANAGER
