"""
core/hybrid_retriever.py  ─  BM25 + FAISS 하이브리드 검색 (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[HybridRetriever 가 필요한 이유]

  FAISS (벡터 검색) 장점: 의미 유사도 (paraphrase 포착)
  FAISS 단점:             정확한 고유명사·숫자 검색 취약

  BM25 (키워드 검색) 장점: "제26조", "2024년 3월" 등 정확 매칭
  BM25 단점:               동의어·문맥 이해 없음

  [병원 규정 특성상 두 가지 모두 필요]
    "연차휴가 신청 절차" → FAISS 강점 (의미 검색)
    "제26조 제3항"       → BM25 강점 (정확 매칭)
    → 두 결과를 RRF(Reciprocal Rank Fusion) 으로 병합

[RRF (Reciprocal Rank Fusion) 공식]
  score(d) = Σ 1/(k + rank_i(d))   where k=60 (표준값)
  → 여러 랭킹 시스템 결과를 편향 없이 통합
  → 2009 Cormack et al. 논문에서 검증된 방법론

[설치 필요]
  pip install rank-bm25
  (이미 설치된 경우 건너뜀)

[속도]
  BM25 검색: 약 0.01~0.05초 (Python 연산)
  → 전체 파이프라인 추가 지연: 약 0.05초
  → 검색 품질 향상 대비 비용 미미
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# RRF 파라미터 (논문 권고값: 60)
RRF_K: int = 60


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BM25 인덱스 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class BM25Index:
    """BM25 인덱스 + 원본 문서 목록"""
    model:     object          # rank_bm25.BM25Okapi 인스턴스
    documents: List[Document]  # 인덱싱된 문서 순서 (BM25 결과와 1:1 대응)


def _tokenize_ko(text: str) -> List[str]:
    """
    한국어 간이 토크나이저.

    [전략]
    · 정식 형태소 분석기(Mecab) 없이 작동
    · 공백 + 구두점 기준 분리
    · BM25 에서는 정확 토큰 매칭이 핵심이므로
      어절 단위로도 충분히 작동

    [개선 옵션]
    · konlpy 설치 후 Okt/Mecab 사용 시 정확도↑
    · 현재는 의존성 최소화 우선
    """
    import re
    # 한글·영문·숫자·점·하이픈 유지, 나머지 제거
    text = re.sub(r"[^\w\s가-힣a-zA-Z0-9.]", " ", text)
    tokens = text.lower().split()
    # 1글자 토큰 제거 (노이즈)
    return [t for t in tokens if len(t) > 1]


def build_bm25_index(documents: List[Document]) -> Optional[BM25Index]:
    """
    문서 리스트로 BM25 인덱스 생성.

    [호출 시점]
    벡터 DB 로드 후 1회 실행 (약 0.5~2초, 문서 수에 비례).
    st.cache_resource 로 캐싱하면 앱 재시작 전까지 1회만 실행.

    Args:
        documents: FAISS에서 추출한 Document 리스트

    Returns:
        BM25Index 또는 None (rank-bm25 미설치 시)
    """
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning(
            "rank-bm25 미설치 → BM25 비활성화. "
            "`pip install rank-bm25` 로 설치하세요."
        )
        return None

    if not documents:
        logger.warning("BM25 인덱스: 문서 없음")
        return None

    t0 = time.time()
    corpus = [_tokenize_ko(doc.page_content) for doc in documents]
    bm25   = BM25Okapi(corpus)

    logger.info(
        f"BM25 인덱스 구축: {len(documents)}개 문서 "
        f"({time.time()-t0:.2f}초)"
    )
    return BM25Index(model=bm25, documents=documents)


def build_bm25_from_faiss(vector_db: FAISS) -> Optional[BM25Index]:
    """
    FAISS vector_db 에서 모든 문서를 추출하여 BM25 인덱스 생성.

    [FAISS docstore 구조]
    FAISS.docstore._dict: {id: Document} 딕셔너리
    → 전체 Document 리스트 추출 가능
    """
    try:
        docs = list(vector_db.docstore._dict.values())
        logger.info(f"FAISS에서 {len(docs)}개 문서 추출 → BM25 인덱싱")
        return build_bm25_index(docs)
    except Exception as exc:
        logger.warning(f"BM25 인덱스 구축 실패: {exc}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RRF 융합
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _rrf_score(rank: int, k: int = RRF_K) -> float:
    """RRF 개별 점수: 1 / (k + rank)"""
    return 1.0 / (k + rank)


def reciprocal_rank_fusion(
    faiss_results: List[Tuple[Document, float]],
    bm25_results:  List[Tuple[Document, float]],
    top_k:         int = 15,
) -> List[Tuple[Document, float]]:
    """
    FAISS + BM25 결과를 RRF 로 병합.

    Args:
        faiss_results: [(Document, l2_distance), ...]  ← 낮을수록 유사
        bm25_results:  [(Document, bm25_score), ...]   ← 높을수록 유사
        top_k:         반환할 후보 수

    Returns:
        [(Document, rrf_score), ...]  ← 내림차순

    [문서 동일성 판별]
    source + page 조합으로 중복 감지.
    (Document 객체 비교는 메모리 주소 비교라 FAISS/BM25 간 동일 문서 감지 불가)
    """
    rrf_scores: Dict[str, float]    = {}
    doc_map:    Dict[str, Document] = {}

    def _doc_id(doc: Document) -> str:
        """문서 고유 ID: source + page + 내용 앞 50자"""
        src  = doc.metadata.get("source", "")
        page = doc.metadata.get("page", "")
        return f"{src}::{page}::{doc.page_content[:50]}"

    # FAISS 결과 RRF 점수 누적 (L2 거리 기준 순서: 낮을수록 우선)
    sorted_faiss = sorted(faiss_results, key=lambda x: x[1])
    for rank, (doc, _) in enumerate(sorted_faiss, start=1):
        did = _doc_id(doc)
        rrf_scores[did] = rrf_scores.get(did, 0.0) + _rrf_score(rank)
        doc_map[did]    = doc

    # BM25 결과 RRF 점수 누적 (BM25 스코어: 높을수록 우선)
    sorted_bm25 = sorted(bm25_results, key=lambda x: x[1], reverse=True)
    for rank, (doc, _) in enumerate(sorted_bm25, start=1):
        did = _doc_id(doc)
        rrf_scores[did] = rrf_scores.get(did, 0.0) + _rrf_score(rank)
        doc_map[did]    = doc

    # RRF 점수 내림차순 정렬 후 top_k 반환
    merged = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return [(doc_map[did], score) for did, score in merged]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HybridRetriever 클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HybridRetriever:
    """
    BM25 + FAISS 하이브리드 검색기.

    [사용 예시]
        # 앱 초기화 시 1회
        retriever = HybridRetriever(vector_db)

        # 검색 시마다
        candidates = retriever.search(query, top_k=15)
        # → CE 리랭킹 입력으로 사용

    [초기화 시간]
    · BM25 인덱스 구축: 0.5~2초 (문서 수에 비례)
    · st.cache_resource 로 캐싱 필요
    """

    def __init__(self, vector_db: FAISS) -> None:
        self._vector_db  = vector_db
        self._bm25: Optional[BM25Index] = None
        self._bm25_ready = False   # 지연 초기화 플래그

    def _ensure_bm25(self) -> None:
        """
        BM25 인덱스 지연 초기화 (첫 search() 호출 시 1회만 실행).

        [변경 이유]
        기존: __init__ 에서 즉시 build_bm25_from_faiss() 호출
              → 13,021개 문서 처리에 30~60초 소요 → 앱 로딩 1분 지연
        변경: 첫 번째 search() 호출 시 초기화
              → 앱 로딩 2~3초 (FAISS 로드만)
              → 첫 검색에 약 3~5초 추가 (이후 캐시로 즉시 응답)
        """
        if self._bm25_ready:
            return
        self._bm25_ready = True   # 재진입 방지 (실패해도 재시도 X)
        try:
            self._bm25 = build_bm25_from_faiss(self._vector_db)
        except Exception as exc:
            logger.warning(f"BM25 초기화 실패 → FAISS 단독 사용: {exc}")
            self._bm25 = None

    def search(
        self,
        query:    str,
        top_k:    int   = 15,
        bm25_top: int   = 20,     # BM25 후보 수 (더 많이 뽑아 RRF)
        alpha:    float = 0.5,    # 현재 미사용 (RRF는 점수 무관)
    ) -> List[Tuple[Document, float]]:
        """
        하이브리드 검색 실행.

        Args:
            query:    검색 쿼리
            top_k:    최종 반환 후보 수 (CE 리랭킹 입력)
            bm25_top: BM25 내부 후보 수

        Returns:
            [(Document, rrf_score), ...]  CE 리랭킹 입력용
        """
        # ── BM25 지연 초기화 (최초 1회, 이후 캐시) ───────────────
        self._ensure_bm25()

        t0 = time.time()

        # ── FAISS 검색 ─────────────────────────────────────────
        faiss_results: List[Tuple[Document, float]] = (
            self._vector_db.similarity_search_with_score(query, k=top_k)
        )

        # BM25 없으면 FAISS 단독 반환
        if self._bm25 is None:
            logger.debug("BM25 없음 → FAISS 단독 반환")
            return faiss_results[:top_k]

        # ── BM25 검색 ─────────────────────────────────────────
        query_tokens = _tokenize_ko(query)
        bm25_scores  = self._bm25.model.get_scores(query_tokens)

        # BM25 상위 bm25_top 개 추출
        import numpy as np
        top_bm25_idx = np.argsort(bm25_scores)[::-1][:bm25_top]
        bm25_results = [
            (self._bm25.documents[i], float(bm25_scores[i]))
            for i in top_bm25_idx
            if bm25_scores[i] > 0   # 관련 없는 문서 제외
        ]

        # ── RRF 융합 ───────────────────────────────────────────
        merged = reciprocal_rank_fusion(faiss_results, bm25_results, top_k=top_k)

        elapsed = time.time() - t0
        logger.info(
            f"HybridRetriever: FAISS {len(faiss_results)}건 + "
            f"BM25 {len(bm25_results)}건 → RRF {len(merged)}건 "
            f"({elapsed:.3f}초)"
        )
        return merged

    @property
    def is_hybrid(self) -> bool:
        """BM25 활성화 여부"""
        return self._bm25 is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  main.py 통합 가이드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
#  [기존 코드]
#  vector_db = _load_vector_db()
#  candidates = vector_db.similarity_search_with_score(query, k=40)
#
#  [변경 코드]
#  from core.hybrid_retriever import HybridRetriever
#
#  @st.cache_resource(show_spinner=False)
#  def _load_hybrid_retriever() -> HybridRetriever:
#      vector_db = _load_vector_db()
#      return HybridRetriever(vector_db)
#
#  retriever = _load_hybrid_retriever()
#  candidates = retriever.search(query, top_k=15)
#  # → 이후 CE 리랭킹은 기존 retriever.py의 _rerank() 사용
#
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━