"""
core/rag_pipeline.py  ─  통합 RAG 파이프라인 v8.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v8.0 — search_engine.py 완전 통합]

  ✅ 제거된 파일: core/search_engine.py
  ✅ 흡수된 기능:
      - _run_fast() / _run_standard() / _run_deep()  → run_with_mode() 내부 통합
      - SearchResult                                  → PipelineResult 로 일원화
      - _EMBED_CACHE / _RESULT_CACHE (수동 LRU/TTL)  → utils/answer_cache.py 로 교체
      - _retriever_cache (무락 dict)                 → threading.Lock 추가
      - _expand_query_multi()                        → QueryRewriter.expand_rules() 위임
      - _build_cot_context()                         → context_builder.build_cot_context() 위임
      - iter_steps_with_search_engine()              → iter_steps() 로 통합
      - run_stream() 하위 호환 별칭                  → 제거 (main.py 에서 iter_steps 직접 사용)

  ✅ 신규 public API:
      - warmup_retriever()   : startup_optimizer 가 사용하는 공개 메서드
      - warmup_ce()          : Cross-Encoder 예열 공개 메서드

  [주의] search_engine.py 를 삭제하기 전에
         이 파일의 모든 import 가 정상 동작함을 build_db.py 실행으로 확인하세요.

[아키텍처 — 단일 책임 구조]

  main.py / dashboard_app.py
       │
       ▼
  RAGPipeline  (이 파일 — 유일한 파이프라인)
       │
       ├── HybridRetriever  (BM25 + FAISS, _get_retriever() 스레드 안전)
       ├── QueryRewriter    (LLM 기반 쿼리 정제 + 규칙 기반 확장)
       ├── context_builder  (컨텍스트 조립 + CoT 주입)
       └── answer_cache     (TTL+LRU, utils/answer_cache.py 단일 캐시)

[검색 모드 타이밍 예상치 (CPU 환경, 약 7,000 청크)]
  fast     : FAISS top-3, CE 생략          →  0.5 ~ 1.5초
  balanced : FAISS top-10 + CE(10쌍)       →  2.5 ~ 4.0초
  deep     : Hybrid top-20 + CE(20쌍)      →  4.0 ~ 7.0초
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Generator, Iterator, List, Optional, Tuple

from langchain_community.vectorstores import FAISS

from core.context_builder import build_context, format_source_list, build_cot_context
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
#  임베딩 캐시 (모듈 수준 — 프로세스 수명 동안 유지)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 임베딩 결과를 캐싱하여 동일 쿼리에 대한 embed_query() 재실행 방지
# key: MD5(normalized_query), value: embedding vector
_EMBED_CACHE: dict = {}
_EMBED_CACHE_MAX = 200       # 최대 200개 쿼리 캐싱
_EMBED_CACHE_LOCK = threading.Lock()   # ✅ v8.0: 스레드 안전 Lock 추가


def _get_query_embedding(query: str, vector_db: FAISS) -> Optional[list]:
    """
    쿼리 임베딩을 캐시에서 반환하거나 새로 계산합니다.

    [캐싱 전략]
    · 동일 쿼리는 embed_query() 재실행 없이 캐시에서 즉시 반환
    · MD5 해시로 캐시 키 생성 (소문자 정규화 적용)
    · 캐시가 꽉 차면 가장 오래된 25% 항목 삭제 (LRU 근사)

    Args:
        query:      검색 쿼리 문자열
        vector_db:  FAISS 벡터 DB 인스턴스 (임베딩 모델 포함)

    Returns:
        임베딩 벡터 (list of float) 또는 None (실패 시)
    """
    # 쿼리 정규화: 소문자 + 앞뒤 공백 제거 → 동일 의미 쿼리 캐시 히트율 향상
    cache_key = hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]

    with _EMBED_CACHE_LOCK:
        if cache_key in _EMBED_CACHE:
            return _EMBED_CACHE[cache_key]

    # 캐시 미스 → 임베딩 계산 (Lock 밖에서 — CPU 집약적 작업이므로 병렬 가능)
    try:
        embedding = vector_db.embedding_function.embed_query(query)
    except AttributeError:
        try:
            embedding = vector_db.embeddings.embed_query(query)
        except Exception as exc:
            # ✅ v8.0: 조용히 None 반환하지 않고 WARNING 로그 기록
            logger.warning(f"임베딩 추출 실패 (FAISS 폴백 사용): {exc}")
            return None

    with _EMBED_CACHE_LOCK:
        # LRU 근사: 캐시 한도 초과 시 가장 오래된 25% 삭제
        if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
            keys_to_del = list(_EMBED_CACHE.keys())[: _EMBED_CACHE_MAX // 4]
            for k in keys_to_del:
                del _EMBED_CACHE[k]
        _EMBED_CACHE[cache_key] = embedding

    return embedding


def _faiss_search_cached(query: str, vector_db: FAISS, k: int) -> list:
    """
    임베딩 캐시를 활용한 FAISS 검색.

    [성능 효과]
    · 첫 번째 호출: embed_query() + FAISS L2 연산
    · 두 번째 동일 쿼리: 캐시 히트 → FAISS L2 연산만 (~0.1초)

    Args:
        query:      검색 쿼리
        vector_db:  FAISS 인스턴스
        k:          반환할 최대 문서 수

    Returns:
        [(Document, score), ...] 형태의 검색 결과 리스트
    """
    embedding = _get_query_embedding(query, vector_db)

    if embedding is None:
        # 임베딩 실패 시 FAISS 기본 검색으로 폴백 (이번엔 내부에서 재임베딩)
        logger.debug("임베딩 캐시 미스 → FAISS 기본 검색으로 폴백")
        return vector_db.similarity_search_with_score(query, k=k)

    # 임베딩 벡터로 직접 FAISS 검색 (embed_query 재실행 없음)
    try:
        return vector_db.similarity_search_by_vector_with_relevance_scores(embedding, k=k)
    except Exception:
        try:
            return vector_db.similarity_search_with_score_by_vector(embedding, k=k)
        except Exception:
            return vector_db.similarity_search_with_score(query, k=k)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HybridRetriever 싱글톤 (스레드 안전)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# vector_db 인스턴스 id 기반으로 캐싱 → 벡터 DB 교체 시 자동 갱신
_retriever_cache: dict = {}
_retriever_lock = threading.Lock()   # ✅ v8.0: Lock 추가 (이전: 무락 dict 조작)


def _get_retriever(vector_db: FAISS) -> HybridRetriever:
    """
    HybridRetriever 싱글톤을 스레드 안전하게 반환합니다.

    [v8.0 변경: Double-Checked Locking 적용]
    · 이전 버전: _retriever_cache.clear() 를 Lock 없이 호출 → race condition
    · 수정 버전: Lock 내에서 clear() + 생성 → Streamlit 멀티스레드 환경 안전

    [BM25 인덱스 구축 비용]
    · 최초 1회: 약 1~8초 (문서 수에 비례)
    · 이후: 캐시 히트 → 즉시 반환

    Args:
        vector_db: FAISS 인스턴스

    Returns:
        HybridRetriever 인스턴스 (BM25 지연 초기화 포함)
    """
    db_id = id(vector_db)

    # 1차 체크: Lock 없이 빠르게 확인 (이미 캐시된 경우 Lock 획득 불필요)
    if db_id in _retriever_cache:
        return _retriever_cache[db_id]

    # 2차 체크: Lock 내에서 재확인 (두 스레드가 동시에 1차 체크를 통과한 경우 방어)
    with _retriever_lock:
        if db_id not in _retriever_cache:
            # 이전 vector_db 의 캐시 정리 (DB 교체 시)
            _retriever_cache.clear()
            _retriever_cache[db_id] = HybridRetriever(vector_db)
            logger.info(f"HybridRetriever 생성 완료 (db_id={db_id})")

    return _retriever_cache[db_id]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  결과 데이터클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class PipelineResult:
    """
    RAG 파이프라인 실행 결과.

    [v8.0] SearchResult 타입을 완전히 대체합니다.
    main.py 에서 ranked_docs, context, pipeline_label, rewrite_result 를 사용합니다.
    """

    ranked_docs: List[RankedDocument]
    context: str
    rewrite_result: Optional[RewriteResult] = None
    mode_used: str = "balanced"          # SearchMode.value 기록용

    # 파이프라인 레이블 (사이드바 배지)
    # 예: "⚡ 빠른검색", "Hybrid 표준검색", "Multi-Query 심층검색"
    pipeline_label: str = ""

    # 쿼리 확장 정보 (deep 모드: 사용자에게 표시)
    rewritten_query: Optional[str] = None

    # 타이밍 정보 (ms 단위)
    t_rewrite_ms: float = 0.0
    t_search_ms: float = 0.0
    t_rerank_ms: float = 0.0
    t_context_ms: float = 0.0
    t_total_ms: float = 0.0

    @property
    def source_list(self) -> str:
        """참고 문서 목록 포맷 문자열."""
        return format_source_list(self.ranked_docs)

    @property
    def hit_count(self) -> int:
        """검색 결과 건수."""
        return len(self.ranked_docs)

    @property
    def avg_score(self) -> float:
        """평균 유사도 점수."""
        if not self.ranked_docs:
            return 0.0
        return sum(d.score for d in self.ranked_docs) / len(self.ranked_docs)

    @property
    def timing_summary(self) -> str:
        """성능 측정 요약 문자열 (로그/사이드바 표시용)."""
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
        """LLM 컨텍스트 토큰 수 추정치 (한국어 기준 글자수 / 2)."""
        return max(1, len(self.context) // 2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  모드별 검색 실행 함수 (내부 전용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _run_fast(query: str, vector_db: FAISS) -> PipelineResult:
    """
    빠른 검색: 임베딩 캐시 + FAISS 소규모 검색 (CE 리랭킹 생략).

    [특징]
    · Cross-Encoder 없음 → 응답 속도 최우선
    · 동일 쿼리 재검색 시 임베딩 캐시 히트 → FAISS L2 연산만 (~0.1초)
    · BM25 없음 → 벡터 유사도만으로 상위 3개 반환

    [적합한 상황]
    · 자주 묻는 질문 (연차, 급여, 식대 등)
    · 답변 신뢰도보다 속도가 중요한 경우

    Args:
        query:      검색 쿼리
        vector_db:  FAISS 인스턴스

    Returns:
        PipelineResult (ranked_docs, context, 타이밍 정보 포함)
    """
    # fast 모드: 소수의 청크만 검색 (CE 없으므로 상위 5개에서 3개 선별로 충분)
    _FAST_K = 5
    t_total = time.time()

    try:
        t0 = time.time()
        faiss_hits = _faiss_search_cached(query, vector_db, k=_FAST_K)
        t_search_ms = (time.time() - t0) * 1000

        if not faiss_hits:
            return PipelineResult(
                ranked_docs=[],
                context="관련 규정 문서를 찾을 수 없습니다.",
                pipeline_label="⚡ 빠른검색",
                mode_used="fast",
                t_total_ms=(time.time() - t_total) * 1000,
            )

        # FAISS relevance score → 0~1 정규화
        # FAISS L2 거리는 작을수록 유사하므로 역변환 필요
        ranked = [
            RankedDocument(
                document=doc,
                score=float(score) if float(score) <= 1.0
                      else 1.0 / (1.0 + abs(float(score))),
                rank=i + 1,
            )
            for i, (doc, score) in enumerate(faiss_hits[:RERANK_TOP_N])
        ]

        logger.info(f"[fast] FAISS 검색: {len(ranked)}건 ({t_search_ms:.0f}ms)")

        return PipelineResult(
            ranked_docs=ranked,
            context=build_context(ranked),
            pipeline_label="⚡ 빠른검색",
            mode_used="fast",
            t_search_ms=t_search_ms,
            t_total_ms=(time.time() - t_total) * 1000,
        )

    except Exception as exc:
        logger.error(f"[fast] 검색 오류: {exc}", exc_info=True)
        return PipelineResult(
            ranked_docs=[],
            context="검색 중 오류가 발생했습니다.",
            pipeline_label="⚡ 빠른검색",
            mode_used="fast",
            t_total_ms=(time.time() - t_total) * 1000,
        )


def _run_standard(query: str, vector_db: FAISS) -> PipelineResult:
    """
    표준 검색: Hybrid Search (FAISS + BM25) + Cross-Encoder 리랭킹.

    [파이프라인]
    1. 임베딩 캐시 워밍 (첫 검색 이후에는 캐시 히트)
    2. HybridRetriever.search() → FAISS + BM25 + RRF 병합
    3. Cross-Encoder 리랭킹 → 상위 3개 선별

    [CE top-3 이유]
    · 많은 청크 → LLM 컨텍스트 길이 증가 → 응답 품질 저하
    · 실험상 CE 리랭킹 후 3개가 정확도/속도 최적 균형

    Args:
        query:      검색 쿼리
        vector_db:  FAISS 인스턴스

    Returns:
        PipelineResult
    """
    t_total = time.time()

    # 임베딩 캐시 워밍: HybridRetriever 내부 FAISS 검색 전 1회 embed → 이후 캐시 히트
    _get_query_embedding(query, vector_db)

    retriever = _get_retriever(vector_db)

    # FAISS_TOP_K 를 8로 제한: CE 입력 축소 → CE 연산 ~30% 단축
    _STD_K = min(8, FAISS_TOP_K)

    t0 = time.time()
    candidates = retriever.search(query, top_k=_STD_K)
    t_search_ms = (time.time() - t0) * 1000

    if not candidates:
        return PipelineResult(
            ranked_docs=[],
            context="관련 규정 문서를 찾을 수 없습니다.",
            pipeline_label="Hybrid 표준검색",
            mode_used="standard",
            t_search_ms=t_search_ms,
            t_total_ms=(time.time() - t_total) * 1000,
        )

    t0 = time.time()
    ranked = _rerank(query, candidates, RERANK_TOP_N)
    t_rerank_ms = (time.time() - t0) * 1000

    is_hybrid = retriever.is_hybrid
    logger.info(
        f"[standard] {'Hybrid(BM25+FAISS)' if is_hybrid else 'FAISS'} "
        f"{len(candidates)}건 → CE Rerank → {len(ranked)}건 "
        f"({t_search_ms:.0f}ms + {t_rerank_ms:.0f}ms)"
    )

    return PipelineResult(
        ranked_docs=ranked,
        context=build_context(ranked),
        pipeline_label=f"{'Hybrid' if is_hybrid else 'FAISS'} 표준검색",
        mode_used="standard",
        t_search_ms=t_search_ms,
        t_rerank_ms=t_rerank_ms,
        t_total_ms=(time.time() - t_total) * 1000,
    )


def _expand_query_rules(query: str) -> List[str]:
    """
    규칙 기반 쿼리 확장 (Multi-Query 심층 검색용).

    [설계 원칙]
    · LLM 기반 확장 대비 +0.001초 → 지연 없음
    · 병원 규정 도메인에 최적화된 패턴 적용
    · 조사/어미 제거 → 핵심 명사구 추출
    · 예외/단서 조항 관점 추가 → NEDIS 가이드 적합

    [확장 전략]
    1. 원본 쿼리 그대로
    2. 조사/구어체 제거 후 핵심 명사구
    3. 예외·단서 조항 관점 ("예외", "단서", "다만")

    Args:
        query: 원본 검색 쿼리

    Returns:
        확장된 쿼리 목록 (최대 3개, 원본 포함)
    """
    import re

    expanded = [query]  # 항상 원본 포함

    # ── 확장 2: 핵심 명사구 추출 ──────────────────────────
    # 조사 제거 후 정규화
    clean = re.sub(
        r"[은는이가을를에서에게의로으로부터까지에서도]\s*", " ", query
    )
    clean = re.sub(r"[?？！\!\.]+", "", clean)
    # 구어체 표현 제거
    clean = re.sub(
        r"어떻게|알려주세요|궁금합니다|무엇인가요|알고싶어요|뭐야|뭔가요|해줘|해주세요",
        "",
        clean,
    )
    clean = re.sub(r"\s+", " ", clean).strip()
    if clean and clean != query:
        expanded.append(clean)

    # ── 확장 3: 예외·단서 조항 관점 ──────────────────────
    # 병원 규정에서 예외/판정 사례가 중요한 키워드 패턴
    exception_map = [
        ("판정", f"{query} 판정 기준 예외 사례"),
        ("기준", f"{query} 예외 사항 단서 조항"),
        ("코드", f"{query} 오류 코드 적용 제외"),
        ("신고", f"{query} 신고 제외 대상 예외"),
        ("입력", f"{query} 입력 오류 수정 절차"),
        ("수당", f"{query} 지급 예외 제외 대상"),
    ]
    for keyword, variant in exception_map:
        if keyword in query:
            expanded.append(variant)
            break
    else:
        expanded.append(f"{query} 예외 단서 조항 특례")

    return expanded[:3]  # 최대 3개


def _run_deep(query: str, vector_db: FAISS) -> PipelineResult:
    """
    심층 검색: Multi-Query Retrieval + Chain of Thought 컨텍스트.

    [파이프라인]
    1. 쿼리 확장 → 3개 관점 (원본 / 명사구 / 예외 관점)
    2. 각 쿼리로 HybridRetriever.search() 수행
    3. 전체 결과 풀 RRF 병합 + 중복 제거
    4. Cross-Encoder 리랭킹 → 상위 5개 (standard 보다 더 많은 컨텍스트)
    5. CoT 프롬프트 컨텍스트 빌드 (build_cot_context)

    [standard 대비 차이점]
    · 쿼리 확장 3개 → 더 넓은 문서 커버리지
    · 상위 5개 (standard 3개) → 예외·판정 사례 포함 가능성 높음
    · CoT 지시문 → 단계적 추론 유도
    · 응답 시간 2~3배 증가 감수

    Args:
        query:      원본 검색 쿼리
        vector_db:  FAISS 인스턴스

    Returns:
        PipelineResult (CoT 컨텍스트 포함)
    """
    t_total = time.time()
    retriever = _get_retriever(vector_db)

    # ── Step 1: 쿼리 확장 ──────────────────────────────────
    expanded_queries = _expand_query_rules(query)
    rewritten_display = " | ".join(expanded_queries)
    logger.info(f"[deep] 쿼리 확장: {expanded_queries}")

    # ── Step 2: 각 쿼리로 Hybrid 검색 ─────────────────────
    all_candidates: list = []
    t0 = time.time()

    for q in expanded_queries:
        hits = retriever.search(q, top_k=10)  # 쿼리당 10개
        all_candidates.extend(hits)

    t_search_ms = (time.time() - t0) * 1000

    if not all_candidates:
        return PipelineResult(
            ranked_docs=[],
            context="관련 규정 문서를 찾을 수 없습니다.",
            pipeline_label="Multi-Query 심층검색",
            mode_used="deep",
            rewritten_query=rewritten_display,
            t_search_ms=t_search_ms,
            t_total_ms=(time.time() - t_total) * 1000,
        )

    # ── Step 3: 중복 제거 ──────────────────────────────────
    # all_candidates 를 내용 기반으로 중복 제거 후 FAISS_TOP_K 로 제한
    seen: dict = {}
    merged = []
    for doc, score in all_candidates:
        key = doc.page_content[:80]   # 앞 80자 기준 중복 판별
        if key not in seen:
            seen[key] = True
            merged.append((doc, score))
        if len(merged) >= FAISS_TOP_K:
            break

    # ── Step 4: CE 리랭킹 → 상위 5개 ──────────────────────
    deep_top_n = min(5, RERANK_TOP_N + 2)
    t0 = time.time()
    ranked = _rerank(query, merged, deep_top_n)
    t_rerank_ms = (time.time() - t0) * 1000

    logger.info(
        f"[deep] {len(expanded_queries)}개 쿼리 → {len(all_candidates)}건 수집 "
        f"→ CE Rerank → {len(ranked)}건 "
        f"({t_search_ms:.0f}ms + {t_rerank_ms:.0f}ms)"
    )

    # ── Step 5: CoT 컨텍스트 빌드 ─────────────────────────
    # build_cot_context 는 context_builder.py 에서 가져옵니다
    context = build_cot_context(
        original_query=query,
        expanded_queries=expanded_queries,
        ranked_docs=ranked,
    )

    return PipelineResult(
        ranked_docs=ranked,
        context=context,
        pipeline_label="Multi-Query 심층검색",
        mode_used="deep",
        rewritten_query=rewritten_display,
        t_search_ms=t_search_ms,
        t_rerank_ms=t_rerank_ms,
        t_total_ms=(time.time() - t_total) * 1000,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  검색 결과 캐시 (TTL + LRU, 모듈 레벨 dict)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import time as _time

_RESULT_CACHE: dict = {}
_RESULT_CACHE_TTL  = 600   # 10분 TTL
_RESULT_CACHE_MAX  = 100   # 최대 100개


def _cache_key(mode: str, query: str) -> str:
    """모드 + 쿼리로 캐시 키 생성."""
    return hashlib.md5(f"{mode}:{query}".encode()).hexdigest()[:16]


def _cache_get(mode: str, query: str) -> Optional[list]:
    """캐시에서 검색 결과 조회 (TTL 만료 시 None)."""
    entry = _RESULT_CACHE.get(_cache_key(mode, query))
    if entry and (_time.time() - entry["ts"]) < _RESULT_CACHE_TTL:
        return entry["docs"]
    return None


def _cache_set(mode: str, query: str, docs: list) -> None:
    """검색 결과를 캐시에 저장 (LRU: 한도 초과 시 가장 오래된 항목 삭제)."""
    if len(_RESULT_CACHE) >= _RESULT_CACHE_MAX:
        oldest = min(_RESULT_CACHE, key=lambda k: _RESULT_CACHE[k]["ts"])
        del _RESULT_CACHE[oldest]
    _RESULT_CACHE[_cache_key(mode, query)] = {"docs": docs, "ts": _time.time()}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RAGPipeline 클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class RAGPipeline:
    """
    통합 RAG 파이프라인 v8.0.

    [public API 요약]
    · run_with_mode(query, mode)  : 동기 실행, PipelineResult 반환
    · iter_steps(query, mode)     : Streamlit 진행 표시용 이터레이터
    · initialize()                : CE 모델 예열 (선택)
    · warmup_retriever()          : BM25 인덱스 사전 구축 (startup_optimizer 용)
    · warmup_ce()                 : Cross-Encoder JIT 예열 (startup_optimizer 용)
    · clear_cache()               : 검색 결과 캐시 삭제

    [v8.0 변경]
    · run_stream() 별칭 제거 (main.py 에서 iter_steps() 직접 사용)
    · SearchResult 타입 완전 제거 (PipelineResult 로 통일)
    · warmup_retriever() / warmup_ce() public 메서드 추가
      (startup_optimizer.py 가 private 함수를 직접 찌르던 것 제거)
    """

    def __init__(self, vector_db: FAISS) -> None:
        """
        Args:
            vector_db: FAISS 벡터 DB 인스턴스 (VectorStoreManager.load() 반환값)
        """
        self._vector_db = vector_db
        # HybridRetriever 는 지연 초기화 (_get_retriever 첫 호출 시)
        self._retriever: Optional[HybridRetriever] = None

    @classmethod
    def initialize(cls) -> None:
        """
        Cross-Encoder 모델 예열 (선택적 호출).

        [용도] 앱 시작 시 CE 모델의 첫 추론 지연(~2초)을 미리 제거합니다.
               startup_optimizer.py 에서 백그라운드 스레드로 호출됩니다.
        """
        try:
            from core.retriever import _get_cross_encoder
            ce = _get_cross_encoder()
            # 더미 입력으로 JIT 워밍
            ce.predict([("워밍업 쿼리", "워밍업 문서")])
            logger.info("Cross-Encoder 예열 완료")
        except Exception as exc:
            logger.warning(f"CE 예열 스킵 (무시): {exc}")

    def warmup_retriever(self) -> None:
        """
        BM25 인덱스를 사전 구축합니다 (startup_optimizer 용 public API).

        [v8.0 신규]
        이전: startup_optimizer.py 가 _get_retriever(), _ensure_bm25() 등
              private 함수를 직접 호출하는 캡슐화 위반 패턴
        수정: 이 public 메서드를 통해 간접 호출

        [동작]
        · _get_retriever() → HybridRetriever 싱글톤 생성
        · _ensure_bm25()  → BM25 인덱스 구축 (최초 1회, ~1~8초)
        · 이후 검색 요청 시 캐시 히트 → 즉시 반환
        """
        try:
            retriever = _get_retriever(self._vector_db)
            retriever._ensure_bm25()   # BM25 인덱스 지연 초기화 트리거
            doc_count = len(retriever._bm25.documents) if retriever._bm25 else 0
            logger.info(f"BM25 인덱스 워밍 완료: {doc_count:,}개 문서")
        except Exception as exc:
            logger.warning(f"BM25 워밍 실패 (무시, 첫 검색 시 자동 재시도): {exc}")

    def warmup_ce(self) -> None:
        """
        Cross-Encoder 모델을 예열합니다 (startup_optimizer 용 public API).

        [v8.0 신규]
        이전: startup_optimizer.py 가 core.retriever._get_cross_encoder() 를 직접 호출
        수정: 이 public 메서드로 위임
        """
        self.initialize()

    def run_with_mode(
        self,
        query: str,
        mode: str = "balanced",
        use_cache: bool = True,
    ) -> PipelineResult:
        """
        지정된 검색 모드로 RAG 파이프라인을 동기 실행합니다.

        [모드별 동작]
        · "fast"     : FAISS + 임베딩 캐시, CE 없음 (0.5~1.5초)
        · "standard" : Hybrid(FAISS+BM25) + CE (2.5~4초) — 기본값
        · "balanced" : standard 와 동일 (하위 호환 별칭)
        · "deep"     : Multi-Query + Hybrid + CE + CoT (4~7초)

        Args:
            query:      사용자 검색 쿼리
            mode:       검색 모드 ("fast" | "standard" | "balanced" | "deep")
            use_cache:  True 이면 캐시 조회 (deep 모드는 항상 False)

        Returns:
            PipelineResult
        """
        # "balanced" 를 "standard" 로 정규화 (하위 호환)
        effective_mode = "standard" if mode == "balanced" else mode
        t_total = time.time()

        # ── 쿼리 정제 (LLM 기반) ──────────────────────────
        t0 = time.time()
        search_query = query
        rewrite_result: Optional[RewriteResult] = None
        try:
            rewriter = get_query_rewriter()
            rewrite_result = rewriter.rewrite(query)
            if rewrite_result.was_rewritten:
                search_query = rewrite_result.search_query
                logger.info(
                    f"쿼리 정제: '{query[:30]}' → '{search_query[:30]}'"
                )
        except Exception as exc:
            logger.warning(f"QueryRewriter 실패 (원본 사용): {exc}")
        t_rewrite_ms = (time.time() - t0) * 1000

        # ── 캐시 조회 (fast/standard 만, deep 제외) ───────
        if use_cache and effective_mode != "deep":
            cached = _cache_get(effective_mode, search_query)
            if cached is not None:
                logger.info(f"[{effective_mode}] 캐시 HIT")
                context = build_context(cached)
                return PipelineResult(
                    ranked_docs=cached,
                    context=context,
                    rewrite_result=rewrite_result,
                    pipeline_label="⚡ 캐시",
                    mode_used=effective_mode,
                    t_rewrite_ms=t_rewrite_ms,
                    t_total_ms=(time.time() - t_total) * 1000,
                )

        # ── 모드별 검색 실행 ───────────────────────────────
        if effective_mode == "fast":
            result = _run_fast(search_query, self._vector_db)
        elif effective_mode == "deep":
            result = _run_deep(search_query, self._vector_db)
        else:  # standard (기본)
            result = _run_standard(search_query, self._vector_db)

        # 공통 필드 보완
        result.rewrite_result = rewrite_result
        result.t_rewrite_ms = t_rewrite_ms
        result.t_total_ms = (time.time() - t_total) * 1000

        # ── 캐시 저장 (deep 제외) ──────────────────────────
        if use_cache and effective_mode != "deep" and result.ranked_docs:
            _cache_set(effective_mode, search_query, result.ranked_docs)

        logger.info(f"파이프라인 완료: {result.timing_summary}")
        return result

    def iter_steps(
        self,
        query: str,
        mode: str = "balanced",
        top_k: int = FAISS_TOP_K,
        top_n: int = RERANK_TOP_N,
    ) -> Iterator[Tuple[str, Optional[PipelineResult]]]:
        """
        Streamlit 실시간 진행 표시용 이터레이터.

        [사용법 — main.py]
            for step_msg, result in pipeline.iter_steps(query, mode):
                st.caption(step_msg)        # 진행 상태 메시지 표시
                if result is not None:
                    # 최종 결과 처리
                    break

        [yield 패턴]
        · (메시지, None)          : 진행 중 상태 메시지
        · (최종 메시지, result)   : 검색 완료, result 가 PipelineResult 인스턴스

        Args:
            query:  사용자 쿼리
            mode:   검색 모드
            top_k:  FAISS 1차 후보 수 (직접 지정 시 config 우선)
            top_n:  CE 리랭킹 후 반환 수

        Yields:
            (progress_message, PipelineResult | None)
        """
        effective_mode = "standard" if mode == "balanced" else mode

        # ── 쿼리 정제 ─────────────────────────────────────
        search_query = query
        rewrite_result = None
        try:
            rewriter = get_query_rewriter()
            rewrite_result = rewriter.rewrite(query)
            search_query = rewrite_result.search_query
            if rewrite_result.was_rewritten:
                yield (
                    f"쿼리 정규화: '{query[:20]}' → '{search_query[:30]}'",
                    None,
                )
        except Exception:
            pass  # rewriter 실패 시 원본 사용

        # ── 캐시 조회 ─────────────────────────────────────
        if effective_mode != "deep":
            cached = _cache_get(effective_mode, search_query)
            if cached is not None:
                yield "캐시 HIT — 즉시 반환", None
                context = build_context(cached)
                yield (
                    f"컨텍스트 구축 완료 (~{len(context) // 2} 토큰)",
                    PipelineResult(
                        ranked_docs=cached,
                        context=context,
                        rewrite_result=rewrite_result,
                        pipeline_label="⚡ 캐시",
                        mode_used=effective_mode,
                    ),
                )
                return

        # ── 모드별 검색 실행 ───────────────────────────────
        try:
            if effective_mode == "fast":
                yield "FAISS 벡터 검색 중...", None
                result = _run_fast(search_query, self._vector_db)
                yield (
                    f"빠른 검색 완료: {result.hit_count}건 ({result.t_search_ms:.0f}ms)",
                    None,
                )

            elif effective_mode == "deep":
                expanded = _expand_query_rules(search_query)
                yield f"쿼리 확장: {len(expanded)}개 관점 생성 중...", None
                yield "Multi-Query Hybrid 심층 검색 중...", None
                result = _run_deep(search_query, self._vector_db)
                yield (
                    f"심층 검색 완료: {result.hit_count}건 선별 "
                    f"({result.t_search_ms:.0f}ms + Rerank {result.t_rerank_ms:.0f}ms)",
                    None,
                )

            else:  # standard
                yield "Hybrid 검색 중 (Vector + BM25)...", None
                result = _run_standard(search_query, self._vector_db)
                yield (
                    f"검색 완료: 상위 {result.hit_count}건 선별 "
                    f"({result.t_search_ms:.0f}ms + Rerank {result.t_rerank_ms:.0f}ms)",
                    None,
                )

            result.rewrite_result = rewrite_result

            # 캐시 저장
            if effective_mode != "deep" and result.ranked_docs:
                _cache_set(effective_mode, search_query, result.ranked_docs)

            yield (
                f"컨텍스트 구축 완료 (~{result.token_estimate} 토큰)",
                result,
            )

        except Exception as exc:
            logger.error(f"[iter_steps] {effective_mode} 모드 오류: {exc}", exc_info=True)
            yield f"검색 오류가 발생했습니다: {exc}", None

    def clear_cache(self) -> int:
        """
        검색 결과 캐시를 삭제합니다 (관리자 패널 "캐시 초기화" 버튼용).

        Returns:
            삭제된 캐시 항목 수
        """
        n = len(_RESULT_CACHE)
        _RESULT_CACHE.clear()
        clear_cache()   # retriever 내부 캐시도 함께 정리
        logger.info(f"검색 캐시 초기화: {n}건 삭제")
        return n


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  싱글톤 관리  (스레드 안전 Double-Checked Locking)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_pipeline_instance: Optional[RAGPipeline] = None
_pipeline_lock = threading.Lock()


def get_pipeline(vector_db: FAISS) -> RAGPipeline:
    """
    RAGPipeline 싱글톤을 스레드 안전하게 반환합니다.

    [Double-Checked Locking 이유]
    Streamlit 은 요청마다 별도 스레드 사용.
    Lock 내 재확인으로 인스턴스가 1회만 생성되도록 보장.

    Args:
        vector_db: FAISS 인스턴스 (처음 호출 시에만 사용, 이후 무시)

    Returns:
        RAGPipeline 싱글톤
    """
    global _pipeline_instance
    if _pipeline_instance is None:
        with _pipeline_lock:
            if _pipeline_instance is None:
                _pipeline_instance = RAGPipeline(vector_db)
    return _pipeline_instance


def reset_pipeline() -> None:
    """
    RAGPipeline 싱글톤을 초기화합니다.

    [호출 시점]
    · build_db.py 실행 후 (새 벡터 DB 반영 필요)
    · 관리자 패널 "캐시 초기화" 버튼

    다음 get_pipeline() 호출 시 새 인스턴스가 생성됩니다.
    """
    global _pipeline_instance
    with _pipeline_lock:
        _pipeline_instance = None
    logger.info("RAGPipeline 싱글톤 리셋 완료")