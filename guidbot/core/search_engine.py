"""
core/search_engine.py  ─  NEDIS 가이드봇 검색 모드 엔진 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[3가지 검색 모드 설계]

  ┌──────────┬────────────────────────────────────────────────────────┐
  │ 모드     │ 로직                                                   │
  ├──────────┼────────────────────────────────────────────────────────┤
  │ fast     │ BM25 키워드 매칭만. 임베딩 없음.                       │
  │          │ 코드 번호, 단순 용어 조회. 응답 속도 최우선 (~0.01초) │
  ├──────────┼────────────────────────────────────────────────────────┤
  │ standard │ Hybrid Search (FAISS + BM25) + RRF + CE Rerank.       │
  │          │ 상위 3개 청크만 참조. 일반 질문 기본 모드.             │
  ├──────────┼────────────────────────────────────────────────────────┤
  │ deep     │ Multi-Query Retrieval: 질문 3개로 확장 검색.           │
  │          │ LLM Chain of Thought 적용. 예외 조항·판정 사례 분석.  │
  └──────────┴────────────────────────────────────────────────────────┘

[SearchResult 구조]
  ranked_docs:     List[RankedDocument]  — CE 리랭킹 결과
  context:         str                   — LLM 컨텍스트
  pipeline_label:  str                   — UI 표시 라벨
  rewritten_query: Optional[str]         — deep 모드: 확장 쿼리 목록
  hit_count:       int
  timing_summary:  str
  t_total_ms:      float

[main.py 연동]
  from core.search_engine import SearchResult, iter_search_steps

  for step_msg, result in iter_search_steps(
      query=prompt, vector_db=vector_db, mode=search_mode,
  ):
      status.write(step_msg)
      if result:
          final_result = result
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Generator, Iterator, List, Optional, Tuple

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from config.settings import settings
from core.context_builder import build_context
from core.hybrid_retriever import HybridRetriever, build_bm25_from_faiss, _tokenize_ko
from core.retriever import RankedDocument, _rerank, FAISS_TOP_K, RERANK_TOP_N
from core.query_rewriter import get_query_rewriter
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# ── 성능 최적화 상수 ────────────────────────────────────────────────
# FAISS 후보 15→10: CE 리랭킹 입력 33% 감소 → 속도↑ (정확도 유지)
_FAST_TOP_K = 10  # fast/standard 모드 FAISS 후보
_DEEP_TOP_K = 12  # deep 모드 (더 넓은 커버리지)

# ── 쿼리 임베딩 LRU 캐시 (가장 큰 성능 개선) ────────────────────────
# FAISS 검색의 병목 = embeddings.embed_query(query) → CPU에서 ~8~10초
# 동일 쿼리의 임베딩 결과를 캐싱 → 두 번째 호출부터 0.001초
import hashlib, time as _time

_EMBED_CACHE: dict = {}
_EMBED_CACHE_MAX = 200  # 최대 200개 쿼리 임베딩 저장


def _get_query_embedding(query: str, vector_db) -> list:
    """
    쿼리 임베딩 캐시 조회/저장.

    FAISS vector_db.similarity_search(query) 내부에서
    embeddings.embed_query(query)를 매번 실행하는 것을 방지.
    동일 쿼리는 캐시에서 즉시 반환 → ko-sroberta CPU 추론 생략.

    Returns:
        embedding vector (list of float)
    """
    _key = hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]
    if _key in _EMBED_CACHE:
        return _EMBED_CACHE[_key]

    # 임베딩 직접 추출
    try:
        emb = vector_db.embedding_function.embed_query(query)
    except AttributeError:
        try:
            emb = vector_db.embeddings.embed_query(query)
        except Exception:
            return None

    if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
        # 오래된 항목 25% 삭제
        _keys = list(_EMBED_CACHE.keys())
        for _k in _keys[: _EMBED_CACHE_MAX // 4]:
            del _EMBED_CACHE[_k]

    _EMBED_CACHE[_key] = emb
    return emb


def _faiss_search_cached(query: str, vector_db, k: int) -> list:
    """
    임베딩 캐시를 활용한 FAISS 검색.

    [성능]
    · 첫 번째 호출: embed_query() → ~8초 + FAISS L2 연산 ~0.1초
    · 두 번째 동일 쿼리: 캐시 히트 → FAISS L2 연산만 ~0.1초
    · 쿼리 정규화 후 캐시 → "연차 어때요?"와 "연차"가 같은 임베딩 가능
    """
    emb = _get_query_embedding(query, vector_db)
    if emb is None:
        # 폴백: 기존 방식
        return vector_db.similarity_search_with_score(query, k=k)

    # 임베딩으로 직접 벡터 검색 (embed_query 재실행 없음)
    try:
        return vector_db.similarity_search_by_vector_with_relevance_scores(emb, k=k)
    except Exception:
        try:
            return vector_db.similarity_search_with_score_by_vector(emb, k=k)
        except Exception:
            return vector_db.similarity_search_with_score(query, k=k)


# ── 검색 결과 LRU 캐시 (동일 쿼리 10분 내 즉시 반환) ────────────────
_RESULT_CACHE: dict = {}
_CACHE_TTL = 600  # 10분


def _cache_get(key: str):
    entry = _RESULT_CACHE.get(key)
    if entry and (_time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["docs"]
    return None


def _cache_set(key: str, docs: list) -> None:
    if len(_RESULT_CACHE) > 100:
        # LRU: 가장 오래된 항목 제거
        oldest = min(_RESULT_CACHE, key=lambda k: _RESULT_CACHE[k]["ts"])
        del _RESULT_CACHE[oldest]
    _RESULT_CACHE[key] = {"docs": docs, "ts": _time.time()}


def _make_cache_key(mode: str, query: str) -> str:
    return hashlib.md5(f"{mode}:{query}".encode()).hexdigest()[:12]


# ── HybridRetriever 싱글톤 캐시 ──────────────────────────────────────
# 매 검색마다 BM25 인덱스를 재구축하는 성능 낭비 방지.
# vector_db 인스턴스 id 기반으로 캐싱 → 벡터DB 교체 시 자동 갱신.
_retriever_cache: dict = {}


def _get_retriever(vector_db) -> HybridRetriever:
    """
    HybridRetriever 싱글톤 반환.

    BM25 인덱스 구축은 최초 1회 (24,000벡터 기준 ~2초).
    이후 동일 vector_db에 대해서는 캐시된 인스턴스 반환 (~0ms).
    """
    db_id = id(vector_db)
    if db_id not in _retriever_cache:
        _retriever_cache.clear()  # 이전 DB 캐시 정리
        _retriever_cache[db_id] = HybridRetriever(vector_db)
        logger.info(f"HybridRetriever 캐시 생성 (db_id={db_id})")
    return _retriever_cache[db_id]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  검색 결과 데이터클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class SearchResult:
    """
    검색 모드별 통합 결과.
    main.py 의 _stream_answer() 에서 ranked_docs, context를 사용합니다.
    """

    ranked_docs: List[RankedDocument]
    context: str
    pipeline_label: str = ""  # 사이드바 배지용: "BM25 빠른검색" 등
    rewritten_query: Optional[str] = None  # deep 모드: 확장된 쿼리 표시용

    # 성능 지표
    t_search_ms: float = 0.0
    t_rerank_ms: float = 0.0
    t_total_ms: float = 0.0

    @property
    def hit_count(self) -> int:
        return len(self.ranked_docs)

    @property
    def avg_score(self) -> float:
        if not self.ranked_docs:
            return 0.0
        return sum(d.score for d in self.ranked_docs) / len(self.ranked_docs)

    @property
    def timing_summary(self) -> str:
        return (
            f"검색 {self.t_search_ms:.0f}ms | "
            f"리랭킹 {self.t_rerank_ms:.0f}ms | "
            f"총 {self.t_total_ms:.0f}ms"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  모드 1: 빠른 검색 (BM25 Only)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _run_fast(query: str, vector_db: FAISS) -> SearchResult:
    """
    빠른 검색: 임베딩 캐시 + FAISS 소규모 검색 (v3.0 개선).

    [v3.0 변경 — BM25 → FAISS + 임베딩 캐시]
    기존 BM25 전용 방식의 문제:
    · 병원 전문용어 의미 파악 불가 ("연차" ≠ "연차휴가" → 미매칭)
    · 조항 번호만 있고 내용 없으면 0점 → 빈 결과
    · 실무 체감: "관련 문서를 찾을 수 없음" 빈번

    v3.0 방식:
    · _faiss_search_cached() → 임베딩 캐시 활용
    · 첫 번째 질문: embed_query(~8초) + FAISS L2(~0.1초) = ~8초
    · 두 번째 동일 질문: 캐시 히트 → FAISS L2만 (~0.1초)
    · BM25 Hybrid는 standard 모드에서 추가 정확도 향상용으로 유지

    [응답 속도]
    · 첫 번째: ~8~10초 (임베딩 모델 추론, 불가피)
    · 재검색(캐시): ~0.2초
    · 리랭킹 없음 → CE 추가 없이 벡터 유사도 순 TOP 3 반환
    """
    _FAST_K = 5  # CE 없이 벡터 유사도만 → 상위 5개에서 3개 선별 충분
    t_total = time.time()

    # 임베딩 캐시 활용 FAISS 검색
    t0 = time.time()
    try:
        faiss_hits = _faiss_search_cached(query, vector_db, k=_FAST_K)
        t_search = (time.time() - t0) * 1000

        if not faiss_hits:
            return SearchResult(
                ranked_docs=[],
                context="관련 규정 문서를 찾을 수 없습니다.",
                pipeline_label="⚡ 빠른검색",
                t_total_ms=(time.time() - t_total) * 1000,
            )

        # 점수 정규화: FAISS relevance score → 0~1
        ranked = [
            RankedDocument(
                document=doc,
                score=float(score)
                if float(score) <= 1.0
                else 1.0 / (1.0 + abs(float(score))),
                rank=i + 1,
            )
            for i, (doc, score) in enumerate(faiss_hits[:RERANK_TOP_N])
        ]

        _cache_hit = t_search < 500  # 0.5초 미만이면 캐시 히트
        _label = "⚡ 빠른검색 (캐시)" if _cache_hit else "⚡ 빠른검색"
        logger.info(f"[fast] FAISS 검색: {len(ranked)}건 ({t_search:.0f}ms)")

        return SearchResult(
            ranked_docs=ranked,
            context=build_context(ranked),
            pipeline_label=_label,
            t_search_ms=t_search,
            t_total_ms=(time.time() - t_total) * 1000,
        )
    except Exception as exc:
        logger.error(f"[fast] 검색 오류: {exc}", exc_info=True)
        return SearchResult(
            ranked_docs=[],
            context="검색 중 오류가 발생했습니다.",
            pipeline_label="⚡ 빠른검색",
            t_total_ms=(time.time() - t_total) * 1000,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  모드 2: 표준 검색 (Hybrid + Rerank)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _run_standard(query: str, vector_db: FAISS) -> SearchResult:
    """
    표준 검색: Hybrid Search(Vector + BM25) + CE Rerank.

    [파이프라인]
    1. FAISS 벡터 검색 (top_k=15)
    2. BM25 키워드 검색 (top_k=20)
    3. RRF(Reciprocal Rank Fusion) 병합
    4. Cross Encoder 리랭킹
    5. 상위 3개 청크만 LLM 컨텍스트에 포함

    [상위 3개 이유]
    · 많은 청크 → LLM 컨텍스트 길이 증가 → 응답 품질 저하
    · 실험상 3개가 정확도/속도 최적 균형점
    · CE 리랭킹으로 3개 내 정답 포함률 ≥ 95%
    """
    t_total = time.time()

    # Hybrid 검색 (FAISS + BM25 + RRF) — 캐시된 인스턴스 사용
    retriever = _get_retriever(vector_db)

    # [v3.0] 임베딩 미리 캐시 워밍
    # retriever.search() 내부 FAISS 검색 전에 한 번 embed → 캐시 저장
    # → retriever 내부 similarity_search에서 캐시 히트 가능
    _get_query_embedding(query, vector_db)  # 캐시 미스 시 여기서 1회만 계산

    t0 = time.time()
    # [v3.0] FAISS_TOP_K 15 → 8: CE 리랭킹 입력 47% 감소 → CE ~30% 단축
    _STD_K = min(8, FAISS_TOP_K)
    candidates = retriever.search(query, top_k=_STD_K)
    t_search = (time.time() - t0) * 1000

    if not candidates:
        return SearchResult(
            ranked_docs=[],
            context="관련 규정 문서를 찾을 수 없습니다.",
            pipeline_label="Hybrid 표준검색",
            t_search_ms=t_search,
            t_total_ms=(time.time() - t_total) * 1000,
        )

    # CE 리랭킹 → 상위 3개
    t0 = time.time()
    ranked = _rerank(query, candidates, RERANK_TOP_N)
    t_rerank = (time.time() - t0) * 1000

    is_hybrid = retriever.is_hybrid
    logger.info(
        f"[standard] Hybrid{'(BM25+FAISS)' if is_hybrid else '(FAISS)'} "
        f"검색 {len(candidates)}건 → CE Rerank → {len(ranked)}건 "
        f"({t_search:.0f}ms + {t_rerank:.0f}ms)"
    )

    return SearchResult(
        ranked_docs=ranked,
        context=build_context(ranked),
        pipeline_label=f"{'Hybrid' if is_hybrid else 'FAISS'} 표준검색",
        t_search_ms=t_search,
        t_rerank_ms=t_rerank,
        t_total_ms=(time.time() - t_total) * 1000,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  모드 3: 심층 검색 (Multi-Query + Chain of Thought)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _expand_query_multi(query: str) -> List[str]:
    """
    Multi-Query: 원본 질문을 3가지 관점으로 확장.

    [확장 전략]
    1. 원본 질문 그대로
    2. 핵심 키워드만 추출 (명사 + 동사원형)
    3. 반대/예외 관점 ("...가 아닌 경우", "...예외 조항")

    [LLM 없이 규칙 기반으로 확장하는 이유]
    · LLM 확장: +1~2초 지연, API 비용 추가
    · 규칙 기반: +0.001초, NEDIS 도메인에 최적화된 패턴 적용
    · 병원 규정 특성상 "예외", "단서", "다만" 패턴이 중요
    """
    import re

    expanded = [query]  # 원본

    # ── 확장 2: 핵심 명사구 추출 ─────────────────────────
    # 조사·어미 제거 후 핵심 명사구
    clean = re.sub(r"[은는이가을를에서에게의로으로부터까지에서도]\s*", " ", query)
    clean = re.sub(r"[?？\!！\.]", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    # 구어체 표현 제거
    clean = re.sub(
        r"어떻게|알려주세요|궁금합니다|무엇인가요|알고싶어요|뭐야|뭔가요", "", clean
    ).strip()
    if clean and clean != query:
        expanded.append(clean)

    # ── 확장 3: 예외·단서 조항 관점 ─────────────────────
    # NEDIS 가이드에서 예외/판정 사례가 중요한 패턴들
    exception_patterns = [
        ("판정", f"{query} 판정 기준 예외"),
        ("기준", f"{query} 예외 사항 단서 조항"),
        ("코드", f"{query} 오류 코드 적용 제외"),
        ("신고", f"{query} 신고 제외 대상 예외"),
        ("입력", f"{query} 입력 오류 수정 절차"),
    ]
    for keyword, variant in exception_patterns:
        if keyword in query:
            expanded.append(variant)
            break
    else:
        # 키워드 없으면 일반 예외 관점
        expanded.append(f"{query} 예외 단서 조항 특례")

    return expanded[:3]  # 최대 3개


def _run_deep(query: str, vector_db: FAISS) -> SearchResult:
    """
    심층 검색: Multi-Query Retrieval + 확장된 컨텍스트.

    [파이프라인]
    1. _expand_query_multi() 로 쿼리 3개 생성
    2. 각 쿼리마다 Hybrid 검색 수행
    3. 결과 풀을 RRF로 최종 병합
    4. CE 리랭킹 → 상위 5개 (standard보다 더 많은 컨텍스트)
    5. LLM에 Chain of Thought 프롬프트 적용 (build_context에서 처리)

    [standard와 차이]
    · 쿼리 확장 3개 → 더 넓은 문서 커버리지
    · 상위 5개 청크 (standard는 3개)
    · 예외 조항·판정 사례 포함 가능성 높음
    · 응답 시간 2~3배 증가 감수
    """
    from core.hybrid_retriever import reciprocal_rank_fusion

    t_total = time.time()
    # Hybrid 검색 — 캐시된 인스턴스 사용
    retriever = _get_retriever(vector_db)

    # ── Step 1: 쿼리 확장 ─────────────────────────────────
    expanded_queries = _expand_query_multi(query)
    rewritten_display = " | ".join(expanded_queries)
    logger.info(f"[deep] 쿼리 확장: {expanded_queries}")

    # ── Step 2: 각 쿼리로 Hybrid 검색 ────────────────────
    all_candidates: List[Tuple[Document, float]] = []
    t0 = time.time()

    for q in expanded_queries:
        hits = retriever.search(q, top_k=10)  # 쿼리당 10개
        all_candidates.extend(hits)

    t_search = (time.time() - t0) * 1000

    if not all_candidates:
        return SearchResult(
            ranked_docs=[],
            context="관련 규정 문서를 찾을 수 없습니다.",
            pipeline_label="Multi-Query 심층검색",
            rewritten_query=rewritten_display,
            t_search_ms=t_search,
            t_total_ms=(time.time() - t_total) * 1000,
        )

    # ── Step 3: 결과 풀 RRF 병합 (중복 제거) ─────────────
    # all_candidates를 FAISS/BM25처럼 두 그룹으로 분리하지 않고
    # 전체를 하나의 랭킹으로 재정렬 (확장 쿼리 순서 = 우선순위)
    seen_ids: dict = {}
    merged = []
    for rank, (doc, score) in enumerate(all_candidates, 1):
        key = doc.page_content[:80]
        if key not in seen_ids:
            seen_ids[key] = True
            merged.append((doc, score))
        if len(merged) >= FAISS_TOP_K:
            break

    # ── Step 4: CE 리랭킹 → 상위 5개 (deep은 더 많은 컨텍스트) ──
    deep_top_n = min(5, RERANK_TOP_N + 2)
    t0 = time.time()
    ranked = _rerank(query, merged, deep_top_n)
    t_rerank = (time.time() - t0) * 1000

    logger.info(
        f"[deep] Multi-Query {len(expanded_queries)}개 쿼리 "
        f"→ {len(all_candidates)}건 수집 "
        f"→ CE Rerank → {len(ranked)}건 "
        f"({t_search:.0f}ms + {t_rerank:.0f}ms)"
    )

    # ── Step 5: CoT 컨텍스트 빌드 ────────────────────────
    # build_context에 CoT 지시문 주입
    base_context = build_context(ranked)
    cot_context = _build_cot_context(query, expanded_queries, base_context)

    return SearchResult(
        ranked_docs=ranked,
        context=cot_context,
        pipeline_label="Multi-Query 심층검색",
        rewritten_query=rewritten_display,
        t_search_ms=t_search,
        t_rerank_ms=t_rerank,
        t_total_ms=(time.time() - t_total) * 1000,
    )


def _build_cot_context(
    original_query: str,
    expanded_queries: List[str],
    base_context: str,
) -> str:
    """
    심층 검색용 Chain of Thought 컨텍스트 빌더.

    [CoT 구조]
    원본 검색 컨텍스트 + LLM에게 단계별 추론 지시
    → 예외 조항, 판정 사례, 적용 기준을 단계별로 검토하도록 유도

    [NEDIS 특화]
    · NEDIS(National Emergency Department Information System) 가이드
    · 응급실 보고 기준, 코드 적용 예외, 판정 불일치 사례가 중요
    """
    queries_text = "\n".join(f"  {i + 1}. {q}" for i, q in enumerate(expanded_queries))

    cot_instruction = f"""
