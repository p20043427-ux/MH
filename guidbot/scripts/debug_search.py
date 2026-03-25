"""
scripts/debug_search.py ─ RAG 검색 품질 디버깅 도구

[이동 이유]
  이전: 프로젝트 루트에 debug_search.py 로 존재 → 앱 코드와 혼재
  변경: scripts/ 디렉토리로 이동 → 개발/운영 도구 명확 분리

[사용법]
  # 프로젝트 루트에서 실행
  python scripts/debug_search.py "연차휴가 신청 방법"
  python scripts/debug_search.py "당직 수당" --top-k 10 --top-n 5
  python scripts/debug_search.py --interactive    # 대화형 모드

[출력 정보]
  - 쿼리 재작성 결과 (QueryRewriter)
  - 하이브리드 검색 결과 (BM25 + FAISS)
  - CE 리랭킹 결과 (순위·점수·원문 발췌)
  - 파이프라인 단계별 소요 시간
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가 (scripts/ 하위에서 실행 시 필요)
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from config.settings import settings
from core.vector_store import VectorStoreManager
from core.rag_pipeline import get_pipeline
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


def _load_pipeline():
    """벡터 DB + RAGPipeline 로드 (디버그용)"""
    print(f"\n[1/2] 벡터 DB 로드 중: {settings.rag_db_path}")
    manager   = VectorStoreManager(
        db_path    = settings.rag_db_path,
        model_name = settings.embedding_model,
        cache_dir  = str(settings.local_work_dir),
    )
    vector_db = manager.load()

    if vector_db is None:
        print("❌ 벡터 DB 없음. build_db.py 를 먼저 실행하세요.")
        sys.exit(1)

    print(f"✅ 벡터 DB 로드 완료: {vector_db.index.ntotal:,}개 벡터")

    print("[2/2] RAGPipeline 초기화 중...")
    pipeline = get_pipeline(vector_db)
    print(f"✅ RAGPipeline 초기화 완료 (Hybrid={pipeline._retriever.is_hybrid})")
    return pipeline


def _run_search(pipeline, query: str, top_k: int, top_n: int) -> None:
    """단일 쿼리 검색 실행 + 결과 상세 출력"""
    print(f"\n{'='*60}")
    print(f"🔍 검색 쿼리: {query!r}")
    print(f"{'='*60}")

    start = time.time()
    result = pipeline.run(query, top_k=top_k, top_n=top_n, use_cache=False)
    elapsed = time.time() - start

    # 쿼리 재작성 결과
    if result.rewrite_result and result.rewrite_result.was_rewritten:
        print(f"\n📝 쿼리 재작성:")
        print(f"   원본:  {result.rewrite_result.original_query}")
        print(f"   변환:  {result.rewrite_result.search_query}")

    # 검색 결과
    print(f"\n📄 검색 결과: {len(result.ranked_docs)}건")
    print(f"⏱  소요 시간: {result.timing_summary}")

    for doc in result.ranked_docs:
        print(f"\n  [{doc.rank}위] {doc.source} p.{doc.page}")
        if doc.article:
            print(f"       조항: {doc.article}")
        print(f"       점수: {doc.score:.4f}")
        print(f"       발췌: {doc.chunk_text[:200]}...")

    # 컨텍스트 요약
    print(f"\n📋 LLM 컨텍스트: 약 {result.token_estimate}토큰 "
          f"({len(result.context)}자)")
    print(f"{'='*60}")
    print(f"✅ 완료 ({elapsed:.2f}초)")


def _interactive_mode(pipeline) -> None:
    """대화형 검색 모드"""
    print("\n대화형 검색 모드 시작 (종료: q 또는 Ctrl+C)")
    while True:
        try:
            query = input("\n검색어 입력: ").strip()
            if query.lower() in ("q", "quit", "exit"):
                break
            if not query:
                continue
            _run_search(pipeline, query, top_k=15, top_n=3)
        except KeyboardInterrupt:
            break
    print("\n종료")


def main() -> None:
    """디버그 스크립트 진입점"""
    parser = argparse.ArgumentParser(
        description="RAG 검색 품질 디버깅 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", nargs="?", help="검색 쿼리 (없으면 --interactive 모드)")
    parser.add_argument("--top-k", type=int, default=15, help="FAISS 1차 후보 수 (기본: 15)")
    parser.add_argument("--top-n", type=int, default=3,  help="CE 리랭킹 후 반환 수 (기본: 3)")
    parser.add_argument("--interactive", "-i", action="store_true", help="대화형 모드")
    args = parser.parse_args()

    pipeline = _load_pipeline()

    if args.interactive or not args.query:
        _interactive_mode(pipeline)
    else:
        _run_search(pipeline, args.query, top_k=args.top_k, top_n=args.top_n)


if __name__ == "__main__":
    main()
