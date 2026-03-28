"""
warmup.py  ─  AI 모델 사전 캐시 워밍업 스크립트 v2.2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v2.2 변경 — search_engine private 접근 완전 제거]

  ❌ 제거:
    from core.search_engine import _get_query_embedding, _EMBED_CACHE
    from core.search_engine import _get_retriever

  ✅ 대체:
    emb.embed_query(q)         → rag_pipeline._EMBED_CACHE 에 자동 저장됨
    pipeline.warmup_retriever() → RAGPipeline public API
    pipeline.warmup_ce()        → RAGPipeline public API

  [왜 자동으로 캐시에 저장되나]
    rag_pipeline._get_query_embedding() 은 모듈 레벨 _EMBED_CACHE 를 사용.
    warmup 에서 emb.embed_query(q) 를 직접 호출해도 되지만,
    pipeline.warmup_retriever() 가 내부적으로 임베딩을 포함하므로
    별도 사전 임베딩 없이도 파이프라인 초기화만으로 캐시 준비 완료.

[목적]
  streamlit run main.py 전에 실행 → 무거운 AI 모델 캐시 선점.
  최초 실행: ~30초 (모델 다운로드 + 캐시)
  이후 실행:  ~3초  (캐시 히트)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 자주 쓰는 쿼리 사전 임베딩 목록
_WARMUP_QUERIES = [
    "연차휴가 신청",
    "당직 수당",
    "출산휴가",
    "징계 절차",
    "병원 규정",
    "취업규칙",
    "급여 계산",
    "야간 근무",
]


def warmup_embedding_model() -> bool:
    """
    임베딩 모델 워밍업 + 자주 쓰는 쿼리 사전 임베딩.

    [v2.2 변경]
    · search_engine._EMBED_CACHE 직접 접근 제거
    · emb.embed_query() 만 호출 → rag_pipeline 캐시에 자동 저장
    · 별도 hashlib 처리 불필요 (캐싱은 rag_pipeline 내부에서 처리)
    """
    print("  임베딩 모델 로딩 중 (ko-sroberta-multitask)...")
    t0 = time.time()
    try:
        from core.embeddings import get_embeddings_auto

        emb = get_embeddings_auto()

        # JIT 예열 (첫 추론 지연 제거)
        _ = emb.embed_query("병원 규정 테스트")
        elapsed = time.time() - t0
        print(f"  ✅ 임베딩 모델 로드 완료 ({elapsed:.1f}초)")

        # 자주 쓰는 쿼리 사전 임베딩
        print(f"  사전 임베딩 중 ({len(_WARMUP_QUERIES)}개 쿼리)...")
        t1 = time.time()
        try:
            # [v2.2] search_engine private 접근 제거
            # emb.embed_query() 호출 시 rag_pipeline._EMBED_CACHE 에 자동 저장됨
            for q in _WARMUP_QUERIES:
                emb.embed_query(q)
            print(
                f"  ✅ 사전 임베딩 완료: {len(_WARMUP_QUERIES)}개 ({time.time() - t1:.1f}초)"
            )
        except Exception as _ce:
            print(f"  ⚠️  사전 임베딩 건너뜀: {_ce}")

        return True
    except Exception as exc:
        print(f"  ❌ 임베딩 모델 실패: {exc}")
        return False


def warmup_cross_encoder() -> bool:
    """Cross-Encoder 모델 워밍업."""
    print("  🔍 리랭킹 모델 로딩 중 (MiniLM-L6)...")
    t0 = time.time()
    try:
        from core.retriever import _load_cross_encoder

        ce = _load_cross_encoder()
        if ce:
            ce.predict(
                [("연차휴가 신청", "제26조 연차휴가")],
                num_workers=0,
                show_progress_bar=False,
            )
            elapsed = time.time() - t0
            print(f"  ✅ 리랭킹 모델 준비 완료 ({elapsed:.1f}초)")
        else:
            print("  ⚠️  리랭킹 모델 없음 (FAISS 단독 모드)")
        return True
    except Exception as exc:
        print(f"  ⚠️  리랭킹 모델 건너뜀: {exc}")
        return True  # 필수 아님


def warmup_vector_db() -> bool:
    """벡터 DB 로드 확인."""
    print("  📚 벡터 DB 확인 중...")
    t0 = time.time()
    try:
        from config.settings import settings

        index_path = settings.rag_db_path / "index.faiss"
        if not index_path.exists():
            print(f"  ⚠️  벡터 DB 없음: {index_path}")
            print("       → python build_db.py 를 실행하세요")
            return False

        from core.vector_store import VectorStoreManager

        manager = VectorStoreManager(
            db_path=settings.rag_db_path,
            model_name=settings.embedding_model,
            cache_dir=str(settings.local_work_dir),
        )
        db = manager.load()
        elapsed = time.time() - t0

        if db:
            print(f"  ✅ 벡터 DB 준비 완료 ({db.index.ntotal:,}개 벡터, {elapsed:.1f}초)")
        else:
            print("  ❌ 벡터 DB 로드 실패")
        return db is not None
    except Exception as exc:
        print(f"  ❌ 벡터 DB 오류: {exc}")
        return False


def warmup_bm25_index() -> bool:
    """
    BM25 인덱스 사전 구축 — 첫 번째 검색 14초 제거.

    [v2.2 변경]
    · from core.search_engine import _get_retriever  ❌ (private, 삭제됨)
    · pipeline.warmup_retriever()                    ✅ (RAGPipeline public API)
    """
    print("  📊 BM25 인덱스 구축 중 (문서 토크나이징)...")
    t0 = time.time()
    try:
        from config.settings import settings
        from core.vector_store import VectorStoreManager
        from core.rag_pipeline import get_pipeline  # [v2.2] public API

        manager = VectorStoreManager(
            db_path=settings.rag_db_path,
            model_name=settings.embedding_model,
            cache_dir=str(settings.local_work_dir),
        )
        db = manager.load()
        if db is None:
            print("  ⚠️  벡터 DB 없음 → BM25 건너뜀")
            return False

        # [v2.2] pipeline public API 사용 (search_engine private 제거)
        pipeline = get_pipeline(db)
        pipeline.warmup_retriever()

        elapsed = time.time() - t0
        print(f"  ✅ BM25 인덱스 구축 완료 ({elapsed:.1f}초)")
        return True
    except Exception as exc:
        print(f"  ⚠️  BM25 건너뜀: {exc}")
        return True  # 필수 아님


def check_env() -> bool:
    """.env 파일 및 필수 설정 확인."""
    print("  ⚙️  환경 설정 확인 중...")
    try:
        from config.settings import settings

        warnings_list = []
        if not settings.get_google_api_key():
            warnings_list.append("GOOGLE_API_KEY 미설정 — Gemini 답변 불가")

        try:
            pw = settings.admin_password.get_secret_value()
            if pw in ("moonhwa", "password", "1234", "admin"):
                warnings_list.append(f"ADMIN_PASSWORD='{pw}' — 취약한 기본값! 변경 필요")
        except Exception:
            pass

        if warnings_list:
            for w in warnings_list:
                print(f"  ⚠️  {w}")
        else:
            print("  ✅ 환경 설정 정상")
        return True
    except Exception as exc:
        print(f"  ❌ 설정 로드 실패: {exc}")
        return False


def main() -> int:
    """워밍업 메인 함수."""
    print()
    print("=" * 52)
    print("  🏥 좋은문화병원 AI 가이드봇 — 사전 워밍업 v2.2")
    print("=" * 52)
    print()

    t_start = time.time()
    results = {}

    results["env"] = check_env()
    print()
    results["embedding"] = warmup_embedding_model()
    print()
    results["ce"] = warmup_cross_encoder()
    print()
    results["db"] = warmup_vector_db()
    print()
    results["bm25"] = warmup_bm25_index()
    print()

    total = time.time() - t_start
    all_ok = results.get("embedding") and results.get("db")
    bm25_ok = results.get("bm25", False)

    print("=" * 52)
    if all_ok:
        print(f"  ✅ 워밍업 완료! (총 {total:.1f}초)")
        print()
        print("  [속도 기대치]")
        print("  첫 번째 질문:  ~8~10초 (임베딩 추론)")
        print("  두 번째 동일:   ~0.1초 (캐시 히트)")
        if bm25_ok:
            print("  BM25:          준비 완료")
        print()
        print("  streamlit run main.py --server.port 8502")
    else:
        print(f"  ⚠️  워밍업 일부 실패 (총 {total:.1f}초)")
    print("=" * 52)
    print()

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())