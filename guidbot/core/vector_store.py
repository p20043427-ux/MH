"""
core/vector_store.py ─ FAISS 벡터 DB 관리 (v3.0)

[v3.0 수정사항]
- P-01 버그 수정 (CRITICAL): _build_in_batches() 에서 embeddings 캐시
  이전: for 루프 안에서 self._embeddings 프로퍼티를 매 배치마다 호출
        → get_embeddings_auto() → detect_device() 가 배치마다 반복 실행
        → torch.cuda.is_available() 이 100개 문서 기준 10번 불필요 호출
  수정: 루프 진입 전 emb = self._embeddings 로 1회 캐시

- 자동 백업 기능 추가: 전체 재구축 시 기존 DB 를 타임스탬프 이름으로 백업
  → settings.backup_dir 에 최근 5개 보관 (오래된 것 자동 삭제)

[설계 원칙]
- CRUD 완전 캡슐화: load / build / append 를 단일 클래스에서 관리
- 배치 처리: 대용량 문서도 OOM 없이 처리 (batch_size=100 기본)
- 반환 타입 명시: Optional[FAISS] → 호출부에서 None 체크 강제
- 증분 업데이트: append() 로 전체 재구축 없이 신규 문서만 추가 가능

[FAISS 직렬화 보안 주의]
FAISS 는 내부적으로 pickle 을 사용하여 저장합니다.
allow_dangerous_deserialization=True 를 설정하는 이유:
- 이 파일은 외부에서 받은 파일이 아닌 우리가 직접 생성한 로컬 파일입니다.
- 신뢰할 수 없는 FAISS 파일을 로드하는 경우에는 절대 True 로 설정하지 마세요.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS

from config.settings import settings
from core.embeddings import get_embeddings_auto
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


class VectorStoreManager:
    """
    FAISS 벡터 DB 생성 / 로드 / 증분 업데이트 관리자.

    [사용 예시]
        manager = VectorStoreManager(
            db_path=settings.rag_db_path,
            model_name=settings.embedding_model,
            cache_dir=str(settings.local_cache_path),
        )

        # 기존 DB 로드
        db = manager.load()

        # 전체 재구축 (기존 DB 자동 백업 후 새로 빌드)
        db = manager.build(all_docs)

        # 증분 추가 (전체 재구축 없이 신규 문서만 추가)
        success = manager.append(new_docs)
    """

    def __init__(
        self,
        db_path: Path,
        model_name: str,
        cache_dir: str,
        batch_size: int = 100,
    ) -> None:
        """
        Args:
            db_path:    FAISS DB 저장 경로 (index.faiss, index.pkl 이 생성됨)
            model_name: HuggingFace 임베딩 모델명 (현재는 get_embeddings_auto 로 자동 처리)
            cache_dir:  모델 캐시 디렉토리 (현재는 settings 에서 관리)
            batch_size: 임베딩 배치 크기. OOM 방지를 위해 이 단위로 나눠 처리
        """
        self._db_path = db_path
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._batch_size = batch_size

    # ──────────────────────────────────────────────────────────────────
    #  임베딩 모델 (지연 로드 + 캐시)
    # ──────────────────────────────────────────────────────────────────

    @property
    def _embeddings(self):
        """
        임베딩 모델 프로퍼티 (지연 초기화).

        [P-01 수정 관련]
        이 프로퍼티 자체는 매번 get_embeddings_auto() 를 호출하지만,
        get_embeddings_auto() 내부에서 @lru_cache 로 캐시되므로
        실제 모델 로딩은 최초 1회만 발생합니다.
        단, _build_in_batches() 의 배치 루프 안에서 이 프로퍼티를 직접 호출하면
        루프마다 get_embeddings_auto() 를 거치는 오버헤드가 있었습니다.
        → _build_in_batches() 에서는 루프 전에 emb = self._embeddings 로 1회 캐시.
        """
        return get_embeddings_auto()

    # ──────────────────────────────────────────────────────────────────
    #  Public API
    # ──────────────────────────────────────────────────────────────────

    def load(self) -> Optional[FAISS]:
        """
        저장된 FAISS 벡터 DB 를 로드합니다.

        [allow_dangerous_deserialization 설명]
        FAISS 는 pickle 포맷으로 저장됩니다.
        LangChain 0.1.0 부터 보안 경고와 함께 이 옵션이 명시적으로 요구됩니다.
        우리가 직접 생성한 로컬 파일이므로 True 로 설정합니다.
        외부에서 받은 FAISS 파일을 로드할 때는 절대 True 로 설정하지 마세요.

        Returns:
            FAISS 인스턴스. DB 파일이 없으면 None.
        """
        index_path = self._db_path / "index.faiss"
        if not index_path.exists():
            logger.warning(
                f"벡터 DB 파일 없음: {index_path}\n"
                f"  → build_db.py 를 먼저 실행하세요."
            )
            return None

        try:
            db = FAISS.load_local(
                str(self._db_path),
                self._embeddings,
                allow_dangerous_deserialization=True,
            )
            doc_count = db.index.ntotal
            logger.info(f"벡터 DB 로드 완료: {self._db_path} ({doc_count:,}개 벡터)")
            return db

        except Exception as exc:
            logger.error(f"벡터 DB 로드 실패: {exc}", exc_info=True)
            return None

    def build(self, documents: List[Document]) -> Optional[FAISS]:
        """
        문서 리스트로 벡터 DB 를 전체 재구축합니다.

        [처리 순서]
        1. 기존 DB 자동 백업 (settings.backup_dir 에 타임스탬프 이름)
        2. 배치 단위 임베딩 처리 (OOM 방지)
        3. 배치별 FAISS 인덱스 생성 후 merge_from 으로 병합
        4. 최종 DB 파일 저장

        [배치 처리 필요 이유]
        문서 1,000개를 한 번에 임베딩하면 메모리 사용량이 급격히 증가합니다.
        batch_size=100 으로 나눠 처리 후 merge_from 으로 합산합니다.
        GPU 환경에서는 batch_size 를 200~500 으로 늘려 속도 개선 가능합니다.

        Args:
            documents: LangChain Document 리스트 (청크 분할 완료된 것)

        Returns:
            생성된 FAISS 인스턴스. 실패 시 None.
        """
        if not documents:
            logger.error("빌드할 문서가 없습니다. (documents 리스트가 비어 있음)")
            return None

        # 기존 DB 백업 (전체 재구축 전 안전망)
        self._backup_existing()

        try:
            logger.info(f"벡터 DB 빌드 시작: {len(documents):,}개 문서")
            vectorstore = self._build_in_batches(documents)
            self._save(vectorstore)
            logger.info(f"벡터 DB 빌드 완료: {vectorstore.index.ntotal:,}개 벡터")
            return vectorstore

        except Exception as exc:
            logger.error(f"벡터 DB 빌드 실패: {exc}", exc_info=True)
            return None

    def append(self, new_documents: List[Document]) -> bool:
        """
        기존 DB 에 신규 문서를 추가합니다 (증분 업데이트).

        [전체 재구축 vs 증분 업데이트]
        - 전체 재구축: 모든 PDF 를 다시 색인. 정확하지만 시간 소요.
        - 증분 업데이트: 기존 DB 에 새 문서만 추가. 빠르지만 삭제는 불가.
          → 관리자가 신규 규정집 몇 개를 추가할 때 적합.

        [동작]
        기존 DB 가 없으면 자동으로 build() 로 전환합니다.
        기존 DB 가 있으면 새 문서를 벡터화 후 merge_from 으로 병합합니다.

        Args:
            new_documents: 추가할 Document 리스트

        Returns:
            True: 성공 | False: 실패
        """
        if not new_documents:
            logger.warning("추가할 문서가 없습니다.")
            return False

        existing = self.load()

        try:
            if existing is None:
                logger.info("기존 DB 없음 → 신규 빌드로 전환")
                return self.build(new_documents) is not None

            # 신규 문서 배치 벡터화
            logger.info(f"증분 업데이트 시작: {len(new_documents):,}개 신규 문서")
            new_db = self._build_in_batches(new_documents)

            # 기존 DB 에 병합
            before_count = existing.index.ntotal
            existing.merge_from(new_db)
            after_count = existing.index.ntotal
            self._save(existing)

            logger.info(
                f"증분 업데이트 완료: "
                f"{before_count:,} → {after_count:,}개 벡터 "
                f"(+{after_count - before_count:,}개)"
            )
            return True

        except Exception as exc:
            logger.error(f"증분 업데이트 실패: {exc}", exc_info=True)
            return False

    # ──────────────────────────────────────────────────────────────────
    #  Private 헬퍼
    # ──────────────────────────────────────────────────────────────────

    def _build_in_batches(self, documents: List[Document]) -> FAISS:
        """
        배치 단위로 임베딩 후 FAISS 인덱스를 병합합니다.

        [P-01 버그 수정]
        이전 코드:
            for batch in batches:
                batch_store = FAISS.from_documents(batch, self._embeddings)
                                                         ^^^^^^^^^^^^^^^^
                self._embeddings 프로퍼티를 루프마다 호출
                → get_embeddings_auto() → detect_device() 반복 실행

        수정 코드:
            emb = self._embeddings  ← 루프 진입 전 1회만 캐시
            for batch in batches:
                batch_store = FAISS.from_documents(batch, emb)

        [merge_from 동작]
        FAISS 의 merge_from 은 두 인덱스를 O(n) 으로 병합합니다.
        인덱스 타입이 동일해야 합니다 (둘 다 IndexFlatL2 등).
        LangChain 의 FAISS.from_documents 는 기본으로 IndexFlatL2 를 생성하므로 호환됩니다.

        Args:
            documents: 임베딩할 Document 리스트

        Returns:
            병합 완료된 FAISS 인스턴스
        """
        total = len(documents)
        total_batches = (total + self._batch_size - 1) // self._batch_size

        # [P-01 수정 핵심] 루프 전 임베딩 모델 1회만 가져오기
        # get_embeddings_auto() 가 @lru_cache 로 캐시되어 있어도
        # 루프 안에서 self._embeddings 를 매번 호출하면 프로퍼티 접근 + 함수 호출 오버헤드 발생
        # → 루프 전 로컬 변수에 한 번만 할당
        emb = self._embeddings

        vectorstore: Optional[FAISS] = None

        for batch_idx, start in enumerate(range(0, total, self._batch_size), start=1):
            batch = documents[start : start + self._batch_size]
            logger.info(
                f"  임베딩 배치 {batch_idx}/{total_batches} "
                f"({len(batch)}개, {start+1}~{min(start+len(batch), total)}/{total})"
            )

            # 배치 임베딩 + FAISS 인덱스 생성
            # [P-01 수정] emb 로컬 변수 사용 (self._embeddings 대신)
            batch_store = FAISS.from_documents(batch, emb)

            if vectorstore is None:
                vectorstore = batch_store
            else:
                # 기존 인덱스에 배치 인덱스 병합
                vectorstore.merge_from(batch_store)

        # 문서가 있으면 반드시 초기화됨 (빈 documents 는 build/append 에서 사전 차단)
        return vectorstore  # type: ignore[return-value]

    def _save(self, vectorstore: FAISS) -> None:
        """벡터 DB 파일을 저장합니다."""
        self._db_path.mkdir(parents=True, exist_ok=True)
        vectorstore.save_local(str(self._db_path))
        logger.info(f"벡터 DB 저장 완료: {self._db_path}")

    def _backup_existing(self) -> None:
        """
        기존 벡터 DB 를 타임스탬프 이름으로 백업합니다.

        [백업 정책]
        - backup_dir / vector_store_YYYYMMDD_HHMMSS 폴더에 복사
        - 최근 5개만 보관, 오래된 것 자동 삭제

        전체 재구축(build) 전에 호출하여 실수로 DB 를 덮어쓰는 경우를 대비합니다.
        """
        index_file = self._db_path / "index.faiss"
        if not index_file.exists():
            return  # 기존 DB 없으면 백업 불필요

        backup_dir = settings.backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)

        # 타임스탬프 이름으로 백업 폴더 생성
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = backup_dir / f"vector_store_{timestamp}"

        try:
            shutil.copytree(str(self._db_path), str(dest))
            logger.info(f"기존 DB 백업 완료: {dest}")

            # 최근 5개만 보관 (오래된 백업 자동 삭제)
            backups = sorted(backup_dir.glob("vector_store_*"))
            if len(backups) > 5:
                for old in backups[:-5]:
                    shutil.rmtree(old)
                    logger.info(f"오래된 백업 삭제: {old.name}")

        except Exception as exc:
            # 백업 실패는 경고로만 처리 (빌드를 막지 않음)
            logger.warning(f"DB 백업 실패 (빌드는 계속 진행): {exc}")
