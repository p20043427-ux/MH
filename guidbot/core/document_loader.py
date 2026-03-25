"""
core/document_loader.py ─ PDF 문서 로드·분할·전처리 파이프라인 (v3.0)

[v3.0 고도화 내용]
1. 조항 경계 인식 청킹 (ArticleBoundaryChunker)
   - "제N조" 패턴으로 조항 시작점 인식 → 조항 단위로 청크 분할
   - 이유: 기존 RecursiveCharacterTextSplitter 는 문자 수 기준으로 자르므로
           "제3조(연차휴가)의 내용이 두 청크에 걸쳐 분리"되는 문제 발생
   - 조항 경계를 먼저 찾고, 청크가 chunk_size 초과 시 재분할

2. pdfplumber 폴백 (이중 추출 전략)
   - 1차: PyPDFLoader (빠름, 레이아웃 보존)
   - 2차: pdfplumber (스캔 PDF·복잡한 표 포함 PDF 에서 더 나은 추출)
   - 두 추출 결과 비교하여 더 많은 텍스트가 추출된 것 선택

3. 컨텍스트 헤더 자동 주입
   - 각 청크 앞에 "[파일명] [조항번호]" 헤더 추가
   - 검색 시 출처 정보가 청크 내용에 포함되어 정확도 향상

4. MD5 해시 기반 중복 제거
   - 동일한 내용의 청크가 중복 색인되지 않도록 해시 비교

[처리 흐름]
PDF 파일
  → ① PyPDFLoader 또는 pdfplumber 로 텍스트 추출
  → ② text_cleaner.process() 로 정제 (헤더/푸터/결재란 제거)
  → ③ 조항 경계 인식 청킹 (ArticleBoundaryChunker)
  → ④ 컨텍스트 헤더 주입
  → ⑤ MD5 중복 제거
  → Document 리스트 반환
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from langchain_core.documents import Document
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config.settings import settings
from utils.exceptions import DocumentProcessError
from utils.logger import get_logger
from utils.text_cleaner import process as preprocess

logger = get_logger(__name__, log_dir=settings.log_dir)

# 조항 시작 패턴: "제1조", "제 1 조", "제1 조" 등 다양한 형태 처리
_RE_ARTICLE_BOUNDARY = re.compile(r"(?=제\s*\d+\s*조)")


# ──────────────────────────────────────────────────────────────────────
#  결과 데이터 클래스
# ──────────────────────────────────────────────────────────────────────

@dataclass
class LoadResult:
    """
    문서 로드 파이프라인 결과 요약.

    build_db.py 에서 이 객체를 통해 성공/실패/통계를 확인합니다.

    Attributes:
        documents:    분할 완료된 Document 리스트 (색인 대상)
        loaded_files: 성공적으로 처리된 파일명 목록
        failed_files: 처리 실패한 파일명 목록
        total_pages:  처리된 총 페이지 수
        total_chunks: 최종 청크 수
    """

    documents:    List[Document] = field(default_factory=list)
    loaded_files: List[str]      = field(default_factory=list)
    failed_files: List[str]      = field(default_factory=list)
    total_pages:  int = 0
    total_chunks: int = 0

    def log_summary(self) -> None:
        """로드 결과를 INFO 레벨로 로그에 기록합니다."""
        failed_msg = f"{len(self.failed_files)}개 실패" if self.failed_files else "실패 없음"
        logger.info(
            f"로드 완료: 파일 {len(self.loaded_files)}개 성공 | {failed_msg} "
            f"| {self.total_pages}페이지 | {self.total_chunks}개 청크"
        )
        if self.failed_files:
            logger.warning(f"처리 실패 파일: {self.failed_files}")


# ──────────────────────────────────────────────────────────────────────
#  단일 PDF 로딩 (이중 추출 전략)
# ──────────────────────────────────────────────────────────────────────

def _extract_with_pypdf(pdf_path: Path) -> List[Document]:
    """PyPDFLoader 로 PDF 에서 텍스트를 추출합니다."""
    return PyPDFLoader(str(pdf_path)).load()


def _extract_with_pdfplumber(pdf_path: Path) -> List[Document]:
    """
    pdfplumber 로 PDF 에서 텍스트를 추출합니다.

    pdfplumber 사용 케이스:
    - 표(Table) 포함 PDF: PyPDF 는 표를 줄바꿈 없이 이어붙이는 경우 많음
    - 2단 레이아웃 PDF: 열 순서가 뒤섞이는 문제를 더 잘 처리
    - 스캔 PDF: pdfplumber 의 word 추출이 더 정확한 경우 있음

    Returns:
        Document 리스트. pdfplumber 미설치 시 빈 리스트.
    """
    try:
        import pdfplumber
    except ImportError:
        return []  # pdfplumber 없으면 조용히 건너뜀

    docs = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if text.strip():
                    docs.append(Document(
                        page_content=text,
                        metadata={"source": pdf_path.name, "page": page_num},
                    ))
    except Exception as exc:
        logger.debug(f"pdfplumber 추출 실패 [{pdf_path.name}]: {exc}")

    return docs


def _load_single_pdf(pdf_path: Path) -> List[Document]:
    """
    단일 PDF 파일을 로드하고 전처리합니다.

    [이중 추출 전략]
    1. PyPDFLoader 로 추출 시도
    2. pdfplumber 로 추출 시도
    3. 총 텍스트 길이가 더 긴 결과 선택 (더 많이 추출한 것이 더 좋음)
    4. text_cleaner.process() 로 노이즈 제거

    Args:
        pdf_path: 처리할 PDF 파일 경로

    Returns:
        전처리된 Document 리스트 (유효 페이지만 포함)

    Raises:
        DocumentProcessError: PDF 파싱 완전 실패 시
    """
    # ── 1차 추출: PyPDFLoader ─────────────────────────────────────────
    try:
        pypdf_docs = _extract_with_pypdf(pdf_path)
    except Exception as exc:
        raise DocumentProcessError(filename=pdf_path.name, reason=str(exc)) from exc

    # ── 2차 추출: pdfplumber ──────────────────────────────────────────
    plumber_docs = _extract_with_pdfplumber(pdf_path)

    # 더 많이 추출한 것 선택
    pypdf_total   = sum(len(d.page_content) for d in pypdf_docs)
    plumber_total = sum(len(d.page_content) for d in plumber_docs)

    if plumber_total > pypdf_total * 1.1:  # 10% 이상 많으면 plumber 선택
        raw_pages = plumber_docs
        logger.debug(
            f"  pdfplumber 선택: {plumber_total:,}자 > PyPDF {pypdf_total:,}자 "
            f"[{pdf_path.name}]"
        )
    else:
        raw_pages = pypdf_docs

    # ── 전처리 (노이즈 제거·품질 필터) ──────────────────────────────
    valid_docs: List[Document] = []
    for page in raw_pages:
        result = preprocess(page.page_content, settings.min_text_length)
        if result is None:
            continue  # 목차/개정이력/빈 페이지 등 노이즈 → 제외

        page.page_content = result.content
        page.metadata["source"] = pdf_path.name
        page.metadata.update(result.metadata)  # article, chapter, dates 등 추가
        valid_docs.append(page)

    return valid_docs


# ──────────────────────────────────────────────────────────────────────
#  조항 경계 인식 청킹
# ──────────────────────────────────────────────────────────────────────

def _split_by_article_boundary(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> List[str]:
    """
    조항 경계("제N조")를 인식하여 텍스트를 분할합니다.

    [알고리즘]
    1. "제N조" 패턴으로 조항 시작 위치 찾기
    2. 각 조항을 하나의 세그먼트로 추출
    3. 세그먼트가 chunk_size 초과 시 RecursiveCharacterTextSplitter 로 재분할
    4. 세그먼트가 chunk_size 미만이면 다음 세그먼트와 합치기 시도 (짧은 조항 합산)

    [왜 조항 경계 인식이 중요한가?]
    "제3조(연차유급휴가): 직원은 1년에 15일의 연차휴가를 받는다."
    이 내용이 두 청크로 분리되면:
    - 청크 A: "제3조(연차유급휴가): 직원은 1년에 15일의"
    - 청크 B: "연차휴가를 받는다."
    → 검색 시 청크 B는 "연차휴가" 질문에 "어느 직원"인지 맥락 없이 검색됨

    Args:
        text:          분할할 텍스트
        chunk_size:    최대 청크 크기 (문자 수)
        chunk_overlap: 청크 간 오버랩 크기

    Returns:
        분할된 텍스트 조각 리스트
    """
    # 조항 경계 위치 찾기
    boundaries = [m.start() for m in _RE_ARTICLE_BOUNDARY.finditer(text)]

    if len(boundaries) < 2:
        # 조항이 없거나 1개인 경우 → 일반 청킹으로 폴백
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ".", " ", ""],
        )
        return splitter.split_text(text)

    # 조항별 세그먼트 추출
    segments: List[str] = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(text)
        segment = text[start:end].strip()
        if segment:
            segments.append(segment)

    # 세그먼트 크기 조정: 너무 크면 재분할, 너무 작으면 합산
    final_chunks: List[str] = []
    buffer = ""

    for seg in segments:
        if len(seg) > chunk_size:
            # 조항이 너무 길면 재분할
            if buffer:
                final_chunks.append(buffer)
                buffer = ""
            sub_splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=["\n\n", "\n", ".", " ", ""],
            )
            final_chunks.extend(sub_splitter.split_text(seg))
        elif len(buffer) + len(seg) > chunk_size:
            # 버퍼가 가득 찼으면 플러시하고 새 버퍼 시작
            if buffer:
                final_chunks.append(buffer)
            buffer = seg
        else:
            # 버퍼에 추가 (짧은 조항 합산)
            buffer = (buffer + "\n\n" + seg).strip() if buffer else seg

    if buffer:
        final_chunks.append(buffer)

    return final_chunks if final_chunks else [text]


# ──────────────────────────────────────────────────────────────────────
#  중복 제거
# ──────────────────────────────────────────────────────────────────────

def _deduplicate(documents: List[Document]) -> List[Document]:
    """
    MD5 해시 기반으로 중복 청크를 제거합니다.

    동일한 내용이 여러 PDF 에 포함된 경우 (예: 공지 문서가 여러 규정집에 첨부)
    중복 색인을 방지하여 검색 노이즈를 줄입니다.

    Args:
        documents: 중복 제거 전 Document 리스트

    Returns:
        중복 제거된 Document 리스트
    """
    seen: set[str] = set()
    unique: List[Document] = []

    for doc in documents:
        # 내용의 MD5 해시 계산 (앞 500자만 비교 → 속도 최적화)
        content_hash = hashlib.md5(
            doc.page_content[:500].encode("utf-8")
        ).hexdigest()

        if content_hash not in seen:
            seen.add(content_hash)
            unique.append(doc)

    removed = len(documents) - len(unique)
    if removed > 0:
        logger.info(f"중복 청크 {removed}개 제거 ({len(documents)} → {len(unique)}개)")

    return unique


# ──────────────────────────────────────────────────────────────────────
#  컨텍스트 헤더 주입
# ──────────────────────────────────────────────────────────────────────

def _inject_context_header(doc: Document) -> Document:
    """
    청크 앞에 출처 컨텍스트 헤더를 주입합니다.

    [헤더 형식]
    [취업규칙.pdf | p.5 | 제3조]
    <본문 내용>

    [효과]
    - LLM 이 답변 생성 시 출처 정보를 자동으로 인식
    - 검색 시 "취업규칙 제3조" 쿼리가 해당 청크와 더 잘 매칭됨

    Args:
        doc: 원본 Document

    Returns:
        헤더가 주입된 Document (원본 메타데이터 보존)
    """
    source = doc.metadata.get("source", "")
    page   = doc.metadata.get("page", "")
    article = doc.metadata.get("article", "")

    header_parts = []
    if source:
        header_parts.append(source)
    if page != "" and page is not None:
        header_parts.append(f"p.{page}")
    if article:
        header_parts.append(article)

    if header_parts:
        header = f"[{' | '.join(header_parts)}]\n"
        doc.page_content = header + doc.page_content

    return doc


# ──────────────────────────────────────────────────────────────────────
#  공개 API
# ──────────────────────────────────────────────────────────────────────

def load_pdfs(pdf_dir: Path) -> LoadResult:
    """
    디렉토리 내 모든 PDF 파일을 로드하고 전처리합니다.

    Args:
        pdf_dir: PDF 파일이 있는 디렉토리

    Returns:
        LoadResult (처리된 Document 리스트, 성공/실패 파일 목록, 통계)
    """
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning(f"PDF 파일을 찾을 수 없습니다: {pdf_dir}")
        return LoadResult()

    result = LoadResult()
    logger.info(f"PDF 로딩 시작: {len(pdf_files)}개 파일 ({pdf_dir})")

    for idx, pdf_path in enumerate(pdf_files, 1):
        logger.info(f"  [{idx}/{len(pdf_files)}] {pdf_path.name}")
        try:
            docs = _load_single_pdf(pdf_path)
            result.documents.extend(docs)
            result.loaded_files.append(pdf_path.name)
            result.total_pages += len(docs)
            logger.debug(f"    → {len(docs)}페이지 유효")
        except DocumentProcessError as exc:
            result.failed_files.append(pdf_path.name)
            logger.error(f"    실패: {exc}")

    return result


def split_documents(
    documents: List[Document],
    chunk_size:    int = settings.chunk_size,
    chunk_overlap: int = settings.chunk_overlap,
) -> List[Document]:
    """
    문서 리스트를 조항 경계 인식 방식으로 분할합니다.

    [처리 순서]
    1. 각 Document 의 page_content 에서 조항 경계 청킹
    2. 분할된 청크에 컨텍스트 헤더 주입
    3. MD5 기반 중복 제거

    Args:
        documents:     분할할 Document 리스트 (load_pdfs 반환값)
        chunk_size:    청크 최대 크기 (기본값: settings.chunk_size)
        chunk_overlap: 청크 간 오버랩 크기

    Returns:
        분할·헤더주입·중복제거 완료된 Document 리스트
    """
    if not documents:
        return []

    all_chunks: List[Document] = []

    for doc in documents:
        # 조항 경계 인식 분할
        text_chunks = _split_by_article_boundary(
            doc.page_content, chunk_size, chunk_overlap
        )

        for chunk_text in text_chunks:
            if not chunk_text.strip():
                continue
            # 원본 메타데이터 복사 + 청크 내용 설정
            chunk_doc = Document(
                page_content=chunk_text,
                metadata=dict(doc.metadata),  # 딕셔너리 복사 (참조 공유 방지)
            )
            # 컨텍스트 헤더 주입
            chunk_doc = _inject_context_header(chunk_doc)
            all_chunks.append(chunk_doc)

    # 중복 제거
    unique_chunks = _deduplicate(all_chunks)

    logger.info(
        f"청크 분할 완료: {len(documents)}페이지 → {len(unique_chunks)}개 청크"
    )
    return unique_chunks


def load_and_split(pdf_dir: Path) -> LoadResult:
    """
    PDF 로드·전처리·분할 통합 파이프라인 (메인 진입점).

    build_db.py 에서 이 함수 하나만 호출하면 됩니다.

    [처리 순서]
    1. load_pdfs():         PDF 로드 + text_cleaner 전처리
    2. split_documents():   조항 경계 청킹 + 헤더 주입 + 중복 제거

    Args:
        pdf_dir: PDF 파일이 있는 디렉토리

    Returns:
        최종 처리 완료된 LoadResult
        (result.documents = 색인 준비된 청크 리스트)

    Example::

        from core.document_loader import load_and_split
        from config.settings import settings

        result = load_and_split(settings.local_work_dir)
        print(f"총 {result.total_chunks}개 청크 준비됨")
    """
    result = load_pdfs(pdf_dir)
    if not result.documents:
        return result

    chunks = split_documents(result.documents)
    result.documents    = chunks
    result.total_chunks = len(chunks)
    result.log_summary()
    return result
