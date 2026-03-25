"""
warmup.py ─ AI 모델 사전 캐시 워밍업 스크립트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[목적]
  streamlit run main.py 실행 전에 이 스크립트를 먼저 실행하면
  무거운 AI 모델이 디스크 캐시에 올라옵니다.
  이후 Streamlit 시작 시 캐시 히트로 로딩이 크게 단축됩니다.

  최초 실행: ~30초 (350MB 모델 다운로드 + 캐시)
  이후 실행:  ~3초 (캐시 히트)

[실행 방법]
  # 최초 설치 후 또는 모델 업데이트 후 1회 실행
  python warmup.py

  # start.bat 에서 자동 실행됨 (매번 실행, 캐시 히트 시 3초 이내)

[실제 속도 개선 효과]
  warmup 없이 Streamlit 시작: 15~20초 (사용자가 빈 화면 대기)
  warmup 후  Streamlit 시작:  3~5초  (캐시에서 즉시 로드)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).parent))


# 자주 쓰는 쿼리 사전 임베딩 목록 (앱 시작 시 미리 캐시)
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

    [v2.0 개선]
    · _WARMUP_QUERIES 사전 임베딩 → search_engine._EMBED_CACHE 채움
    · 실제 질문과 유사한 쿼리들을 미리 임베딩해두면
      비슷한 질문 입력 시 캐시 히트율 향상
    · 전체 예열 ~10초 → 사용자가 첫 질문 전에 완료

    [효과]
    첫 질문: 캐시 미스 → ~8~10초 (불가피)
    이후 비슷한 질문: 캐시 히트 → ~0.1초
    """
    print("  임베딩 모델 로딩 중 (ko-sroberta-multitask)...")
    t0 = time.time()
    try:
        from core.embeddings import get_embeddings_auto

        emb = get_embeddings_auto()

        # 기본 JIT 예열
        _ = emb.embed_query("병원 규정 테스트")
        elapsed = time.time() - t0
        print(f"  ✅ 임베딩 모델 로드 완료 ({elapsed:.1f}초)")

        # 자주 쓰는 쿼리 사전 임베딩 → search_engine 캐시 주입
        print(f"  사전 임베딩 중 ({len(_WARMUP_QUERIES)}개 쿼리)...")
        t1 = time.time()
        try:
            from core.search_engine import _get_query_embedding, _EMBED_CACHE

            # search_engine 캐시에 직접 저장
            for q in _WARMUP_QUERIES:
                import hashlib

                _k = hashlib.md5(q.strip().lower().encode()).hexdigest()[:12]
                if _k not in _EMBED_CACHE:
                    _EMBED_CACHE[_k] = emb.embed_query(q)
            print(
                f"  ✅ 사전 임베딩 완료: {len(_WARMUP_QUERIES)}개 ({time.time() - t1:.1f}초)"
            )
        except Exception as _ce:
            # search_engine 캐시 주입 실패해도 모델 자체는 OK
            print(f"  ⚠️  캐시 주입 건너뜀: {_ce}")

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
            # JIT 예열
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
            print(
                f"  ✅ 벡터 DB 준비 완료 ({db.index.ntotal:,}개 벡터, {elapsed:.1f}초)"
            )
        else:
            print("  ❌ 벡터 DB 로드 실패")
        return db is not None
    except Exception as exc:
        print(f"  ❌ 벡터 DB 오류: {exc}")
        return False


def warmup_bm25_index() -> bool:
    """BM25 인덱스 사전 구축 — 첫 번째 검색 14초 제거."""
    print("  📊 BM25 인덱스 구축 중 (24,000개 문서 토크나이징)...")
    t0 = time.time()
    try:
        from config.settings import settings
        from core.vector_store import VectorStoreManager
        from core.search_engine import _get_retriever

        manager = VectorStoreManager(
            db_path=settings.rag_db_path,
            model_name=settings.embedding_model,
            cache_dir=str(settings.local_work_dir),
        )
        db = manager.load()
        if db is None:
            print("  ⚠️  벡터 DB 없음 → BM25 건너뜀")
            return False

        retriever = _get_retriever(db)
        retriever._ensure_bm25()

        elapsed = time.time() - t0
        count = len(retriever._bm25.documents) if retriever._bm25 else 0
        print(f"  ✅ BM25 인덱스 구축 완료: {count:,}개 문서 ({elapsed:.1f}초)")
        return True
    except Exception as exc:
        print(f"  ⚠️  BM25 건너뜀: {exc}")
        return True  # 필수 아님


def check_env() -> bool:
    """.env 파일 및 필수 설정 확인."""
    print("  ⚙️  환경 설정 확인 중...")
    try:
        from config.settings import settings

        warnings = []
        if not settings.get_google_api_key():
            warnings.append("GOOGLE_API_KEY 미설정 — Gemini 답변 불가")

        # admin_password 기본값 경고
        try:
            pw = settings.admin_password.get_secret_value()
            if pw in ("moonhwa", "password", "1234", "admin"):
                warnings.append(f"ADMIN_PASSWORD='{pw}' — 취약한 기본값! 변경 필요")
        except Exception:
            pass

        if warnings:
            for w in warnings:
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
    print("  🏥 좋은문화병원 AI 가이드봇 — 사전 워밍업")
    print("=" * 52)
    print()

    t_start = time.time()
    results = {}

    # 1. 환경 설정 확인
    results["env"] = check_env()
    print()

    # 2. 임베딩 모델 (가장 무거움, 먼저 시작)
    results["embedding"] = warmup_embedding_model()
    print()

    # 3. CE 모델
    results["ce"] = warmup_cross_encoder()
    print()

    # 4. 벡터 DB (임베딩 캐시 히트로 빠름)
    results["db"] = warmup_vector_db()
    print()

    # 5. BM25 인덱스 구축 (첫 검색 14초 제거 핵심)
    results["bm25"] = warmup_bm25_index()
    print()

    # 결과 요약
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
        print("  재검색(캐시):   ~0.1초 (캐시 히트)")
        if bm25_ok:
            print("  BM25:          준비 완료 (백그라운드 불필요)")
        print()
        print("  streamlit run main.py --server.port 8502")
    else:
        print(f"  ⚠️  워밍업 일부 실패 (총 {total:.1f}초)")
        print("  Streamlit 은 시작할 수 있으나 첫 검색이 느릴 수 있습니다.")
    print("=" * 52)
    print()

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
