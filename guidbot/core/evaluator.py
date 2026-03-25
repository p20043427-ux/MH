"""
core/evaluator.py  ─  검색 모드 성능 평가 시스템 (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[역할]
  각 검색 요청의 품질·성능 데이터를 JSON 파일에 누적 기록하고,
  Precision / Recall / Latency 지표를 자동 계산합니다.

[저장 위치]
  logs/search_evaluation.json

[데이터 스키마 (EvaluationRecord)]
  {
    "id":            "uuid4",           // 레코드 고유 ID
    "timestamp":     "ISO-8601",        // 기록 시각
    "question":      "연차휴가 신청 방법", // 사용자 원본 질문
    "search_mode":   "balanced",        // fast / balanced / deep
    "search_query":  "연차 휴가 신청",   // 실제 검색된 쿼리 (rewrite 후)
    "retrieved_docs": [                 // 검색된 문서 목록
      {"source": "취업규칙.pdf", "page": "12", "score": 0.92}
    ],
    "llm_response":  "연차휴가는 ...",   // LLM 생성 답변
    "latency_ms":    2340.5,            // 전체 응답 시간 (ms)
    "t_search_ms":   45.2,              // 검색 단계 시간
    "t_rerank_ms":   1820.1,            // 리랭킹 단계 시간
    "satisfaction":  4,                 // 사용자 만족도 1~5 (null=미응답)
    "is_relevant":   null               // 수동 평가 여부 (관리자용)
  }

[품질 지표 정의]
  · Precision = 검색된 문서 중 실제 관련 문서 수 / 전체 검색 문서 수
    → CE score >= threshold (0.1) 인 문서를 "관련 있음" 으로 간주
    → 정확한 평가는 expected_document 비교 (benchmark 전용)

  · Recall = 정답 문서가 검색 결과에 포함된 비율
    → benchmark 테스트 (expected_document 있는 경우)에서만 계산 가능

  · Latency = 전체 응답 시간 평균 (ms)

[설계 결정]
  · 파일 잠금: threading.Lock 으로 동시 쓰기 방지 (Streamlit 멀티스레드)
  · 쓰기 원자성: 임시파일 → rename 패턴으로 데이터 손상 방지
  · 메모리 상한: 최근 10,000건만 메모리 보관 (오래된 건은 파일만)
  · None 직렬화: JSON 표준에서 null 로 변환
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.search_modes import SearchMode
from utils.logger import get_logger

# 순환 import 방지를 위해 settings 를 함수 안에서 lazy import
logger = get_logger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  상수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# CE score 기준: 이 값 이상이면 "관련 있음" (Precision 계산용)
RELEVANCE_THRESHOLD: float = 0.1

# 메모리 보관 최대 레코드 수
MAX_MEMORY_RECORDS: int = 10_000

# 파일명
EVAL_FILE_NAME: str = "search_evaluation.json"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. 평가 레코드 데이터클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class RetrievedDocInfo:
    """검색된 단일 문서 정보 (직렬화용)."""

    source: str  # 파일명 (예: 취업규칙.pdf, DB_SCHEMA)
    page: str  # 페이지 번호 또는 빈 문자열
    score: float  # CE 점수 또는 FAISS 유사도
    rank: int  # 검색 순위 (1위부터)
    article: str = ""  # 조항 번호 (있을 때만)


@dataclass
class EvaluationRecord:
    """
    단일 검색 요청의 전체 평가 레코드.

    [주의] dataclass asdict() 로 JSON 직렬화 시
    datetime 은 isoformat() 으로 수동 변환 필요.
    """

    id: str  # uuid4 고유 ID
    timestamp: str  # ISO-8601 문자열
    question: str  # 사용자 원본 질문
    search_mode: str  # SearchMode.value (str)
    search_query: str  # 실제 검색 쿼리 (rewrite 후)
    retrieved_docs: List[RetrievedDocInfo] = field(default_factory=list)
    llm_response: str = ""  # LLM 답변 (스트리밍 완료 후 저장)
    latency_ms: float = 0.0  # 전체 응답 시간 (ms)
    t_search_ms: float = 0.0  # 검색 단계
    t_rerank_ms: float = 0.0  # 리랭킹 단계
    satisfaction: Optional[int] = None  # 사용자 만족도 1~5
    is_relevant: Optional[bool] = None  # 수동 평가 (관리자)

    # ── Precision 자동 계산 프로퍼티 ──────────────────────────
    @property
    def auto_precision(self) -> Optional[float]:
        """
        CE score 기반 자동 Precision 계산.

        [계산 방식]
        · CE score >= RELEVANCE_THRESHOLD 인 문서를 "관련 있음"으로 간주
        · Fast 모드(CE 없음)는 FAISS score 음수변환 점수 사용

        [한계]
        · 실제 정답 문서를 모르므로 간접 지표임
        · 정확한 평가는 benchmark.py 에서 expected_document 비교 필요
        """
        if not self.retrieved_docs:
            return None
        relevant = sum(1 for d in self.retrieved_docs if d.score >= RELEVANCE_THRESHOLD)
        return relevant / len(self.retrieved_docs)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. 평가 로거
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class EvaluationLogger:
    """
    검색 평가 데이터를 JSON 파일에 기록하고 분석하는 클래스.

    [스레드 안전성]
    Streamlit 은 요청마다 별도 스레드를 사용합니다.
    _lock 으로 파일 읽기/쓰기를 보호합니다.

    [싱글톤 패턴]
    get_evaluator() 함수를 통해 프로세스당 1개 인스턴스 공유.
    """

    def __init__(self, log_dir: Path) -> None:
        """
        Args:
            log_dir: 로그 파일 저장 디렉토리 (settings.log_dir)
        """
        self._log_dir = log_dir
        self._eval_path = log_dir / EVAL_FILE_NAME
        self._lock = threading.Lock()
        self._records: List[EvaluationRecord] = []  # 메모리 캐시

        # 기존 로그 로드
        self._load_from_file()
        logger.info(
            f"EvaluationLogger 초기화 완료 "
            f"(기존 {len(self._records):,}건 로드, "
            f"경로={self._eval_path})"
        )

    # ── 레코드 생성 ─────────────────────────────────────────────

    def create_record(
        self,
        question: str,
        mode: SearchMode,
        search_query: str,
    ) -> EvaluationRecord:
        """
        새 평가 레코드를 생성합니다 (저장은 update_record 로).

        [두 단계 기록 설계 이유]
        · 검색 완료 시점(create) + LLM 완료 시점(update) 이 다름
        · 스트리밍 답변 생성 중에는 레코드가 미완성 상태

        Args:
            question:    사용자 원본 질문
            mode:        사용된 SearchMode
            search_query: 실제 검색 쿼리 (rewrite 후)

        Returns:
            EvaluationRecord (id 할당됨, 저장 전)
        """
        return EvaluationRecord(
            id=str(uuid.uuid4()),
            timestamp=datetime.now().isoformat(),
            question=question,
            search_mode=mode.value,
            search_query=search_query,
        )

    def update_record(
        self,
        record: EvaluationRecord,
        docs: list,  # List[RankedDocument]
        response: str,
        latency_ms: float,
        t_search_ms: float = 0.0,
        t_rerank_ms: float = 0.0,
    ) -> None:
        """
        RAG 파이프라인 완료 후 레코드를 업데이트하고 저장합니다.

        Args:
            record:      create_record() 로 생성한 레코드
            docs:        List[RankedDocument] — 검색 결과
            response:    LLM 생성 답변 전문
            latency_ms:  전체 응답 시간 (ms)
            t_search_ms: 검색 단계 시간
            t_rerank_ms: 리랭킹 단계 시간
        """
        # docs (RankedDocument) → RetrievedDocInfo 변환
        record.retrieved_docs = [
            RetrievedDocInfo(
                source=d.source,
                page=d.page,
                score=round(float(d.score), 4),
                rank=d.rank,
                article=d.article,
            )
            for d in docs
        ]
        record.llm_response = response
        record.latency_ms = round(latency_ms, 2)
        record.t_search_ms = round(t_search_ms, 2)
        record.t_rerank_ms = round(t_rerank_ms, 2)

        self._save_record(record)

    def update_satisfaction(
        self,
        record_id: str,
        satisfaction: int,
    ) -> bool:
        """
        사용자 만족도(1~5)를 기록합니다.

        [호출 시점]
        Streamlit UI 의 만족도 버튼 클릭 시 호출.

        Args:
            record_id:    EvaluationRecord.id
            satisfaction: 1(매우 불만족) ~ 5(매우 만족)

        Returns:
            True=업데이트 성공, False=레코드 없음
        """
        if not (1 <= satisfaction <= 5):
            logger.warning(f"잘못된 만족도 값: {satisfaction} (1~5 범위)")
            return False

        with self._lock:
            for r in reversed(self._records):  # 최근 레코드 우선 탐색
                if r.id == record_id:
                    r.satisfaction = satisfaction
                    self._flush_to_file()
                    logger.info(
                        f"만족도 기록: id={record_id[:8]}... score={satisfaction}/5"
                    )
                    return True
        logger.warning(f"레코드 없음: id={record_id}")
        return False

    # ── 통계 분석 ─────────────────────────────────────────────

    def get_mode_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        모드별 성능 통계를 계산합니다.

        [반환 구조]
        {
          "fast": {
            "count":           100,      // 총 질문 수
            "avg_latency_ms":  1200.5,   // 평균 응답 시간 (ms)
            "avg_precision":   0.72,     // 평균 Precision
            "avg_satisfaction": 3.8,    // 평균 만족도 (응답한 것만)
            "error_rate":      0.02,     // 응답 없음(0ms) 비율
          },
          ...
        }
        """
        stats: Dict[str, Dict[str, Any]] = {
            m.value: {
                "count": 0,
                "avg_latency_ms": 0.0,
                "avg_precision": 0.0,
                "avg_satisfaction": 0.0,
                "error_rate": 0.0,
                "p50_latency_ms": 0.0,  # 중앙값
                "p90_latency_ms": 0.0,  # 90th 퍼센타일
            }
            for m in SearchMode
        }

        with self._lock:
            records_copy = list(self._records)

        # 모드별 그룹화
        groups: Dict[str, List[EvaluationRecord]] = {m.value: [] for m in SearchMode}
        for r in records_copy:
            if r.search_mode in groups:
                groups[r.search_mode].append(r)

        for mode_val, recs in groups.items():
            if not recs:
                continue
            n = len(recs)

            # 응답 시간
            latencies = [r.latency_ms for r in recs]
            sorted_lat = sorted(latencies)
            avg_lat = sum(latencies) / n
            p50 = sorted_lat[int(n * 0.50)]
            p90 = sorted_lat[min(int(n * 0.90), n - 1)]

            # Precision (auto)
            precisions = [
                r.auto_precision for r in recs if r.auto_precision is not None
            ]
            avg_prec = sum(precisions) / len(precisions) if precisions else 0.0

            # 만족도
            sats = [r.satisfaction for r in recs if r.satisfaction is not None]
            avg_sat = sum(sats) / len(sats) if sats else 0.0

            # 오류율 (latency=0 인 건 오류로 간주)
            err_count = sum(1 for r in recs if r.latency_ms == 0)
            error_rate = err_count / n

            stats[mode_val] = {
                "count": n,
                "avg_latency_ms": round(avg_lat, 1),
                "avg_precision": round(avg_prec, 3),
                "avg_satisfaction": round(avg_sat, 2),
                "error_rate": round(error_rate, 4),
                "p50_latency_ms": round(p50, 1),
                "p90_latency_ms": round(p90, 1),
            }

        return stats

    def get_report_text(self) -> str:
        """
        모드 성능 비교 리포트를 읽기 좋은 텍스트로 반환합니다.

        [UI 표시 및 로그 출력용]
        """
        stats = self.get_mode_stats()
        lines = [
            "=" * 52,
            "  📊 검색 모드 성능 비교 리포트",
            "=" * 52,
        ]

        mode_meta = {
            "fast": (" 빠른 검색", "Fast"),
            "balanced": ("표준 검색", "Balanced"),
            "deep": (" 심층 검색", "Deep"),
        }

        for mode_val, (label, _) in mode_meta.items():
            s = stats[mode_val]
            if s["count"] == 0:
                lines.append(f"\n{label}")
                lines.append("  데이터 없음 (아직 사용 기록 없음)")
                continue

            # 정확도: Precision 을 % 로 표시
            prec_pct = s["avg_precision"] * 100
            sat_str = (
                f"{s['avg_satisfaction']:.1f}/5.0"
                if s["avg_satisfaction"] > 0
                else "미집계"
            )

            lines.append(f"\n{label}  (n={s['count']:,}건)")
            lines.append(f"  평균 응답 속도 : {s['avg_latency_ms'] / 1000:.1f}초")
            lines.append(f"  중앙값 (P50)  : {s['p50_latency_ms'] / 1000:.1f}초")
            lines.append(f"  P90 응답 속도 : {s['p90_latency_ms'] / 1000:.1f}초")
            lines.append(f"  추정 정확도   : {prec_pct:.0f}%")
            lines.append(f"  평균 만족도   : {sat_str}")
            lines.append(f"  오류율        : {s['error_rate'] * 100:.1f}%")

        lines.append("\n" + "=" * 52)
        return "\n".join(lines)

    def get_recent(self, n: int = 20) -> List[EvaluationRecord]:
        """최근 n 건의 평가 레코드 반환."""
        with self._lock:
            return list(reversed(self._records[-n:]))

    def get_total_count(self) -> int:
        """누적 평가 레코드 수."""
        with self._lock:
            return len(self._records)

    # ── 파일 I/O ──────────────────────────────────────────────

    def _save_record(self, record: EvaluationRecord) -> None:
        """레코드를 메모리에 추가하고 파일에 flush."""
        with self._lock:
            self._records.append(record)
            # 메모리 상한 초과 시 가장 오래된 레코드 제거
            if len(self._records) > MAX_MEMORY_RECORDS:
                self._records = self._records[-MAX_MEMORY_RECORDS:]
            self._flush_to_file()

    def _flush_to_file(self) -> None:
        """
        메모리 레코드를 JSON 파일에 원자적으로 씁니다.

        [원자성 보장]
        임시파일(.tmp) → rename 방식 사용.
        쓰기 중 프로세스가 종료되어도 기존 파일은 손상되지 않습니다.

        [주의] _lock 을 이미 보유한 상태에서 호출해야 합니다.
        """
        tmp_path = self._eval_path.with_suffix(".tmp")
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            data = [self._record_to_dict(r) for r in self._records]
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self._eval_path)  # 원자적 교체
        except Exception as exc:
            logger.error(f"평가 파일 저장 실패: {exc}", exc_info=True)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _load_from_file(self) -> None:
        """시작 시 기존 JSON 파일에서 레코드를 로드합니다."""
        if not self._eval_path.exists():
            return
        try:
            raw = json.loads(self._eval_path.read_text(encoding="utf-8"))
            for item in raw[-MAX_MEMORY_RECORDS:]:
                try:
                    # retrieved_docs 하위 객체 복원
                    item["retrieved_docs"] = [
                        RetrievedDocInfo(**d) for d in item.get("retrieved_docs", [])
                    ]
                    self._records.append(EvaluationRecord(**item))
                except Exception:
                    pass  # 스키마 변경으로 역직렬화 실패 시 무시
        except Exception as exc:
            logger.warning(f"평가 파일 로드 실패 (무시): {exc}")

    @staticmethod
    def _record_to_dict(record: EvaluationRecord) -> Dict[str, Any]:
        """EvaluationRecord → JSON 직렬화 가능 dict 변환."""
        d = asdict(record)
        return d


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. 싱글톤 접근자
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_evaluator_instance: Optional[EvaluationLogger] = None
_eval_lock = threading.Lock()


def get_evaluator() -> EvaluationLogger:
    """
    EvaluationLogger 싱글톤을 반환합니다.

    [지연 초기화]
    settings 가 완전히 초기화된 후 첫 호출 시에만 인스턴스를 생성합니다.
    임포트 시점에 settings 에 의존하지 않아 순환 import 방지.
    """
    global _evaluator_instance
    if _evaluator_instance is None:
        with _eval_lock:
            if _evaluator_instance is None:
                from config.settings import settings

                _evaluator_instance = EvaluationLogger(settings.log_dir)
    return _evaluator_instance
