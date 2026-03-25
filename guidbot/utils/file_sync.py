"""
utils/file_sync.py ─ G드라이브 PDF 파일 동기화 유틸리티 (v2.1)

[v2.1 수정사항]
- BUG#3 수정: get_logger(__name__, log_dir=settings.log_dir) 추가
  이전: log_dir 없이 콘솔 출력만 → file_sync.log 파일 저장 안 됨
  수정: settings 직접 임포트하여 항상 파일 핸들러 등록

[설계 원칙]
- mtime(수정 시각) 비교 기반 증분 복사 → 변경된 파일만 재복사 (속도 최적화)
- rglob 으로 하위 폴더(부서별 폴더 구조)까지 재귀 탐색
- dst 는 항상 flat 구조 → 부서 폴더 관계없이 동일한 경로에서 PDF 접근
- SyncResult 데이터클래스로 결과 명확히 반환 (호출부에서 성공/실패 판단)
- G드라이브 미연결 시 warning 만 출력 후 계속 진행 (Fail-Soft 원칙)

[동작 흐름]
G드라이브 경로 (src_dir)
  ├── 원무팀/원무규정.pdf        ─┐
  ├── 간호팀/간호지침.pdf         │  rglob 재귀 탐색
  └── 공통/취업규칙.pdf          ─┘
         ↓ (mtime 비교)
로컬 작업 경로 (dst_dir)         → flat 구조 (하위폴더 없음)
  ├── 원무규정.pdf
  ├── 간호지침.pdf
  └── 취업규칙.pdf
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from config.settings import settings    # BUG#3: settings import (log_dir 참조용)
from utils.logger import get_logger

# BUG#3 수정: log_dir 추가 → 파일 로그 저장 가능하게 됨
logger = get_logger(__name__, log_dir=settings.log_dir)


@dataclass
class SyncResult:
    """
    파일 동기화 결과 요약.

    sync_pdf_files() 반환값이며, 호출부에서 결과를 구조적으로 처리할 수 있습니다.

    Attributes:
        copied:  src 에만 있어 새로 복사된 파일명 목록
        updated: mtime 변경으로 재복사된 파일명 목록
        failed:  복사 실패한 파일명 목록 (OSError 발생)
        skipped: 변경 없어 건너뛴 파일 수 (int, 이름 불필요)
    """

    copied:  List[str] = field(default_factory=list)   # 신규 복사
    updated: List[str] = field(default_factory=list)   # mtime 변경으로 업데이트
    failed:  List[str] = field(default_factory=list)   # 복사 실패
    skipped: int = 0                                    # 변경 없어 건너뜀

    @property
    def total_changed(self) -> int:
        """신규 복사 + 업데이트 합산 (실제로 파일이 갱신된 수)."""
        return len(self.copied) + len(self.updated)

    def log_summary(self) -> None:
        """동기화 결과를 INFO 레벨로 로그에 기록합니다."""
        logger.info(
            f"동기화 완료 | "
            f"신규 {len(self.copied)}개 | "
            f"업데이트 {len(self.updated)}개 | "
            f"건너뜀 {self.skipped}개 | "
            f"실패 {len(self.failed)}개"
        )
        if self.failed:
            logger.warning(f"동기화 실패 파일: {self.failed}")


def sync_pdf_files(
    src_dir: Path,
    dst_dir: Path,
    *,
    extensions: tuple[str, ...] = (".pdf",),
) -> SyncResult:
    """
    src_dir 에서 dst_dir 로 PDF 파일을 증분 동기화합니다.

    [증분 동기화 동작]
    1. src 에 있고 dst 에 없으면 → 복사 (신규)
    2. src 의 mtime > dst 의 mtime → 재복사 (업데이트)
    3. mtime 동일 → 건너뜀 (불필요한 I/O 방지)
    4. G드라이브 미연결(src_dir 없음) → warning 후 빈 결과 반환

    [하위 폴더 처리]
    rglob 으로 부서별 하위 폴더(예: 간호팀/, 원무팀/)까지 재귀 탐색합니다.
    단, dst 에는 항상 flat 구조로 복사 (폴더명 없이 파일명만 사용).
    파일명 충돌 시 마지막으로 처리된 파일이 덮어씁니다.

    Args:
        src_dir   : 원본 디렉토리 (G드라이브 공유 경로)
        dst_dir   : 대상 디렉토리 (로컬 작업 경로)
        extensions: 동기화할 파일 확장자 목록 (기본값: .pdf 만)

    Returns:
        SyncResult 동기화 결과 요약 객체

    Example::

        from utils.file_sync import sync_pdf_files
        from config.settings import settings

        result = sync_pdf_files(settings.rag_source_path, settings.local_work_dir)
        result.log_summary()
        print(f"변경된 파일: {result.total_changed}개")
    """
    result = SyncResult()

    # dst_dir 없으면 생성 (exist_ok=True 로 이미 있으면 무시)
    dst_dir.mkdir(parents=True, exist_ok=True)

    # G드라이브 미연결 등으로 소스 경로가 없을 때 → Fail-Soft 처리
    if not src_dir.exists():
        logger.warning(
            f"원본 경로를 찾을 수 없습니다 (G드라이브 미연결 가능성): {src_dir}"
        )
        return result  # 빈 SyncResult 반환 → 앱은 계속 진행

    # rglob 으로 모든 하위 폴더의 PDF 파일 목록 수집
    # 예: G:\규정집\간호팀\간호지침.pdf, G:\규정집\공통\취업규칙.pdf
    src_files: List[Path] = [
        f
        for ext in extensions
        for f in src_dir.rglob(f"*{ext}")  # ** = 모든 하위 폴더 재귀
        if f.is_file()
    ]

    logger.info(
        f"동기화 시작: {src_dir} → {dst_dir} "
        f"({len(src_files)}개 파일 발견, 하위폴더 포함)"
    )

    for src in sorted(src_files):  # 알파벳 순 정렬로 처리 순서 일관성 확보
        # dst 는 항상 src.name 만 사용 (폴더 구조 제거)
        # 예: G:\규정집\간호팀\간호지침.pdf → D:\working\간호지침.pdf
        dst = dst_dir / src.name

        try:
            if not dst.exists():
                # ── 신규 파일: 복사 ────────────────────────────────
                shutil.copy2(src, dst)   # copy2 = 메타데이터(mtime)도 복사
                result.copied.append(src.name)
                logger.debug(f"  [신규] {src.name}")

            elif src.stat().st_mtime > dst.stat().st_mtime:
                # ── 변경된 파일: 재복사 ───────────────────────────
                # src 의 최종 수정 시각이 dst 보다 새롭다 = 소스가 업데이트됨
                shutil.copy2(src, dst)
                result.updated.append(src.name)
                logger.debug(f"  [업데이트] {src.name}")

            else:
                # ── 변경 없음: 건너뜀 ─────────────────────────────
                # 내용이 완전히 동일하더라도 mtime 이 같으면 건너뜀
                # (MD5 해시 비교는 성능상 과도한 비용)
                result.skipped += 1

        except OSError as exc:
            # 권한 오류, 디스크 공간 부족 등 → 해당 파일만 실패 처리, 계속 진행
            result.failed.append(src.name)
            logger.error(f"  [실패] {src.name}: {exc}")

    result.log_summary()
    return result
