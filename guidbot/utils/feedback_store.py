"""
utils/feedback_store.py  ─  답변 피드백 저장소 (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[설계 목적]
  사용자의 👍 / 👎 피드백을 JSONL 파일로 저장하여
  향후 RAG 파이프라인 개선 데이터로 활용.

[저장 포맷 — JSONL (line-delimited JSON)]
  한 줄 = 한 건의 피드백
  {
    "id":         "a1b2c3d4",       # UUID 앞 8자리
    "timestamp":  "2025-07-16T...", # ISO-8601
    "question":   "연차휴가 신청 방법은?",
    "answer":     "연차휴가는...",  # LLM 응답 전문
    "feedback":   "positive",       # "positive" | "negative"
    "mode":       "standard",       # 검색 모드
    "sources":    ["취업규칙.pdf p.17", ...],
    "session_id": "sess_xyz"        # 세션 추적용
  }

[JSONL 선택 이유]
  · 한 줄씩 append — 동시성 문제 최소화
  · 줄 단위 읽기/파싱 → pandas/DuckDB 로 분석 가능
  · 파일 손상 시 일부만 손실 (JSON 배열보다 안전)

[RAG 개선 활용 방법]
  1. negative 피드백 질문 → Hard Negative Mining
     → 해당 질문에서 잘못 검색된 문서 파악
  2. positive 피드백 질문·답변 쌍 → Fine-tuning 데이터셋
  3. 빈번한 negative 주제 → 청크 분할 기준 재검토
  4. search_mode 별 피드백률 → 모드 기본값 조정 근거
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# ──────────────────────────────────────────────────────────────────────
#  파일 경로 설정
# ──────────────────────────────────────────────────────────────────────


def _get_feedback_path() -> Path:
    """피드백 JSONL 파일 경로 반환 (없으면 생성)."""
    try:
        base_dir = settings.log_dir or Path("logs")
    except AttributeError:
        base_dir = Path("logs")
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "feedback.jsonl"


# 동시 쓰기 보호용 lock
_write_lock = threading.Lock()


# ──────────────────────────────────────────────────────────────────────
#  데이터 클래스
# ──────────────────────────────────────────────────────────────────────


@dataclass
class FeedbackRecord:
    """
    단일 피드백 레코드.

    Attributes:
        question:   사용자 질문 원문
        answer:     LLM 응답 전문
        feedback:   "positive" (👍) | "negative" (👎)
        mode:       검색 모드 ("fast" | "standard" | "deep")
        sources:    참고된 문서 목록 ["파일명 p.X", ...]
        session_id: Streamlit 세션 ID (추적용)
        id:         UUID 앞 8자리 (자동 생성)
        timestamp:  ISO-8601 UTC 시각 (자동 생성)
    """

    question: str
    answer: str
    feedback: str  # "positive" | "negative"
    mode: str = "standard"
    sources: List[str] = field(default_factory=list)
    session_id: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────
#  핵심 함수: 저장
# ──────────────────────────────────────────────────────────────────────


def save_feedback(
    question: str,
    answer: str,
    feedback: str,
    mode: str = "standard",
    sources: List[str] = None,
    session_id: str = "",
) -> Optional[str]:
    """
    피드백 1건을 JSONL 파일에 저장합니다.

    [동시성 처리]
    여러 사용자가 동시에 피드백을 보낼 경우를 위해
    threading.Lock 으로 파일 쓰기 보호.
    Streamlit 싱글 서버 환경에서는 거의 발생하지 않으나
    안전을 위해 유지.

    Args:
        question:   사용자 질문
        answer:     LLM 응답 (전문 또는 요약)
        feedback:   "positive" | "negative"
        mode:       검색 모드
        sources:    출처 문서 목록
        session_id: 세션 식별자

    Returns:
        저장된 레코드 ID (실패 시 None)
    """
    record = FeedbackRecord(
        question=question.strip()[:2000],  # 최대 2000자
        answer=answer.strip()[:5000],  # 최대 5000자
        feedback=feedback,
        mode=mode,
        sources=sources or [],
        session_id=session_id,
    )

    feedback_path = _get_feedback_path()

    try:
        with _write_lock:
            with open(feedback_path, "a", encoding="utf-8") as f:
                f.write(record.to_json_line() + "\n")

        logger.info(
            f"피드백 저장: [{record.id}] {feedback} | mode={mode} | q='{question[:30]}'"
        )
        return record.id

    except Exception as exc:
        logger.error(f"피드백 저장 실패: {exc}", exc_info=True)
        return None


# ──────────────────────────────────────────────────────────────────────
#  읽기/분석 함수
# ──────────────────────────────────────────────────────────────────────


def load_all_feedback() -> List[dict]:
    """
    저장된 피드백 전체를 리스트로 반환합니다.

    Returns:
        피드백 레코드 dict 목록 (최신순)
    """
    feedback_path = _get_feedback_path()
    if not feedback_path.exists():
        return []

    records: List[dict] = []
    try:
        with open(feedback_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"피드백 JSON 파싱 실패: {line[:50]}")
    except Exception as exc:
        logger.error(f"피드백 파일 읽기 실패: {exc}", exc_info=True)

    # 최신 순 정렬
    return sorted(records, key=lambda r: r.get("timestamp", ""), reverse=True)


def get_feedback_stats() -> Dict[str, int | float]:
    """
    피드백 집계 통계를 반환합니다.

    Returns:
        {
          "total":         전체 피드백 수,
          "positive":      긍정 수,
          "negative":      부정 수,
          "positive_rate": 긍정률 (0.0~1.0),
          "by_mode":       {"fast": {...}, "standard": {...}, "deep": {...}},
        }
    """
    records = load_all_feedback()
    if not records:
        return {
            "total": 0,
            "positive": 0,
            "negative": 0,
            "positive_rate": 0.0,
            "by_mode": {},
        }

    positive = sum(1 for r in records if r.get("feedback") == "positive")
    negative = sum(1 for r in records if r.get("feedback") == "negative")
    total = positive + negative

    # 모드별 집계
    by_mode: Dict[str, Dict] = {}
    for mode in ("fast", "standard", "deep"):
        mode_recs = [r for r in records if r.get("mode") == mode]
        pos = sum(1 for r in mode_recs if r.get("feedback") == "positive")
        neg = sum(1 for r in mode_recs if r.get("feedback") == "negative")
        cnt = pos + neg
        by_mode[mode] = {
            "total": cnt,
            "positive": pos,
            "negative": neg,
            "positive_rate": round(pos / cnt, 3) if cnt > 0 else 0.0,
        }

    return {
        "total": total,
        "positive": positive,
        "negative": negative,
        "positive_rate": round(positive / total, 3) if total > 0 else 0.0,
        "by_mode": by_mode,
    }


def get_negative_feedback_questions(limit: int = 50) -> List[dict]:
    """
    부정 피드백 받은 질문 목록 반환 (RAG 개선 데이터 활용용).

    Args:
        limit: 최대 반환 건수

    Returns:
        부정 피드백 레코드 목록 (최신순, limit 개)
    """
    records = load_all_feedback()
    negatives = [r for r in records if r.get("feedback") == "negative"]
    return negatives[:limit]


def export_as_training_data(output_path: Optional[Path] = None) -> Path:
    """
    긍정 피드백 데이터를 Fine-tuning 형식(Q&A JSON)으로 내보냅니다.

    [출력 형식]
    [
      {"prompt": "연차휴가 신청 방법은?", "completion": "연차휴가는..."},
      ...
    ]

    향후 활용:
    · Gemini Fine-tuning Dataset
    · RAG Evaluation (RAGAS) 골든셋 구축

    Args:
        output_path: 저장 경로 (기본값: logs/training_data.json)

    Returns:
        저장된 파일 경로
    """
    if output_path is None:
        try:
            output_path = Path(settings.log_dir) / "training_data.json"
        except AttributeError:
            output_path = Path("logs") / "training_data.json"

    records = load_all_feedback()
    positives = [r for r in records if r.get("feedback") == "positive"]

    training_data = [
        {
            "prompt": r["question"],
            "completion": r["answer"],
            "mode": r.get("mode", "standard"),
            "sources": r.get("sources", []),
            "id": r.get("id", ""),
        }
        for r in positives
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(training_data, f, ensure_ascii=False, indent=2)

    logger.info(f"학습 데이터 내보내기: {len(training_data)}건 → {output_path}")
    return output_path