[심층 분석 요청]
다음 질문에 대해 아래 참조 문서를 단계별로 분석해 주세요.

원본 질문: {original_query}
확장 검색 쿼리:
{queries_text}

[분석 절차 — Chain of Thought]
Step 1. 질문의 핵심 개념과 적용 범위를 명확히 정의
Step 2. 참조 문서에서 직접 적용되는 기준/조항 식별
Step 3. 예외 사항, 단서 조항, 특례 규정 확인
Step 4. 판정 기준이 애매한 경우 우선순위 원칙 적용
Step 5. 최종 답변 및 근거 명시

"""
    return cot_instruction + base_context


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  메인 인터페이스 — main.py에서 사용
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def iter_search_steps(
    query: str,
    vector_db: FAISS,
    mode: str = "standard",
) -> Iterator[Tuple[str, Optional[SearchResult]]]:
    """
    Streamlit st.status 위젯용 단계별 검색 이터레이터.

    [v2.0 개선]
    · QueryRewriter 통합: 구어체→문서어 정규화 (연차→연차휴가)
    · 결과 캐시 통합: 동일 쿼리 10분 내 재검색 시 즉시 반환
    · fast 모드도 쿼리 정규화 적용

    Args:
        query:     사용자 질문
        vector_db: FAISS 인스턴스
        mode:      "fast" | "standard" | "deep"

    Yields:
        (진행 메시지, SearchResult | None)
    """
    _MODE_LABELS = {
        "fast": "⚡ BM25 빠른 검색 (키워드 매칭)",
        "standard": "⚖️ Hybrid 표준 검색 (Vector + BM25 + Rerank)",
        "deep": "🧠 Multi-Query 심층 검색 (3쿼리 확장 + CoT)",
    }
    yield _MODE_LABELS.get(mode, f"검색 모드: {mode}"), None

    # ── QueryRewriter: 구어체 → 문서어 정규화 ────────────────────
    # "연차 어떻게 써요?" → "연차휴가 신청"
    # "야근비가 얼마야?" → "연장근로 수당"
    try:
        _rewriter = get_query_rewriter()
        _rewrite = _rewriter.rewrite(query)
        _search_query = _rewrite.search_query
        if _rewrite.was_rewritten:
            yield f"쿼리 정규화: '{query[:20]}' → '{_search_query[:30]}'", None
    except Exception:
        _search_query = query  # rewriter 실패 시 원본 사용

    # ── 캐시 조회 (fast/standard만 — deep은 항상 새로 탐색) ──────
    if mode != "deep":
        _ck = _make_cache_key(mode, _search_query)
        _cached_docs = _cache_get(_ck)
        if _cached_docs is not None:
            yield "캐시 HIT — 즉시 반환 (TTL 10분)", None
            _context = build_context(_cached_docs)
            yield (
                f"컨텍스트 구축 완료 (~{len(_context) // 2} 토큰)",
                SearchResult(
                    ranked_docs=_cached_docs,
                    context=_context,
                    pipeline_label="캐시 ⚡",
                    t_total_ms=0.1,
                ),
            )
            return

    try:
        if mode == "fast":
            yield "BM25 키워드 인덱스 검색 중...", None
            result = _run_fast(_search_query, vector_db)
            yield (
                f"BM25 검색 완료: {result.hit_count}건 ({result.t_search_ms:.0f}ms)",
                None,
            )

        elif mode == "deep":
            expanded = _expand_query_multi(_search_query)
            yield f"쿼리 확장: {len(expanded)}개 → {' | '.join(expanded[:2])}...", None
            yield "Multi-Query Hybrid 검색 중...", None
            result = _run_deep(_search_query, vector_db)
            yield (
                f"심층 검색 완료: {result.hit_count}건 선별 "
                f"({result.t_search_ms:.0f}ms + "
                f"Rerank {result.t_rerank_ms:.0f}ms)",
                None,
            )

        else:  # standard (기본)
            yield "Hybrid 검색 중 (Vector + BM25)...", None
            result = _run_standard(_search_query, vector_db)
            yield (
                f"검색 완료: 상위 {result.hit_count}건 선별 "
                f"({result.t_search_ms:.0f}ms + "
                f"Rerank {result.t_rerank_ms:.0f}ms)",
                None,
            )

        # 결과 캐시 저장 (deep 제외)
        if mode != "deep" and result and result.ranked_docs:
            _cache_set(_make_cache_key(mode, _search_query), result.ranked_docs)

        yield f"컨텍스트 구축 완료 (~{len(result.context) // 2} 토큰)", result

    except Exception as exc:
        logger.error(f"[search_engine] {mode} 모드 오류: {exc}", exc_info=True)
        yield f"검색 오류: {exc}", None
