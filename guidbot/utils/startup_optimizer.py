"""
utils/startup_optimizer.py  ─  앱 시작 성능 최적화 v2.1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v2.1 변경사항 — v8.0 rag_pipeline 통합 대응]

  ✅ 제거:
    - from core.search_engine import _get_retriever  (private, 파일 삭제됨)
    - retriever._ensure_bm25()                       (private 메서드 직접 호출)
    - from core.retriever import _get_cross_encoder  (private 함수 직접 호출)

  ✅ 대체:
    - pipeline.warmup_retriever()   (RAGPipeline public 메서드)
    - pipeline.warmup_ce()          (RAGPipeline public 메서드)

[왜 백그라운드 워밍업이 필요한가]

  문제:
    · BM25 인덱스 구축 (7,000+ 문서 토크나이징) → ~2~8초
    · Cross-Encoder 첫 추론 (JIT 컴파일) → ~1~2초
    → 첫 번째 질문 입력 시 사용자가 최대 10초 대기

  해결:
    · _load_resources() 완료 직후 백그라운드 스레드에서 워밍업 실행
    · 사용자가 사이드바 탐색, 검색 모드 선택하는 동안 준비 완료
    · 첫 질문 입력 시 이미 캐시됨 → 즉시 검색

[스레드 안전성]
  · HybridRetriever 싱글톤: _get_retriever() 내부 Lock 으로 보호 (v8.0)
  · CE 모델 싱글톤: retriever 내부 Lock 으로 보호
  · Streamlit st.cache_resource 와 충돌 없음 (모두 모듈 레벨 dict)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  결과 데이터클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class LoadResult:
    """
    parallel_load_resources() 반환값.

    Attributes:
        vector_db:  FAISS 인스턴스 (없으면 None)
        pipeline:   RAGPipeline 인스턴스 (없으면 None)
        t_vector_s: 벡터 DB 로드 소요 시간 (초)
        t_pipeline_s: 파이프라인 초기화 소요 시간 (초)
        success:    전체 로드 성공 여부
    """
    vector_db: Any = None
    pipeline: Any = None
    t_vector_s: float = 0.0
    t_pipeline_s: float = 0.0
    success: bool = False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  병렬 로드 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _load_vector_db() -> Tuple[Any, float]:
    """
    FAISS 벡터 DB를 로드합니다.

    Returns:
        (FAISS 인스턴스 또는 None, 소요 시간 초)
    """
    t0 = time.time()
    try:
        from core.vector_store import VectorStoreManager

        manager = VectorStoreManager(
            db_path=settings.rag_db_path,
            model_name=settings.embedding_model,
            cache_dir=str(settings.local_work_dir),
        )
        vector_db = manager.load()
        elapsed = time.time() - t0
        if vector_db is not None:
            logger.info(f"[프리로드] 벡터 DB 로드 완료: {elapsed:.1f}초")
        else:
            logger.warning("[프리로드] 벡터 DB 없음 → build_db.py 를 먼저 실행하세요")
        return vector_db, elapsed
    except Exception as exc:
        elapsed = time.time() - t0
        logger.error(f"[프리로드] 벡터 DB 로드 실패: {exc}")
        return None, elapsed


def _load_pipeline(vector_db: Any) -> Tuple[Any, float]:
    """
    RAGPipeline을 초기화합니다.

    Args:
        vector_db: FAISS 인스턴스 (None 이면 즉시 반환)

    Returns:
        (RAGPipeline 인스턴스 또는 None, 소요 시간 초)
    """
    if vector_db is None:
        return None, 0.0

    t0 = time.time()
    try:
        from core.rag_pipeline import get_pipeline

        pipeline = get_pipeline(vector_db)
        elapsed = time.time() - t0
        logger.info(f"[프리로드] RAGPipeline 초기화 완료: {elapsed:.1f}초")
        return pipeline, elapsed
    except Exception as exc:
        elapsed = time.time() - t0
        logger.error(f"[프리로드] RAGPipeline 초기화 실패: {exc}")
        return None, elapsed


def parallel_load_resources() -> LoadResult:
    """
    벡터 DB + RAGPipeline을 순차 로드합니다.

    [순차 로드 이유]
    · RAGPipeline 이 vector_db 에 의존 → 직렬화 불가피
    · 단, BM25/CE 워밍업은 백그라운드에서 병렬 수행 (start_background_warmup)

    Returns:
        LoadResult
    """
    vector_db, t_v = _load_vector_db()
    pipeline, t_p = _load_pipeline(vector_db)

    return LoadResult(
        vector_db=vector_db,
        pipeline=pipeline,
        t_vector_s=t_v,
        t_pipeline_s=t_p,
        success=(vector_db is not None and pipeline is not None),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  백그라운드 워밍업
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_bg_warmup_thread: Optional[threading.Thread] = None
_bg_warmup_done: bool = False
_bg_warmup_start: float = 0.0


def start_background_warmup(pipeline: Any) -> None:
    """
    앱 시작 직후 백그라운드에서 BM25 + CE 워밍업을 실행합니다.

    [v2.1 변경]
    · 이전: pipeline 대신 vector_db 를 받아 private 함수 직접 호출
      - _get_retriever(vector_db)._ensure_bm25()   ← private 접근
      - _get_cross_encoder().predict(...)          ← private 접근
    · 수정: pipeline.warmup_retriever(), pipeline.warmup_ce() 사용
      - 캡슐화 유지 + search_engine.py 삭제 후에도 동작

    [타이밍]
    main.py → _load_resources() 완료
           → start_background_warmup(pipeline) 호출 (non-blocking)
           → 백그라운드 스레드 시작
           → 사용자가 UI 탐색하는 동안 BM25 인덱싱 + CE 예열 진행
           → 첫 질문 입력 시 이미 완료 → 즉시 검색

    Args:
        pipeline: RAGPipeline 인스턴스 (None 이면 무시)
    """
    global _bg_warmup_thread, _bg_warmup_done, _bg_warmup_start

    if _bg_warmup_done or pipeline is None:
        return

    _bg_warmup_start = time.time()

    def _run() -> None:
        global _bg_warmup_done

        logger.info("백그라운드 워밍업 시작...")

        # 1. BM25 인덱스 구축 (가장 오래 걸림 ~2~8초)
        t0 = time.time()
        try:
            pipeline.warmup_retriever()   # ✅ public API 사용
            logger.info(f"[워밍업] BM25 완료: {time.time() - t0:.1f}초")
            bm25_ok = True
        except Exception as exc:
            logger.warning(f"[워밍업] BM25 실패 (무시): {exc}")
            bm25_ok = False

        # 2. Cross-Encoder JIT 예열 (~1~2초)
        t0 = time.time()
        try:
            pipeline.warmup_ce()          # ✅ public API 사용
            logger.info(f"[워밍업] CE 완료: {time.time() - t0:.1f}초")
            ce_ok = True
        except Exception as exc:
            logger.warning(f"[워밍업] CE 실패 (무시): {exc}")
            ce_ok = False

        total = time.time() - _bg_warmup_start
        logger.info(
            f"백그라운드 워밍업 완료: "
            f"BM25={'✅' if bm25_ok else '❌'} "
            f"CE={'✅' if ce_ok else '❌'} "
            f"총 {total:.1f}초"
        )
        _bg_warmup_done = True

    _bg_warmup_thread = threading.Thread(target=_run, daemon=True, name="warmup")
    _bg_warmup_thread.start()
    logger.info("백그라운드 워밍업 스레드 시작 (non-blocking)")


def get_warmup_status() -> dict:
    """
    워밍업 상태를 반환합니다 (관리자 패널 표시용).

    Returns:
        {"done": bool, "elapsed_s": float}
    """
    elapsed = time.time() - _bg_warmup_start if _bg_warmup_start > 0 else 0.0
    return {
        "done": _bg_warmup_done,
        "elapsed_s": elapsed,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Streamlit 로딩 UI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_loading_ui() -> None:
    """
    앱 초기 로딩 중 Streamlit 진행 표시 UI를 렌더링합니다.

    [사용법 — main.py]
        from utils.startup_optimizer import render_loading_ui
        render_loading_ui()          # 먼저 UI 표시
        vector_db, pipeline = _load_resources()   # 그 다음 로드

    [주의]
    · 이 함수는 UI 업데이트만 담당 (실제 로딩은 _load_resources 에서 수행)
    · st.cache_resource 캐시 히트 시 거의 즉시 반환
    """
    import streamlit as st

    steps = [
        ("🧠", "AI 언어 모델 초기화"),
        ("🔍", "문서 검색 엔진 준비"),
        ("📚", "규정집 데이터베이스 로딩"),
    ]

    container = st.container()
    with container:
        cols = st.columns([1, 6])
        with cols[1]:
            st.markdown("### ⏳ 시스템 준비 중...")
            progress = st.progress(0)
            status_text = st.empty()

            for i, (icon, label) in enumerate(steps):
                progress.progress((i + 1) / len(steps))
                status_text.markdown(
                    f"**{icon} {label}** `({i + 1}/{len(steps)})`"
                )
                time.sleep(0.1)

            status_text.markdown("✅ **준비 완료!**")
            time.sleep(0.2)

    container.empty()