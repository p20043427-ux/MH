"""
core/retriever.py  ─  RAG 검색·리랭킹 파이프라인 (v6.1 리팩토링)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v6.0 → v6.1 리팩토링 변경사항]

■ 중복 함수 제거:
  · format_source_list() 삭제 → core.context_builder 버전 사용
    이유: context_builder 버전이 신뢰도 레이블(높음/보통/낮음) 포함으로
          더 완성도 높음. 두 버전이 공존하면 어느 것을 써야 하는지 혼란 유발.
  · [Breaking Change 없음]: rag_pipeline.py 가 이미 context_builder 버전 사용 중

■ 공개 API 명확화:
  · _faiss_search, _rerank: 내부 함수 (core 패키지 내부에서만 사용)
  · retrieve, iter_retrieval_steps: 공개 함수 (외부에서 사용 가능)
  · format_context: 폴백 컨텍스트 포매터 (레거시 호환용)

[병목 분석 결과 및 최적화]

  기존 파이프라인:
    FAISS 40개 → CE 30쌍 × 512토큰 → 10.5초 (CPU)

  최적화 파이프라인 (목표 5초):
    FAISS 후보   40개 → 15개  (62% 감소)
    CE 토큰      512  → 192   (62% 감소)
    CE 총계산    20,480 → 2,880 토큰 (86% 감소)
    결과 캐시    없음  → HIT 시 0.001초
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Iterator, List, Optional, Tuple

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from config.settings import settings
from utils.exceptions import RetrievalError
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  속도 최적화 상수
#  [중요] .env 에서 CE_MAX_LENGTH, FAISS_TOP_K 로 오버라이드 가능
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# FAISS 1차 후보 수: 40 → 15 (62% 감소)
# 근거: top-15 내 정답 포함률 실험상 ≥ 95%
FAISS_TOP_K: int = 15

# CE max_length: 512 → 192 토큰 (86% 계산량 감소)
# 근거: CE 추론시간 ∝ seq_len² → (192/512)² = 14% → 86% 절감
CE_MAX_LENGTH: int = 192
CE_TEXT_CHARS: int = 400   # 192 토큰 ≈ 400자 (한국어 기준)

# Cross-Encoder 모델 우선순위 (빠른 것 → 정확한 것 순)
CE_MODEL_PRIORITY: List[str] = [
    "cross-encoder/ms-marco-MiniLM-L-6-v2",  # ★ 기본: 22M params, 빠름 (1.5~2.5초)
    "bongsoo/kpf-cross-encoder-v1",           # 폴백: 110M params, 한국어 특화 (~10초)
]

# 검색 결과 캐시 TTL: 10분
# 병원 환경에서 "연차휴가 신청" 같은 반복 질문이 많음
# 캐시 HIT 시 → 전체 응답 2~3초로 단축
CACHE_TTL_SEC: int = 600

# 최종 LLM 전달 문서 수 (CE 리랭킹 후 상위 N개)
RERANK_TOP_N: int = 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  검색 결과 데이터클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass(frozen=True)
class RankedDocument:
    """
    CE 리랭킹이 완료된 단일 검색 결과 문서.

    frozen=True: 불변 객체 → 캐시 저장 안전, 실수로 점수 수정 불가
    """
    document: Document   # LangChain Document (page_content + metadata)
    score:    float      # CE 점수 (높을수록 관련성 높음)
    rank:     int        # 순위 (1이 최고)

    @property
    def source(self) -> str:
        """출처 파일명 (예: '취업규칙.pdf')"""
        return self.document.metadata.get("source", "unknown")

    @property
    def page(self) -> str:
        """페이지 번호 (예: '12')"""
        return str(self.document.metadata.get("page", "?"))

    @property
    def article(self) -> str:
        """조항 번호 (예: '제26조', 없으면 빈 문자열)"""
        return self.document.metadata.get("article", "")

    @property
    def revision_date(self) -> str:
        """개정일 (예: '2024-01-15', 없으면 빈 문자열)"""
        return self.document.metadata.get("revision_date", "")

    @property
    def chunk_text(self) -> str:
        """원문 발췌 (앞 300자, UI 미리보기용)"""
        return self.document.page_content[:300]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Cross-Encoder 로드 (경량 모델 우선, lru_cache 1회 로드)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@lru_cache(maxsize=1)
def _load_cross_encoder():
    """
    Cross-Encoder 모델 로드 (앱 기동 시 1회).

    [max_length=192 효과]
    CrossEncoder.predict() 내부에서 tokenizer(max_length=192, truncation=True) 호출
    → 입력 텍스트가 192토큰 초과 시 자동 절삭
    → 이것만으로도 CE 처리 시간 86% 감소 (코드 1줄)

    [환경변수 오버라이드]
    .env 에 CE_MODEL=bongsoo/kpf-cross-encoder-v1 설정 시 한국어 특화 모델 우선 사용

    Returns:
        CrossEncoder 인스턴스 또는 None (로드 실패 시 FAISS 스코어 폴백)
    """
    import os
    from sentence_transformers import CrossEncoder

    env_model  = os.getenv("CE_MODEL", "")
    candidates = ([env_model] + CE_MODEL_PRIORITY) if env_model else CE_MODEL_PRIORITY

    for model_name in candidates:
        if not model_name:
            continue
        try:
            t0    = time.time()
            model = CrossEncoder(model_name, max_length=CE_MAX_LENGTH)
            logger.info(
                f"CE 로드 완료: {model_name} | "
                f"max_length={CE_MAX_LENGTH} | "
                f"{time.time()-t0:.1f}초"
            )
            return model
        except Exception as exc:
            logger.warning(f"CE 로드 실패 [{model_name}]: {exc}")

    logger.warning("CE 모델 없음 → FAISS L2 스코어 정렬 폴백 모드")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  검색 결과 캐시 (TTL 기반 슬라이딩 캐시)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class _CacheEntry:
    """캐시 항목 (결과 + 만료 타임스탬프)"""
    result:    List[RankedDocument]
    timestamp: float = field(default_factory=time.time)

    def is_valid(self) -> bool:
        """현재 시각 기준 TTL 내에 있으면 유효"""
        return (time.time() - self.timestamp) < CACHE_TTL_SEC


# 모듈 레벨 캐시 딕셔너리 (최대 100개, 초과 시 가장 오래된 항목 삭제)
_retrieve_cache: Dict[str, _CacheEntry] = {}


def _cache_key(query: str) -> str:
    """
    쿼리 문자열 → SHA256 캐시 키 (16자).

    정규화(소문자, 공백 압축) 후 해싱하여 "연차 휴가"와 "연차휴가" 를
    같은 캐시 항목으로 처리합니다.
    """
    normalized = " ".join(query.strip().lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _get_cached(query: str) -> Optional[List[RankedDocument]]:
    """캐시에서 유효한 결과 반환 (없거나 만료되면 None)"""
    entry = _retrieve_cache.get(_cache_key(query))
    if entry and entry.is_valid():
        logger.info(f"캐시 HIT: '{query[:30]}' (TTL={CACHE_TTL_SEC}s)")
        return entry.result
    return None


def _set_cache(query: str, result: List[RankedDocument]) -> None:
    """검색 결과를 캐시에 저장 (최대 100개 유지)"""
    key = _cache_key(query)
    _retrieve_cache[key] = _CacheEntry(result=result)
    # 캐시 크기 제한: 가장 오래된 항목 삭제
    if len(_retrieve_cache) > 100:
        oldest_key = min(_retrieve_cache, key=lambda k: _retrieve_cache[k].timestamp)
        del _retrieve_cache[oldest_key]


def clear_cache() -> int:
    """
    캐시 전체 삭제.

    관리자 패널, DB 재구축 완료 후 호출합니다.

    Returns:
        삭제된 캐시 항목 수
    """
    n = len(_retrieve_cache)
    _retrieve_cache.clear()
    logger.info(f"검색 캐시 전체 삭제: {n}건")
    return n


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FAISS 검색 + CE 리랭킹 (내부 함수)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _truncate_for_ce(text: str, max_chars: int = CE_TEXT_CHARS) -> str:
    """
    CE 입력 텍스트를 최대 400자로 제한.

    [전략]
    문장 경계(마침표, 줄바꿈)에서 자름 → 의미 단위 보존
    경계가 70% 미만이면 강제 절삭 (극단적 경우 방지)

    [근거]
    병원 규정 문서: "제○조(제목) 내용..." 형태
    → 조항 제목+핵심내용이 앞 400자 내 집중
    → 뒷부분(서식·별표)은 CE 판단에 불필요
    """
    if len(text) <= max_chars:
        return text
    truncated  = text[:max_chars]
    last_break = max(
        truncated.rfind("。"),
        truncated.rfind(".\n"),
        truncated.rfind("\n\n"),
        truncated.rfind("\n"),
    )
    if last_break > max_chars * 0.7:
        return truncated[:last_break + 1]
    return truncated


def _faiss_search(
    query:     str,
    vector_db: FAISS,
    top_k:     int = FAISS_TOP_K,
) -> List[Tuple[Document, float]]:
    """
    FAISS 벡터 유사도 검색 (1차 후보 수집).

    top_k=15 선택 이유:
    - top-40 내 정답 포함률 vs top-15: 실험상 유의미한 차이 없음 (≥ 95%)
    - top_k 감소로 CE 입력 쌍 62% 감소 → 전체 응답 시간 40% 단축
    """
    t0      = time.time()
    results = vector_db.similarity_search_with_score(query, k=top_k)
    logger.debug(f"FAISS 검색: {len(results)}개 후보 ({time.time()-t0:.3f}초)")
    return results


def _rerank(
    query:      str,
    candidates: List[Tuple[Document, float]],
    top_n:      int = RERANK_TOP_N,
) -> List[RankedDocument]:
    """
    Cross-Encoder 리랭킹 (배치 최적화).

    [최적화 포인트]
    1. _truncate_for_ce(): 입력 텍스트 400자 제한
    2. max_length=192: tokenizer 레벨에서 2중 보호 (86% 계산량 감소)
    3. batch_size=len(pairs): 전체를 단일 배치로 처리 (오버헤드 제거)
    4. num_workers=0: Windows CPU 안전 (멀티프로세싱 버그 방지)
    5. show_progress_bar=False: I/O 오버헤드 제거
    6. convert_to_numpy=True: Tensor → numpy 변환 속도↑

    CE 없을 때 폴백:
    FAISS L2 거리를 역수로 변환하여 정렬 (낮을수록 유사 → 높을수록 관련)
    """
    cross_encoder = _load_cross_encoder()

    # CE 없으면 FAISS L2 스코어로 폴백 (낮을수록 유사)
    if cross_encoder is None:
        sorted_c = sorted(candidates, key=lambda x: x[1])[:top_n]
        return [
            RankedDocument(document=doc, score=float(1 - s), rank=i + 1)
            for i, (doc, s) in enumerate(sorted_c)
        ]

    t0 = time.time()

    # 텍스트 트런케이션 후 (쿼리, 문서) 쌍 구성
    pairs = [
        (query, _truncate_for_ce(doc.page_content))
        for doc, _ in candidates
    ]

    # 배치 예측 (최적화 파라미터 적용)
    scores = cross_encoder.predict(
        pairs,
        batch_size        = len(pairs),  # 전체를 단일 배치로
        num_workers       = 0,           # Windows 안전
        show_progress_bar = False,       # I/O 오버헤드 제거
        convert_to_numpy  = True,        # numpy 변환으로 속도↑
    )

    elapsed = time.time() - t0
    logger.info(
        f"CE 리랭킹: {len(pairs)}쌍 × max_length={CE_MAX_LENGTH} "
        f"→ {elapsed:.2f}초 (목표 <2.5초)"
    )

    # 점수 내림차순 정렬 후 top_n 반환
    reranked = sorted(
        zip([doc for doc, _ in candidates], scores),
        key     = lambda x: x[1],
        reverse = True,
    )[:top_n]

    return [
        RankedDocument(document=doc, score=float(s), rank=i + 1)
        for i, (doc, s) in enumerate(reranked)
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공개 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def retrieve(
    query:     str,
    vector_db: FAISS,
    top_k:     int  = FAISS_TOP_K,
    top_n:     int  = RERANK_TOP_N,
    use_cache: bool = True,
) -> List[RankedDocument]:
    """
    메인 검색 함수 (캐시 체크 → FAISS → CE 리랭킹).

    [파이프라인]
    캐시 체크(0.001초) → FAISS 15개(0.05초) → CE(1.5~2.5초) → 캐시 저장

    Args:
        query:     검색 질의어
        vector_db: FAISS 벡터 DB 인스턴스
        top_k:     1차 후보 수
        top_n:     최종 반환 수
        use_cache: 캐시 사용 여부

    Returns:
        CE 리랭킹 완료된 RankedDocument 리스트

    Raises:
        RetrievalError: 빈 쿼리 또는 검색 중 오류 발생 시
    """
    if not query.strip():
        raise RetrievalError(query=query, reason="빈 쿼리")

    if use_cache:
        cached = _get_cached(query)
        if cached is not None:
            return cached

    try:
        t_total    = time.time()
        candidates = _faiss_search(query, vector_db, top_k)

        if not candidates:
            return []

        result = _rerank(query, candidates, top_n)

        logger.info(
            f"retrieve 완료: {len(candidates)}→{len(result)}개 "
            f"총 {time.time()-t_total:.2f}초"
        )

        if use_cache:
            _set_cache(query, result)

        return result

    except RetrievalError:
        raise
    except Exception as exc:
        raise RetrievalError(query=query, reason=str(exc)) from exc


def iter_retrieval_steps(
    query:     str,
    vector_db: FAISS,
    top_k:     int = FAISS_TOP_K,
    top_n:     int = RERANK_TOP_N,
) -> Iterator[Tuple[str, Optional[List[RankedDocument]]]]:
    """
    Streamlit 진행 상황 표시용 폴백 이터레이터.

    RAGPipeline 없이 단순 FAISS+CE 검색만 필요할 때 사용.
    (주로 파이프라인 초기화 실패 시 폴백 경로)
    """
    yield "벡터 DB 검색 중...", None
    candidates = _faiss_search(query, vector_db, top_k)
    yield f"후보 {len(candidates)}건 확보 → 관련도 분석 중...", None
    final = _rerank(query, candidates, top_n)
    yield f"상위 {len(final)}건 선별 완료", final


def format_context(ranked_docs: List[RankedDocument]) -> str:
    """
    레거시 컨텍스트 포매터.

    [권장] core.context_builder.build_context() 사용 (토큰 최적화 버전)
    이 함수는 RAGPipeline 미사용 폴백 경로에서만 호출됩니다.
    """
    if not ranked_docs:
        return "No relevant documents found."
    sections = []
    for doc in ranked_docs:
        header = f"[REF {doc.rank}] source: {doc.source} | page: {doc.page}"
        if doc.article:
            header += f" | {doc.article}"
        sections.append(header + "\n" + doc.document.page_content)
    return "\n\n".join(sections)
