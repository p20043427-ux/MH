"""
core/rag_pipeline.py  ─  통합 RAG 파이프라인 v7.0  (검색 모드 통합)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v7.0 변경사항]
  · SearchConfig 를 주입받아 모드별 검색 전략 동적 전환
  · run_with_mode()  : 동기 실행 (모드 선택 지원)
  · iter_steps_mode(): Streamlit 진행 표시용 이터레이터 (모드 지원)
  · Fast 모드: CE 리랭킹 생략 → FAISS L2 score 직접 사용
  · Deep 모드: BM25 강제 활성화 + 쿼리 확장 on
  · initialize() 정식 클래스 메서드로 복원 (몽키패치 제거)
  · get_pipeline() 스레드 안전 Double-Checked Locking
  · reset_pipeline() 추가 (build_db.py 호출용)

[타임라인 예상치 (CPU, 7845청크)]
  Fast     :  FAISS top-3             0.5 ~ 1.5초
  Balanced :  FAISS top-10 + CE(10쌍) 2.5 ~ 4.0초
  Deep     :  Hybrid top-20 + CE(20쌍) 4.0 ~ 7.0초
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Generator, Iterator, List, Optional, Tuple

from langchain_community.vectorstores import FAISS

from core.context_builder import build_context, format_source_list
from core.hybrid_retriever import HybridRetriever
from core.query_rewriter import RewriteResult, get_query_rewriter
from core.retriever import (
    FAISS_TOP_K,
    RERANK_TOP_N,
    RankedDocument,
    _faiss_search,
    _rerank,
    clear_cache,
)
from core.search_modes import (
    SearchConfig,
    SearchMode,
    get_default_config,
    get_config,
    BALANCED_CONFIG,
)
from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  결과 데이터클래스  (v7.0: mode_used 필드 추가)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class PipelineResult:
    """RAG 파이프라인 실행 결과."""

    ranked_docs: List[RankedDocument]
    context: str
    rewrite_result: Optional[RewriteResult] = None
    mode_used: str = "balanced"  # SearchMode.value 기록

    # 타이밍 정보 (ms)
    t_rewrite_ms: float = 0.0
    t_search_ms: float = 0.0
    t_rerank_ms: float = 0.0
    t_context_ms: float = 0.0
    t_total_ms: float = 0.0

    @property
    def source_list(self) -> str:
        return format_source_list(self.ranked_docs)

    @property
    def timing_summary(self) -> str:
        return (
            f"[{self.mode_used}] "
            f"쿼리정제 {self.t_rewrite_ms:.0f}ms | "
            f"검색 {self.t_search_ms:.0f}ms | "
            f"리랭킹 {self.t_rerank_ms:.0f}ms | "
            f"컨텍스트 {self.t_context_ms:.0f}ms | "
            f"총 {self.t_total_ms:.0f}ms"
        )

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.context) // 2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RAGPipeline 클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class RAGPipeline:
    """
    검색 모드(Fast / Balanced / Deep)를 지원하는 통합 RAG 파이프라인.

    [검색 전략 분기]
    · Fast     : FAISS top-k → CE 생략 → L2 score 사용
    · Balanced : FAISS top-k → CE 리랭킹 → top-n
    · Deep     : Hybrid(BM25+FAISS) top-k → CE 리랭킹 → top-n
                 + QueryRewriter 확장 쿼리 활성화
    """

    def __init__(self, vector_db: FAISS) -> None:
        self._retriever = HybridRetriever(vector_db)
        self._rewriter = get_query_rewriter()
        logger.info(
            f"RAGPipeline v7.0 초기화 완료 (Hybrid={self._retriever.is_hybrid})"
        )

    def initialize(self) -> None:
        """
        워밍업 메서드 (main.py 에서 pipeline.initialize() 호출).
        __init__ 에서 초기화 완료 → 로그만 출력.
        """
        logger.info(
            f"RAGPipeline.initialize() 호출 (Hybrid={self._retriever.is_hybrid})"
        )

    # ── 핵심 실행 메서드 ───────────────────────────────────────

    def run_with_mode(
        self,
        query: str,
        config: Optional[SearchConfig] = None,
    ) -> PipelineResult:
        """
        검색 모드를 지정한 동기 실행.

        [파이프라인 분기]
        Fast     : QueryRewrite → FAISS(top_k) → Context
        Balanced : QueryRewrite → FAISS(top_k) → CE(top_n) → Context
        Deep     : QueryRewrite(확장) → Hybrid(top_k) → CE(top_n) → Context

        Args:
            query:  사용자 질문
            config: 검색 설정 (None 이면 Balanced 기본값)

        Returns:
            PipelineResult
        """
        cfg = config or get_default_config()
        t_total = time.time()

        # ── Step 1: QueryRewriter ─────────────────────────────
        t0 = time.time()
        rewrite_result = self._rewriter.rewrite(query)

        # Deep 모드: 확장 쿼리 활성화
        if cfg.use_query_expand and rewrite_result.expanded_query:
            search_query = rewrite_result.expanded_query
            logger.debug(f"[Deep] 쿼리 확장: '{query}' → '{search_query}'")
        else:
            search_query = rewrite_result.search_query

        t_rewrite_ms = (time.time() - t0) * 1000

        # ── Step 2: 검색 ─────────────────────────────────────
        t0 = time.time()

        if cfg.use_hybrid:
            # Deep: BM25 + FAISS 하이브리드
            candidates = self._retriever.search(
                search_query,
                top_k=cfg.top_k,
            )
            search_method = "하이브리드(BM25+FAISS)"
        else:
            # Fast / Balanced: FAISS 단독
            candidates = self._retriever.search(
                search_query,
                top_k=cfg.top_k,
            )
            search_method = "FAISS 벡터"

        t_search_ms = (time.time() - t0) * 1000

        if not candidates:
            logger.warning(f"[{cfg.mode.value}] 검색 결과 없음: '{search_query}'")
            return PipelineResult(
                ranked_docs=[],
                context="관련 규정 문서를 찾을 수 없습니다.",
                rewrite_result=rewrite_result,
                mode_used=cfg.mode.value,
                t_rewrite_ms=t_rewrite_ms,
                t_search_ms=t_search_ms,
                t_total_ms=(time.time() - t_total) * 1000,
            )

        # ── Step 3: 리랭킹 (Fast 모드는 생략) ─────────────────
        t0 = time.time()

        if cfg.use_rerank:
            # Balanced / Deep: Cross-Encoder 리랭킹
            ranked_docs = _rerank(
                search_query,
                candidates,
                cfg.rerank_top_n,
            )
        else:
            # Fast: FAISS L2 거리 → 유사도 점수로 직접 변환
            #   · FAISS similarity_search_with_score 는 L2 거리(낮을수록 유사)
            #   · RankedDocument.score 는 높을수록 좋음으로 변환 필요
            #   · 변환식: score = max(0, 1 - L2_distance / 2)
            top_n = min(cfg.rerank_top_n, len(candidates))
            ranked_docs = [
                RankedDocument(
                    document=doc,
                    score=max(0.0, 1.0 - float(l2) / 2.0),
                    rank=i + 1,
                )
                for i, (doc, l2) in enumerate(candidates[:top_n])
            ]

        t_rerank_ms = (time.time() - t0) * 1000

        # ── Step 4: Context 구축 ─────────────────────────────
        t0 = time.time()
        context = build_context(ranked_docs)
        t_context_ms = (time.time() - t0) * 1000

        t_total_ms = (time.time() - t_total) * 1000

        result = PipelineResult(
            ranked_docs=ranked_docs,
            context=context,
            rewrite_result=rewrite_result,
            mode_used=cfg.mode.value,
            t_rewrite_ms=t_rewrite_ms,
            t_search_ms=t_search_ms,
            t_rerank_ms=t_rerank_ms,
            t_context_ms=t_context_ms,
            t_total_ms=t_total_ms,
        )

        logger.info(
            f"RAG 완료 | {result.timing_summary} | "
            f"{search_method} {len(candidates)}건 → "
            f"top{len(ranked_docs)} | "
            f"context ~{result.token_estimate}tok"
        )
        return result

    # ── 하위 호환: 기존 run() ─────────────────────────────────

    def run(
        self,
        query: str,
        top_k: int = FAISS_TOP_K,
        top_n: int = RERANK_TOP_N,
        use_cache: bool = True,
    ) -> PipelineResult:
        """
        기존 호환성 유지용 run() — 내부적으로 run_with_mode(Balanced) 호출.

        기존 코드를 수정하지 않고도 v7.0 으로 업그레이드 가능.
        """
        return self.run_with_mode(query, BALANCED_CONFIG)

    # ── Streamlit 진행 표시용 이터레이터 ──────────────────────

    def iter_steps_mode(
        self,
        query: str,
        config: Optional[SearchConfig] = None,
    ) -> Iterator[Tuple[str, Optional[PipelineResult]]]:
        """
        검색 모드를 지원하는 Streamlit st.status 이터레이터.

        [사용 예시 — main.py]
            cfg = get_config(search_mode)
            with st.status("🔍 검색 중...", expanded=False) as status:
                for msg, result in pipeline.iter_steps_mode(query, cfg):
                    status.update(label=msg)
                    if result:
                        final_result = result
                status.update(label="완료", state="complete")

        Yields:
            (진행 메시지, PipelineResult|None)
            마지막 yield 만 result 가 None 이 아님.
        """
        cfg = config or get_default_config()
        mode_label = cfg.icon + " " + cfg.label.split(" ", 1)[-1]  # 예: "⚡ 빠른 검색"

        # ── Step 1: QueryRewriter ──────────────────────────────
        rewrite_result = self._rewriter.rewrite(query)
        if cfg.use_query_expand and rewrite_result.expanded_query:
            search_query = rewrite_result.expanded_query
            yield (
                f"[{mode_label}] 쿼리 확장: '{query[:20]}' → '{search_query[:30]}'",
                None,
            )
        else:
            search_query = rewrite_result.search_query
            if rewrite_result.was_rewritten:
                yield (
                    f"[{mode_label}] 쿼리 정제: '{query[:20]}' → '{search_query[:30]}'",
                    None,
                )
            else:
                yield f"[{mode_label}] 문서 검색 중...", None

        # ── Step 2: 검색 ──────────────────────────────────────
        t0 = time.time()
        candidates = self._retriever.search(
            search_query,
            top_k=cfg.top_k,
        )
        t_search = time.time() - t0
        search_type = "하이브리드" if cfg.use_hybrid else "벡터"
        yield (
            f"{search_type} 검색 완료: {len(candidates)}건 "
            f"({t_search:.2f}초)" + (" → 관련도 분석 중..." if cfg.use_rerank else ""),
            None,
        )

        # ── Step 3: 리랭킹 ────────────────────────────────────
        t0 = time.time()
        if cfg.use_rerank:
            ranked_docs = _rerank(search_query, candidates, cfg.rerank_top_n)
            t_rerank = time.time() - t0
            yield f"AI 리랭킹 완료: 상위 {len(ranked_docs)}건 ({t_rerank:.2f}초)", None
        else:
            top_n = min(cfg.rerank_top_n, len(candidates))
            ranked_docs = [
                RankedDocument(
                    document=doc,
                    score=max(0.0, 1.0 - float(l2) / 2.0),
                    rank=i + 1,
                )
                for i, (doc, l2) in enumerate(candidates[:top_n])
            ]
            t_rerank = time.time() - t0

        # ── Step 4: Context ────────────────────────────────────
        context = build_context(ranked_docs)
        result = PipelineResult(
            ranked_docs=ranked_docs,
            context=context,
            rewrite_result=rewrite_result,
            mode_used=cfg.mode.value,
            t_search_ms=t_search * 1000,
            t_rerank_ms=t_rerank * 1000,
        )
        tok = result.token_estimate
        yield f"컨텍스트 구축 완료 (~{tok} 토큰)", result

    # ── 기존 iter_steps() 하위 호환 래퍼 ────────────────────

    def iter_steps(
        self,
        query: str,
        top_k: int = FAISS_TOP_K,
        top_n: int = RERANK_TOP_N,
    ) -> Iterator[Tuple[str, Optional[PipelineResult]]]:
        """기존 호환용 — iter_steps_mode(Balanced) 를 호출합니다."""
        return self.iter_steps_mode(query, BALANCED_CONFIG)

    def run_stream(
        self,
        query: str,
        top_k: int = FAISS_TOP_K,
        top_n: int = RERANK_TOP_N,
        **kwargs,
    ):
        """iter_steps() 별칭 — main.py 하위 호환용."""
        return self.iter_steps(query, top_k=top_k, top_n=top_n)

    def clear_cache(self) -> int:
        """검색 결과 캐시 삭제 (관리자 패널)."""
        return clear_cache()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  싱글톤 관리  (스레드 안전 Double-Checked Locking)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_pipeline_instance: Optional[RAGPipeline] = None
_pipeline_lock = threading.Lock()


def get_pipeline(vector_db: FAISS) -> RAGPipeline:
    """
    RAGPipeline 싱글톤 반환 (스레드 안전).

    [Double-Checked Locking 이유]
    · Streamlit 은 요청마다 별도 스레드 사용
    · 단순 if-check 는 두 스레드가 동시에 None 확인 후 중복 생성 위험
    · Lock 내 재확인으로 1회만 생성 보장
    """
    global _pipeline_instance
    if _pipeline_instance is None:
        with _pipeline_lock:
            if _pipeline_instance is None:
                _pipeline_instance = RAGPipeline(vector_db)
    return _pipeline_instance


def reset_pipeline() -> None:
    """
    싱글톤 인스턴스를 초기화합니다.

    [호출 시점]
    · build_db.py 실행 후 (새 벡터 DB 반영)
    · 관리자 패널 "캐시 초기화" 버튼

    다음 get_pipeline() 호출 시 새 RAGPipeline 이 생성됩니다.
    """
    global _pipeline_instance
    with _pipeline_lock:
        _pipeline_instance = None
    logger.info("RAGPipeline 싱글톤 리셋 완료")
