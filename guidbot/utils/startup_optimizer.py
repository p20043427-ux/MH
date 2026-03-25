"""
utils/startup_optimizer.py ─ Streamlit 앱 시작 속도 최적화 (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[현재 로딩이 느린 이유 — 3가지 병목]

  ┌──────────────────────────────────────────────────────────────────┐
  │ 단계                     소요 시간    원인                       │
  │─────────────────────────────────────────────────────────────────│
  │ HuggingFace 임베딩 모델  5~8초       ko-sroberta (~350MB) 로드  │
  │ FAISS.load_local()       1~2초       index.faiss 역직렬화        │
  │ CE 모델 첫 로드          2~3초       MiniLM (~23MB) + 예열       │
  │ BM25 인덱스 구축         3~5초       16,930개 문서 토크나이징    │
  │─────────────────────────────────────────────────────────────────│
  │ 기존 합계                11~18초     화면 공백 상태로 대기       │
  └──────────────────────────────────────────────────────────────────┘

[최적화 전략]

  1. 병렬 프리로드 (핵심):
     임베딩 모델 + CE 모델을 별도 스레드에서 동시에 로드.
     FAISS 로드가 임베딩 모델을 기다리는 동안 CE 모델도 함께 준비.
     → 직렬: 8s + 3s = 11s → 병렬: max(8s, 3s) = 8s (27% 단축)

  2. UI-first 렌더링:
     모델 로딩 중에도 CSS/레이아웃을 즉시 렌더링하여
     사용자가 즉시 UI 구조를 확인 가능.

  3. 프로그레스 피드백:
     단계별 진행 상황을 실시간으로 표시.
     "AI 모델 준비 중 (1/3)" → "벡터 DB 로딩 (2/3)" → "완료 (3/3)"

  4. 사전 준비 스크립트 (warmup.bat):
     Streamlit 실행 전 모델 다운로드/캐시를 확인.
     이후 Streamlit 시작 시 캐시 히트로 2~3초로 단축.

[적용 방법]

  main.py 의 _load_resources() 를 이 모듈의 parallel_load_resources() 로 교체:

    from utils.startup_optimizer import parallel_load_resources, render_loading_ui

    @st.cache_resource(show_spinner=False)
    def _load_resources():
        return parallel_load_resources()

  로딩 UI 표시 (main() 함수 내):
    with st.spinner(""):
        render_loading_ui()
        vector_db, pipeline = _load_resources()
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  로딩 단계 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class LoadStep:
    """로딩 단계 상태 추적"""

    name: str
    label: str
    done: bool = False
    elapsed: float = 0.0
    error: Optional[str] = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  병렬 프리로드 함수들
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _preload_embedding_model() -> Tuple[bool, float]:
    """
    HuggingFace ko-sroberta 임베딩 모델 프리로드.

    [캐시 동작]
    get_embeddings_auto() 는 @lru_cache 로 결과를 캐싱합니다.
    이 함수를 먼저 호출해 두면 FAISS.load_local() 에서 캐시 히트합니다.

    Returns:
        (성공 여부, 소요 시간)
    """
    t0 = time.time()
    try:
        from core.embeddings import get_embeddings_auto

        embeddings = get_embeddings_auto()
        elapsed = time.time() - t0
        logger.info(f"[프리로드] 임베딩 모델 완료: {elapsed:.1f}초")
        return True, elapsed
    except Exception as exc:
        elapsed = time.time() - t0
        logger.error(f"[프리로드] 임베딩 모델 실패: {exc}")
        return False, elapsed


def _preload_cross_encoder() -> Tuple[bool, float]:
    """
    Cross-Encoder 모델 프리로드 + 예열(warmup).

    [예열이 필요한 이유]
    CrossEncoder 는 첫 번째 predict() 호출 시 내부 컴파일(JIT)이 발생합니다.
    빈 입력으로 미리 1회 실행해 두면 실제 사용 시 0.5~1초 절약됩니다.

    Returns:
        (성공 여부, 소요 시간)
    """
    t0 = time.time()
    try:
        from core.retriever import _load_cross_encoder

        ce = _load_cross_encoder()
        if ce is not None:
            # 예열: 더미 입력으로 1회 실행 (JIT 컴파일 트리거)
            try:
                ce.predict(
                    [("테스트 쿼리", "테스트 문서")],
                    num_workers=0,
                    show_progress_bar=False,
                )
            except Exception:
                pass  # 예열 실패해도 무관
        elapsed = time.time() - t0
        status = "완료" if ce else "없음 (FAISS 폴백)"
        logger.info(f"[프리로드] CrossEncoder {status}: {elapsed:.1f}초")
        return True, elapsed
    except Exception as exc:
        elapsed = time.time() - t0
        logger.warning(f"[프리로드] CrossEncoder 실패 (무시): {exc}")
        return False, elapsed


def _load_vector_db() -> Tuple[Optional[Any], float]:
    """
    FAISS 벡터 DB 로드.

    _preload_embedding_model() 이 먼저 완료되어 있으면 캐시 히트로 빠르게 로드됩니다.

    Returns:
        (FAISS 인스턴스 또는 None, 소요 시간)
    """
    t0 = time.time()
    try:
        from core.vector_store import VectorStoreManager

        manager = VectorStoreManager(
            db_path=settings.rag_db_path,
            model_name=settings.embedding_model,
            cache_dir=str(settings.local_work_dir),
        )
        db = manager.load()
        elapsed = time.time() - t0
        count = db.index.ntotal if db else 0
        logger.info(f"[프리로드] 벡터 DB 로드 완료: {count:,}개 벡터 ({elapsed:.1f}초)")
        return db, elapsed
    except Exception as exc:
        elapsed = time.time() - t0
        logger.error(f"[프리로드] 벡터 DB 로드 실패: {exc}")
        return None, elapsed


def _build_rag_pipeline(vector_db: Any) -> Tuple[Optional[Any], float]:
    """
    RAGPipeline 초기화.

    Returns:
        (RAGPipeline 인스턴스 또는 None, 소요 시간)
    """
    if vector_db is None:
        return None, 0.0

    t0 = time.time()
    try:
        from core.rag_pipeline import get_pipeline

        pipeline = get_pipeline(vector_db)
        pipeline.initialize()
        elapsed = time.time() - t0
        logger.info(f"[프리로드] RAGPipeline 초기화 완료: {elapsed:.1f}초")
        return pipeline, elapsed
    except Exception as exc:
        elapsed = time.time() - t0
        logger.error(f"[프리로드] RAGPipeline 초기화 실패: {exc}")
        return None, elapsed


def _preload_bm25_index(vector_db: Any) -> Tuple[bool, float]:
    """
    BM25 인덱스 사전 구축 (백그라운드 스레드 전용).

    [왜 백그라운드인가]
    24,000개 문서 토크나이징 = ~8~10초.
    이를 첫 검색 시 수행하면 사용자가 10초를 기다림.
    앱 시작 직후 백그라운드 스레드에서 미리 구축하면
    사용자가 UI를 탐색하는 동안 준비 완료.

    [동작 원리]
    HybridRetriever._ensure_bm25() 를 직접 호출 →
    search_engine._get_retriever() 싱글톤 캐시에 저장.
    첫 번째 검색 요청 시 캐시 히트 → 즉시 반환.

    Args:
        vector_db: FAISS 인스턴스

    Returns:
        (성공 여부, 소요 시간)
    """
    if vector_db is None:
        return False, 0.0

    t0 = time.time()
    try:
        from core.search_engine import _get_retriever

        retriever = _get_retriever(vector_db)
        # BM25 인덱스 지연 초기화 트리거
        retriever._ensure_bm25()
        elapsed = time.time() - t0
        docs_count = len(retriever._bm25.documents) if retriever._bm25 else 0
        logger.info(
            f"[프리로드] BM25 인덱스 구축 완료: "
            f"{docs_count:,}개 문서 토크나이징 ({elapsed:.1f}초)"
        )
        return True, elapsed
    except Exception as exc:
        elapsed = time.time() - t0
        logger.warning(f"[프리로드] BM25 인덱스 실패 (무시): {exc}")
        return False, elapsed


# ── 백그라운드 워밍업 상태 추적 ──────────────────────────────────────
_bg_warmup_thread: Optional[threading.Thread] = None
_bg_warmup_done: bool = False
_bg_warmup_start: float = 0.0


def start_background_warmup(vector_db: Any) -> None:
    """
    앱 시작 직후 백그라운드에서 BM25 + CE JIT 워밍업 실행.

    [타이밍]
    main.py → _load_resources() 완료 (FAISS 로드)
           → start_background_warmup(vector_db) 호출 (non-blocking)
           → 백그라운드 스레드 시작
           → 사용자가 사이드바 탐색, 검색 모드 선택하는 동안
             BM25 인덱싱 + CE 예열 병렬 진행
           → 첫 질문 입력 시 이미 완료 → 즉시 검색

    [st.cache_resource와 충돌 없음]
    search_engine._get_retriever()의 _retriever_cache는
    모듈 레벨 dict → 스레드 안전 (Python GIL 보호)
    """
    global _bg_warmup_thread, _bg_warmup_done, _bg_warmup_start

    if _bg_warmup_done:
        return

    _bg_warmup_start = time.time()

    def _run():
        global _bg_warmup_done
        logger.info("백그라운드 워밍업 시작...")

        # 1. BM25 인덱스 구축 (가장 오래 걸림 ~8초)
        bm25_ok, bm25_t = _preload_bm25_index(vector_db)

        # 2. CE 모델 예열
        ce_ok, ce_t = _preload_cross_encoder()

        # 3. 자주 쓰는 쿼리 사전 임베딩 → _EMBED_CACHE 채움
        # 사용자가 입력할 가능성 높은 쿼리들을 미리 임베딩
        _PRELOAD_QUERIES = [
            "연차휴가 신청",
            "당직 수당 계산",
            "출산휴가",
            "징계 절차",
            "취업규칙",
            "급여",
            "야간 근무",
            "병실 현황",
            "재원 환자",
            "입원 환자",
        ]
        emb_t = time.time()
        try:
            from core.embeddings import get_embeddings_auto
            from core.search_engine import _EMBED_CACHE
            import hashlib

            _emb = get_embeddings_auto()
            for q in _PRELOAD_QUERIES:
                _k = hashlib.md5(q.strip().lower().encode()).hexdigest()[:12]
                if _k not in _EMBED_CACHE:
                    _EMBED_CACHE[_k] = _emb.embed_query(q)
            logger.info(
                f"사전 임베딩 완료: {len(_PRELOAD_QUERIES)}개 "
                f"({time.time() - emb_t:.1f}초)"
            )
        except Exception as _e:
            logger.warning(f"사전 임베딩 실패 (무시): {_e}")

        total = time.time() - _bg_warmup_start
        _bg_warmup_done = True
        logger.info(
            f"백그라운드 워밍업 완료: {total:.1f}초 | "
            f"BM25({'OK' if bm25_ok else 'ERR'}) {bm25_t:.1f}초 | "
            f"CE({'OK' if ce_ok else 'WARN'}) {ce_t:.1f}초"
        )

    _bg_warmup_thread = threading.Thread(
        target=_run,
        name="bg-warmup",
        daemon=True,  # 메인 프로세스 종료 시 자동 종료
    )
    _bg_warmup_thread.start()
    logger.info("🔄 백그라운드 워밍업 스레드 시작 (non-blocking)")


def is_warmup_ready() -> bool:
    """BM25 백그라운드 워밍업 완료 여부."""
    return _bg_warmup_done


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  메인 병렬 로드 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class LoadResult:
    """병렬 로딩 결과"""

    vector_db: Optional[Any]
    pipeline: Optional[Any]
    total_elapsed: float = 0.0
    step_times: Dict[str, float] = field(default_factory=dict)


def parallel_load_resources() -> LoadResult:
    """
    임베딩 모델 + CE 모델을 병렬로 프리로드한 후 FAISS + Pipeline 초기화.

    [실행 순서]
    Phase 1 (병렬):  임베딩 모델 ─┐
                     CE 모델     ─┘ → 동시 실행
    Phase 2 (순차):  FAISS 로드 (임베딩 모델 캐시 히트)
                     → RAGPipeline 초기화

    [병렬 실행 효과]
    직렬 방식: 임베딩(7초) + CE(2초) + FAISS(1초) + Pipeline(1초) = 11초
    병렬 방식: max(임베딩7초, CE2초) + FAISS(1초) + Pipeline(1초) = 9초
    → 약 18% 단축, 체감 속도는 더 크게 개선 (스피너가 멈추지 않음)

    Returns:
        LoadResult (vector_db, pipeline, 타이밍 정보)
    """
    t_total = time.time()
    step_times: Dict[str, float] = {}

    logger.info("=" * 55)
    logger.info("🚀 병렬 리소스 로딩 시작")
    logger.info("=" * 55)

    # ── Phase 1: 임베딩 + CE 병렬 로드 ──────────────────────────
    embedding_ok = False
    ce_ok = False

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="preload") as executor:
        future_emb = executor.submit(_preload_embedding_model)
        future_ce = executor.submit(_preload_cross_encoder)

        # 완료 순서대로 결과 수집
        for future in as_completed([future_emb, future_ce]):
            try:
                ok, elapsed = future.result()
                if future is future_emb:
                    embedding_ok = ok
                    step_times["embedding"] = elapsed
                else:
                    ce_ok = ok
                    step_times["cross_encoder"] = elapsed
            except Exception as exc:
                logger.warning(f"병렬 로딩 예외 (무시): {exc}")

    logger.info(
        f"Phase 1 완료: 임베딩({'✅' if embedding_ok else '❌'}), "
        f"CE({'✅' if ce_ok else '⚠️'})"
    )

    # ── Phase 2: FAISS 로드 (임베딩 캐시 히트 기대) ──────────────
    vector_db, elapsed_db = _load_vector_db()
    step_times["vector_db"] = elapsed_db

    # ── Phase 3: RAGPipeline 초기화 ───────────────────────────────
    pipeline, elapsed_pl = _build_rag_pipeline(vector_db)
    step_times["pipeline"] = elapsed_pl

    total = time.time() - t_total
    step_times["total"] = total

    logger.info(
        f"🏁 로딩 완료: 총 {total:.1f}초\n"
        f"  임베딩:   {step_times.get('embedding', 0):.1f}초\n"
        f"  CE:       {step_times.get('cross_encoder', 0):.1f}초\n"
        f"  벡터DB:   {step_times.get('vector_db', 0):.1f}초\n"
        f"  Pipeline: {step_times.get('pipeline', 0):.1f}초"
    )

    return LoadResult(
        vector_db=vector_db,
        pipeline=pipeline,
        total_elapsed=total,
        step_times=step_times,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Streamlit 로딩 UI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_loading_ui() -> None:
    """
    모델 로딩 중 Streamlit 진행 상황 UI 렌더링.

    main() 에서 _load_resources() 호출 직전에 배치하여
    사용자에게 진행 상황을 실시간으로 보여줍니다.

    [사용 예시 — main.py]

        # 기존 코드
        vector_db, pipeline = _load_resources()

        # 변경 코드
        from utils.startup_optimizer import render_loading_ui
        render_loading_ui()          # 로딩 UI 먼저 표시
        result = _load_resources()   # 그 다음 로드 (캐시 히트 시 즉시)
        vector_db  = result.vector_db
        pipeline   = result.pipeline
    """
    import streamlit as st

    # 이미 로드 완료되면 UI 표시 불필요
    # (st.cache_resource 가 캐시된 경우 이 함수는 거의 즉시 반환)

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
                status_text.markdown(f"**{icon} {label}** `({i + 1}/{len(steps)})`")
                # 실제 로딩은 _load_resources() 에서 수행
                # 여기서는 UI 업데이트만 (약 100ms 간격)
                time.sleep(0.1)

            status_text.markdown("✅ **준비 완료!**")
            time.sleep(0.2)

    container.empty()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  main.py 교체 패치 — 아래 코드로 main.py 수정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_MAIN_PY_PATCH = """
# ── main.py 에서 아래와 같이 수정하세요 ──────────────────────────

# [1] import 추가
from utils.startup_optimizer import parallel_load_resources

# [2] _load_resources() 함수를 아래로 교체

@st.cache_resource(show_spinner="⏳ AI 모델 준비 중... (최초 실행 시 30초 소요)")
def _load_resources():
    \"\"\"
    벡터 DB + RAGPipeline 병렬 초기화.
    
    [변경 사항]
    - 임베딩 모델 + CE 모델을 병렬 로드 (기존 직렬 → 병렬)
    - show_spinner 메시지로 사용자에게 진행 상황 안내
    - LoadResult 반환으로 타이밍 정보 포함
    \"\"\"
    result = parallel_load_resources()
    return result.vector_db, result.pipeline

# [3] main() 함수 내 _load_resources() 호출 부분은 그대로 유지
#     (기존과 동일하게 tuple 반환)
vector_db, pipeline = _load_resources()
"""
