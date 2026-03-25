"""
tests/rag_benchmark.py  ─  RAG 검색 모드 자동 벤치마크 (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[목적]
  3가지 검색 모드(Fast / Balanced / Deep)의 성능을
  동일한 테스트 세트로 자동 비교하고 리포트를 생성합니다.

[측정 지표]
  · Precision  : 검색된 문서 중 기대 문서(expected_document) 포함 비율
  · Recall     : 기대 문서가 검색 결과에 포함된 비율 (= Hit Rate)
  · Latency    : 검색 + 리랭킹 소요 시간 (ms)
  · Top-1 Hit  : 1위 문서가 기대 문서인 비율

[실행 방법]
  # 기본 실행 (3개 모드 모두 테스트)
  python tests/rag_benchmark.py

  # 특정 모드만 테스트
  python tests/rag_benchmark.py --mode fast
  python tests/rag_benchmark.py --mode balanced
  python tests/rag_benchmark.py --mode deep

  # 반복 횟수 지정 (평균 편차 안정화)
  python tests/rag_benchmark.py --repeat 3

  # 결과를 파일로 저장
  python tests/rag_benchmark.py --output logs/benchmark_result.json

[테스트 데이터 커스터마이즈]
  TEST_CASES 리스트를 수정하여 병원 실제 규정집에 맞게 조정하세요.
  expected_document 는 파일명 일부(소문자)로 매칭합니다.
  예: "취업규칙_2024.pdf" → expected_document="취업규칙"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean, median, stdev
from typing import Dict, List, Optional

# ── 프로젝트 루트를 PYTHONPATH 에 추가 ───────────────────────────────
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  테스트 케이스 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class TestCase:
    """
    단일 벤치마크 테스트 케이스.

    Attributes:
        question:          사용자 질문 (자연어)
        expected_document: 기대 문서명 일부 (소문자 포함 매칭)
                           "DB_SCHEMA" → DB 스키마 문서
                           "취업규칙"  → 취업규칙.pdf
        category:          분류 레이블 (리포트 그룹핑용)
        description:       테스트 설명 (옵션)
    """

    question: str
    expected_document: str  # 소문자 부분 매칭
    category: str = "일반"
    description: str = ""


# ── 병원 업무 도메인 테스트 케이스 (27개) ─────────────────────────────
TEST_CASES: List[TestCase] = [
    # ── 휴가·복무 규정 (가장 빈번한 질문) ──────────────────────────
    TestCase(
        question="출산전후휴가 기간은 얼마나 되나요?",
        expected_document="취업규칙",
        category="휴가",
        description="출산전후휴가 규정 조회",
    ),
    TestCase(
        question="연차 유급휴가 일수 기준이 어떻게 되나요?",
        expected_document="취업규칙",
        category="휴가",
    ),
    TestCase(
        question="육아휴직 신청 대상과 기간은?",
        expected_document="취업규칙",
        category="휴가",
    ),
    TestCase(
        question="병가 신청 절차를 알고 싶습니다",
        expected_document="취업규칙",
        category="휴가",
    ),
    TestCase(
        question="특별휴가 종류에는 무엇이 있나요?",
        expected_document="취업규칙",
        category="휴가",
    ),
    # ── 급여·수당 ────────────────────────────────────────────────
    TestCase(
        question="당직 수당 계산 기준은 어떻게 되나요?",
        expected_document="취업규칙",
        category="급여",
        description="당직수당 규정 조회",
    ),
    TestCase(
        question="초과근무 수당 지급 기준이 궁금합니다",
        expected_document="취업규칙",
        category="급여",
    ),
    TestCase(
        question="식대 지급 기준은 무엇인가요?",
        expected_document="취업규칙",
        category="급여",
    ),
    TestCase(
        question="급여 지급일이 언제인가요?",
        expected_document="취업규칙",
        category="급여",
    ),
    # ── 인사·복무 ────────────────────────────────────────────────
    TestCase(
        question="직원 복장 규정 및 용모 기준은?",
        expected_document="취업규칙",
        category="복무",
    ),
    TestCase(
        question="근무 시간 및 휴게시간 규정을 알려주세요",
        expected_document="취업규칙",
        category="복무",
    ),
    TestCase(
        question="퇴직금 지급 기준이 어떻게 되나요?",
        expected_document="취업규칙",
        category="인사",
    ),
    TestCase(
        question="징계 종류 및 절차는 무엇인가요?",
        expected_document="취업규칙",
        category="인사",
    ),
    # ── 교육·인증 ───────────────────────────────────────────────
    TestCase(
        question="법정의무교육 이수 방법과 시간",
        expected_document="교육",
        category="교육",
    ),
    TestCase(
        question="감염관리 교육 주기 및 이수 기준은?",
        expected_document="감염",
        category="교육",
    ),
    # ── DB 스키마 질의 ───────────────────────────────────────────
    TestCase(
        question="PATIENT_INFO 테이블 구조를 설명해주세요",
        expected_document="db_schema",
        category="DB",
        description="DB 스키마 조회 테스트",
    ),
    TestCase(
        question="환자 진료 기록 테이블 컬럼 목록은?",
        expected_document="db_schema",
        category="DB",
    ),
    TestCase(
        question="처방 관련 테이블 이름과 설명",
        expected_document="db_schema",
        category="DB",
    ),
    # ── 의료 원무 ───────────────────────────────────────────────
    TestCase(
        question="입원 신청 절차는 어떻게 되나요?",
        expected_document="원무",
        category="원무",
    ),
    TestCase(
        question="제증명 서류 발급 종류와 발급 방법",
        expected_document="원무",
        category="원무",
    ),
    TestCase(
        question="보호자 면회 시간 규정은?",
        expected_document="규정",
        category="원무",
    ),
    # ── 복잡한 조합 질문 (Deep 모드 우위 예상) ─────────────────
    TestCase(
        question="출산전후휴가 후 육아휴직 연계 신청 방법과 복직 절차",
        expected_document="취업규칙",
        category="복잡질문",
        description="여러 규정 조합 — Deep 모드 우위 예상",
    ),
    TestCase(
        question="야간 당직 수당과 초과근무수당을 동시에 받을 수 있나요",
        expected_document="취업규칙",
        category="복잡질문",
    ),
    TestCase(
        question="취업규칙 제26조 제3항 내용이 무엇인가요",
        expected_document="취업규칙",
        category="복잡질문",
        description="정확한 조항 번호 — BM25 강점 (Deep 우위)",
    ),
    # ── 키워드 검색 강점 테스트 (BM25 우위 예상) ───────────────
    TestCase(
        question="2024년 3월 개정 취업규칙 변경 내용",
        expected_document="취업규칙",
        category="키워드",
        description="날짜+개정 키워드 — BM25 우위",
    ),
    TestCase(
        question="VISIT_HISTORY 테이블 설명",
        expected_document="db_schema",
        category="키워드",
        description="정확한 테이블명 — BM25 우위",
    ),
    TestCase(
        question="EMR 처방 코드 조회 방법",
        expected_document="db_schema",
        category="키워드",
    ),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  결과 데이터클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class SingleResult:
    """단일 케이스 × 단일 실행 결과."""

    question: str
    mode: str
    expected_document: str
    category: str
    latency_ms: float
    retrieved_sources: List[str]  # 검색된 파일명 목록
    top1_source: str  # 1위 문서명
    hit: bool  # expected_document 가 검색 결과에 있는지
    top1_hit: bool  # expected_document 가 1위인지
    precision: float  # 검색 결과 중 관련 문서 비율 (0 or 1, 1건 기준)
    recall: float  # hit == top1_hit 단순화 (binary)
    error: str = ""  # 오류 메시지 (있을 때만)


@dataclass
class ModeStats:
    """단일 모드의 전체 통계."""

    mode: str
    n: int
    avg_latency_ms: float
    p50_latency_ms: float
    p90_latency_ms: float
    hit_rate: float  # Recall (expected 문서 포함 비율)
    top1_rate: float  # Top-1 Hit Rate
    avg_precision: float  # 검색 결과 중 관련 문서 비율
    error_rate: float
    by_category: Dict[str, Dict] = field(default_factory=dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  벤치마크 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class RAGBenchmark:
    """
    RAG 검색 모드 벤치마크 실행기.

    [실행 흐름]
    1. 벡터 DB + RAGPipeline 로드 (한 번만)
    2. TEST_CASES × modes × repeat 로 검색 실행
    3. 결과 집계 → ModeStats 생성
    4. 리포트 텍스트 + JSON 출력
    """

    def __init__(self) -> None:
        self.vector_db = None
        self.pipeline = None
        self._load_resources()

    def _load_resources(self) -> None:
        """벡터 DB + RAGPipeline 초기화."""
        print("=" * 60)
        print("🚀 벤치마크 리소스 로드 중...")
        print("=" * 60)

        try:
            from config.settings import settings
            from core.vector_store import VectorStoreManager
            from core.rag_pipeline import get_pipeline

            manager = VectorStoreManager(
                db_path=settings.rag_db_path,
                model_name=settings.embedding_model,
                cache_dir=str(settings.local_work_dir),
            )
            self.vector_db = manager.load()

            if self.vector_db is None:
                print("❌ 벡터 DB 없음 → build_db.py 를 먼저 실행하세요")
                sys.exit(1)

            print(f"✅ 벡터 DB: {self.vector_db.index.ntotal:,}개 벡터")

            self.pipeline = get_pipeline(self.vector_db)
            print("✅ RAGPipeline 준비 완료")

        except Exception as exc:
            print(f"❌ 리소스 로드 실패: {exc}")
            raise

    def _run_single(
        self,
        case: TestCase,
        mode: "SearchMode",
    ) -> SingleResult:
        """
        단일 케이스를 실행하고 SingleResult 를 반환합니다.

        [측정 범위]
        · 쿼리 정제 ~ CE 리랭킹까지 (LLM 제외)
        · LLM 호출은 벤치마크에서 제외 (API 비용 + 속도 변동 큼)
        """
        from core.search_modes import get_config

        cfg = get_config(mode)

        t0 = time.time()
        error_msg = ""
        ranked_docs = []

        try:
            result = self.pipeline.run_with_mode(case.question, cfg)
            ranked_docs = result.ranked_docs
        except Exception as exc:
            error_msg = str(exc)

        latency_ms = (time.time() - t0) * 1000

        # 검색 결과에서 소스 추출
        sources = [getattr(d, "source", "").lower() for d in ranked_docs]
        top1_source = sources[0] if sources else ""
        expected = case.expected_document.lower()

        # Hit 판정: 검색 결과 중 expected_document 포함 여부
        hit = any(expected in s for s in sources)
        top1_hit = expected in top1_source

        # Precision: 검색 문서 중 관련 문서 수 / 전체 검색 문서 수
        if sources:
            relevant_count = sum(1 for s in sources if expected in s)
            precision = relevant_count / len(sources)
        else:
            precision = 0.0

        return SingleResult(
            question=case.question,
            mode=mode.value,
            expected_document=expected,
            category=case.category,
            latency_ms=latency_ms,
            retrieved_sources=sources,
            top1_source=top1_source,
            hit=hit,
            top1_hit=top1_hit,
            precision=precision,
            recall=1.0 if hit else 0.0,
            error=error_msg,
        )

    def run(
        self,
        modes: Optional[List[str]] = None,
        repeat: int = 1,
        verbose: bool = True,
    ) -> Dict[str, ModeStats]:
        """
        전체 벤치마크를 실행하고 모드별 통계를 반환합니다.

        Args:
            modes:   테스트할 모드 목록 (None=전체)
            repeat:  각 케이스 반복 횟수 (평균 안정화용)
            verbose: 진행 상황 출력 여부

        Returns:
            {mode_value: ModeStats} 딕셔너리
        """
        from core.search_modes import SearchMode, MODE_ORDER

        # 테스트할 모드 결정
        if modes:
            target_modes = [SearchMode(m) for m in modes]
        else:
            target_modes = MODE_ORDER

        all_results: Dict[str, List[SingleResult]] = {m.value: [] for m in target_modes}
        total = len(TEST_CASES) * len(target_modes) * repeat

        print(
            f"\n📋 벤치마크 시작: {len(TEST_CASES)}케이스 × "
            f"{len(target_modes)}모드 × {repeat}회 = 총 {total}회\n"
        )

        done = 0
        for mode in target_modes:
            mode_name = mode.value.upper()
            print(f"\n{'─' * 50}")
            print(f"  🔍 [{mode_name}] 모드 테스트 시작")
            print(f"{'─' * 50}")

            for case in TEST_CASES:
                for r in range(repeat):
                    result = self._run_single(case, mode)
                    all_results[mode.value].append(result)
                    done += 1

                    if verbose:
                        hit_icon = "✅" if result.hit else "❌"
                        top1_icon = "⭐" if result.top1_hit else "  "
                        print(
                            f"  {hit_icon}{top1_icon} [{result.category:5}] "
                            f"{case.question[:30]:30} "
                            f"→ {result.latency_ms:6.0f}ms "
                            f"(P={result.precision:.2f})"
                            + (f"  ⚠️ {result.error[:30]}" if result.error else "")
                        )

        # ── 통계 집계 ──────────────────────────────────────────────
        stats: Dict[str, ModeStats] = {}
        for mode_val, results in all_results.items():
            if not results:
                continue
            n = len(results)
            latencies = [r.latency_ms for r in results]
            sorted_lat = sorted(latencies)

            # 카테고리별 집계
            by_cat: Dict[str, Dict] = {}
            for r in results:
                if r.category not in by_cat:
                    by_cat[r.category] = {
                        "count": 0,
                        "hit": 0,
                        "top1": 0,
                        "latencies": [],
                    }
                by_cat[r.category]["count"] += 1
                by_cat[r.category]["hit"] += int(r.hit)
                by_cat[r.category]["top1"] += int(r.top1_hit)
                by_cat[r.category]["latencies"].append(r.latency_ms)

            cat_stats = {}
            for cat, d in by_cat.items():
                cat_stats[cat] = {
                    "count": d["count"],
                    "hit_rate": round(d["hit"] / d["count"], 3),
                    "top1_rate": round(d["top1"] / d["count"], 3),
                    "avg_latency_ms": round(mean(d["latencies"]), 1),
                }

            stats[mode_val] = ModeStats(
                mode=mode_val,
                n=n,
                avg_latency_ms=round(mean(latencies), 1),
                p50_latency_ms=round(sorted_lat[int(n * 0.50)], 1),
                p90_latency_ms=round(sorted_lat[min(int(n * 0.90), n - 1)], 1),
                hit_rate=round(mean(r.recall for r in results), 3),
                top1_rate=round(mean(r.top1_hit for r in results), 3),
                avg_precision=round(mean(r.precision for r in results), 3),
                error_rate=round(sum(1 for r in results if r.error) / n, 4),
                by_category=cat_stats,
            )

        return stats

    # ── 리포트 출력 ─────────────────────────────────────────────────

    @staticmethod
    def print_report(stats: Dict[str, ModeStats]) -> None:
        """
        벤치마크 결과를 콘솔에 출력합니다.
        """
        from core.search_modes import MODE_CONFIGS, SearchMode

        print("\n")
        print("=" * 62)
        print("   📊 RAG 검색 모드 성능 비교 리포트")
        print("=" * 62)

        mode_order = ["fast", "balanced", "deep"]
        icons = {"fast": "⚡", "balanced": "⚖️", "deep": "🧠"}

        for mv in mode_order:
            s = stats.get(mv)
            if s is None:
                continue

            icon = icons.get(mv, "?")
            print(f"\n{icon} {mv.upper()} Mode  (n={s.n}건)")
            print(f"  {'─' * 46}")
            print(f"  평균 응답 속도  : {s.avg_latency_ms / 1000:.2f}초")
            print(f"  중앙값 (P50)    : {s.p50_latency_ms / 1000:.2f}초")
            print(f"  P90 응답 속도   : {s.p90_latency_ms / 1000:.2f}초")
            print(f"  Hit Rate (Recall): {s.hit_rate * 100:.1f}%")
            print(f"  Top-1 정확도    : {s.top1_rate * 100:.1f}%")
            print(f"  Precision       : {s.avg_precision * 100:.1f}%")
            print(f"  오류율          : {s.error_rate * 100:.1f}%")

            if s.by_category:
                print(f"\n  카테고리별 Hit Rate:")
                for cat, cs in sorted(s.by_category.items()):
                    bar_len = int(cs["hit_rate"] * 20)
                    bar = "█" * bar_len + "░" * (20 - bar_len)
                    print(
                        f"    {cat:8} [{bar}] "
                        f"{cs['hit_rate'] * 100:.0f}% "
                        f"(n={cs['count']})"
                    )

        # ── 모드 간 비교 요약 ──────────────────────────────────────
        print(f"\n{'─' * 62}")
        print("  📌 모드 선택 권장 가이드")
        print(f"{'─' * 62}")

        available = [mv for mv in mode_order if mv in stats]
        if len(available) >= 2:
            # 가장 빠른 모드
            fastest = min(available, key=lambda m: stats[m].avg_latency_ms)
            # 가장 정확한 모드
            best_acc = max(available, key=lambda m: stats[m].hit_rate)

            print(
                f"  ⚡ 속도 최우선 → {fastest.upper()} "
                f"({stats[fastest].avg_latency_ms / 1000:.1f}초)"
            )
            print(
                f"  🎯 정확도 최우선 → {best_acc.upper()} "
                f"(Hit {stats[best_acc].hit_rate * 100:.0f}%)"
            )

        print("=" * 62)

    @staticmethod
    def save_json(
        stats: Dict[str, ModeStats],
        output: str,
        results: Optional[List[SingleResult]] = None,
    ) -> None:
        """벤치마크 결과를 JSON 파일로 저장합니다."""
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "summary": {mv: asdict(s) for mv, s in stats.items()},
        }

        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n💾 결과 저장: {output_path}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CLI 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAG 검색 모드 자동 벤치마크",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python tests/rag_benchmark.py                          # 3개 모드 전체 테스트
  python tests/rag_benchmark.py --mode fast balanced    # 2개 모드만
  python tests/rag_benchmark.py --repeat 3              # 3회 반복 평균
  python tests/rag_benchmark.py --output logs/report.json
        """,
    )
    parser.add_argument(
        "--mode",
        nargs="+",
        choices=["fast", "balanced", "deep"],
        default=None,
        help="테스트할 모드 (기본: 전체)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="케이스당 반복 횟수 (기본: 1)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="JSON 결과 저장 경로 (기본: 없음)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="개별 케이스 출력 생략",
    )

    args = parser.parse_args()

    benchmark = RAGBenchmark()
    stats = benchmark.run(
        modes=args.mode,
        repeat=args.repeat,
        verbose=not args.quiet,
    )
    RAGBenchmark.print_report(stats)

    if args.output:
        RAGBenchmark.save_json(stats, args.output)
    else:
        # 기본: logs/ 디렉토리에 날짜+시간 파일명으로 저장
        default_out = Path("logs") / f"benchmark_{time.strftime('%Y%m%d_%H%M%S')}.json"
        RAGBenchmark.save_json(stats, str(default_out))


if __name__ == "__main__":
    main()
