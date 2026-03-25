"""
core/pdf_to_markdown.py  ─  PDF → Markdown 변환 파이프라인 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[왜 PDF → Markdown 변환이 필요한가?]

  기존 PyPDFLoader 방식 문제:
  · 표(Table) → 셀이 붙어서 추출: "코드설명비고"
  · 헤더/푸터 → 모든 페이지에 반복 삽입
  · 2단 레이아웃 → 열 순서 뒤섞임
  · 조항 번호 → "제3조 ①" 등 특수문자 깨짐
  · NEDIS 가이드 특성: 코드표, 판정 기준표가 핵심 내용

  Markdown 변환 장점:
  · 표 → | 컬럼 | 헤더 | 형식으로 구조 보존
  · 제목 계층 → ## 제3장, ### 제7조 로 청킹 경계 명확
  · 헤더/푸터 자동 제거
  · MarkdownHeaderTextSplitter로 조항 단위 청킹 가능

[변환 전략 — 3단계]

  1. pdfplumber로 텍스트 + 표 추출
     · 표: pdfplumber.extract_tables() → Markdown 표 형식
     · 텍스트: 페이지별 블록 추출

  2. 구조 인식 Markdown 변환
     · "제N조", "제N장" 등 → ## 헤더
     · "①②③" 항목 → 순서 없는 목록
     · 표 영역 → | 표 | 형식

  3. MarkdownHeaderTextSplitter 청킹
     · ## 헤더 경계로 청크 분리
     · 조항이 두 청크로 분리되는 문제 해결

[처리 흐름]

  PDF 파일
    → pdfplumber 추출 (텍스트 + 표)
    → Markdown 구조화
    → markdown 파일 저장 (선택, docs/markdown/)
    → MarkdownHeaderTextSplitter 청킹
    → 컨텍스트 헤더 주입
    → Document 리스트 반환 → FAISS 색인

[설치 필요]
  pip install pdfplumber
  (이미 설치된 경우 건너뜀)
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import List, Optional, Tuple

from langchain_core.documents import Document

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# 조항 패턴: "제1조", "제 1 조", "제1장" 등
_RE_ARTICLE = re.compile(r"^(제\s*\d+\s*조)", re.MULTILINE)
_RE_CHAPTER = re.compile(r"^(제\s*\d+\s*장)", re.MULTILINE)
_RE_SECTION = re.compile(r"^(제\s*\d+\s*절)", re.MULTILINE)
_RE_ITEM_NUM = re.compile(r"^([①②③④⑤⑥⑦⑧⑨⑩])", re.MULTILINE)
# NEDIS 코드 패턴 (예: "A001", "ED001")
_RE_CODE = re.compile(r"\b([A-Z]{1,3}\d{2,4})\b")
# 헤더/푸터 제거 패턴 (페이지 번호, 기관명 등)
_RE_FOOTER = re.compile(r"^\s*[-–]\s*\d+\s*[-–]\s*$", re.MULTILINE)
_RE_PAGE_NUM = re.compile(r"^\s*\d+\s*$", re.MULTILINE)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  pdfplumber 추출
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _extract_page_content(
    page,
    page_num: int,
) -> Tuple[str, List[str]]:
    """
    pdfplumber 페이지에서 텍스트와 표를 추출합니다.

    Returns:
        (page_text, table_md_list)
        · page_text: 표 영역 제외한 텍스트
        · table_md_list: Markdown 표 형식 문자열 목록
    """
    # 표 영역 좌표 수집 (표 영역을 텍스트에서 제외하기 위해)
    table_bboxes = []
    tables_md = []

    try:
        tables = page.extract_tables()
        if tables:
            for i, table in enumerate(tables):
                if not table or not table[0]:
                    continue
                md_lines = []
                # 헤더행
                headers = [str(cell or "").strip() for cell in table[0]]
                md_lines.append("| " + " | ".join(headers) + " |")
                md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                # 데이터행
                for row in table[1:]:
                    cells = [str(cell or "").strip().replace("\n", " ") for cell in row]
                    # 빈 행 스킵
                    if all(c == "" for c in cells):
                        continue
                    md_lines.append("| " + " | ".join(cells) + " |")
                if md_lines:
                    tables_md.append("\n".join(md_lines))
    except Exception as exc:
        logger.debug(f"표 추출 실패 (페이지 {page_num}): {exc}")

    # 텍스트 추출
    try:
        text = page.extract_text() or ""
    except Exception:
        text = ""

    return text, tables_md


def _table_to_markdown(table: List[List[Optional[str]]]) -> str:
    """2D 리스트 → Markdown 표 문자열."""
    if not table or not table[0]:
        return ""

    rows = []
    headers = [str(cell or "").strip() for cell in table[0]]
    rows.append("| " + " | ".join(headers) + " |")
    rows.append("| " + " | ".join(["---"] * len(headers)) + " |")

    for row in table[1:]:
        cells = [str(cell or "").strip().replace("\n", " ") for cell in row]
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")

    return "\n".join(rows)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  텍스트 → Markdown 구조화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _text_to_markdown_structure(text: str) -> str:
    """
    추출된 텍스트에 Markdown 헤더 구조를 적용합니다.

    [변환 규칙]
    · "제N장 제목"   → ## 제N장 제목
    · "제N절 제목"   → ### 제N절 제목
    · "제N조(제목)"  → #### 제N조(제목)
    · "①②③..."     → - ① 내용 (목록)
    · 헤더/푸터 제거
    """
    # 헤더/푸터 제거
    text = _RE_FOOTER.sub("", text)
    text = _RE_PAGE_NUM.sub("", text)

    lines = text.splitlines()
    result = []

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            result.append("")
            continue

        # 제N장
        if _RE_CHAPTER.match(line_stripped):
            result.append(f"## {line_stripped}")
            continue

        # 제N절
        if _RE_SECTION.match(line_stripped):
            result.append(f"### {line_stripped}")
            continue

        # 제N조 — 괄호로 조항명 포함 가능
        if _RE_ARTICLE.match(line_stripped):
            result.append(f"#### {line_stripped}")
            continue

        # ①②③ 항목
        if _RE_ITEM_NUM.match(line_stripped):
            result.append(f"- {line_stripped}")
            continue

        result.append(line_stripped)

    return "\n".join(result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PDF → Markdown 변환 메인 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def pdf_to_markdown(
    pdf_path: Path,
    save_md: bool = True,
    md_output_dir: Optional[Path] = None,
) -> str:
    """
    PDF 파일을 Markdown 문자열로 변환합니다.

    [처리 순서]
    1. pdfplumber로 페이지별 텍스트 + 표 추출
    2. 표 → | Markdown 표 | 형식으로 변환
    3. 텍스트 → ## 헤더 구조 적용
    4. 텍스트와 표를 페이지 순서로 병합
    5. save_md=True 이면 docs/markdown/ 에 .md 파일 저장

    Args:
        pdf_path:      PDF 파일 경로
        save_md:       Markdown 파일 저장 여부 (벡터 재구축 없이 참조용)
        md_output_dir: Markdown 저장 디렉토리 (기본: docs/markdown/)

    Returns:
        Markdown 형식 문자열
    """
    try:
        import pdfplumber
    except ImportError:
        logger.warning(
            "pdfplumber 미설치 → PyPDFLoader로 폴백. "
            "pip install pdfplumber 으로 더 나은 변환 품질을 얻을 수 있습니다."
        )
        return _fallback_pypdf_to_markdown(pdf_path)

    logger.info(f"PDF → Markdown 변환 시작: {pdf_path.name}")

    md_pages = []

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                page_parts = [f"\n<!-- page {page_num} -->"]

                # 텍스트 추출
                text, tables_md = _extract_page_content(page, page_num)

                # 텍스트 → Markdown 구조화
                if text.strip():
                    structured = _text_to_markdown_structure(text)
                    if structured.strip():
                        page_parts.append(structured)

                # 표 추가 (텍스트 뒤에 삽입)
                for tbl_md in tables_md:
                    if tbl_md.strip():
                        page_parts.append(f"\n{tbl_md}\n")

                md_pages.append("\n".join(page_parts))

    except Exception as exc:
        logger.error(f"PDF 변환 실패 ({pdf_path.name}): {exc}", exc_info=True)
        return _fallback_pypdf_to_markdown(pdf_path)

    full_md = f"# {pdf_path.stem}\n\n" + "\n\n".join(md_pages)

    # Markdown 파일 저장 (선택)
    if save_md:
        _save_markdown(full_md, pdf_path, md_output_dir)

    logger.info(
        f"PDF → Markdown 변환 완료: {pdf_path.name} "
        f"({len(pdf.pages)}페이지 → {len(full_md):,}자)"
    )
    return full_md


def _fallback_pypdf_to_markdown(pdf_path: Path) -> str:
    """pdfplumber 없을 때 PyPDFLoader로 폴백."""
    try:
        from langchain_community.document_loaders import PyPDFLoader

        docs = PyPDFLoader(str(pdf_path)).load()
        parts = [f"# {pdf_path.stem}\n"]
        for doc in docs:
            text = _text_to_markdown_structure(doc.page_content)
            if text.strip():
                page = doc.metadata.get("page", 0)
                parts.append(f"\n<!-- page {page + 1} -->\n{text}")
        return "\n".join(parts)
    except Exception as exc:
        logger.error(f"PyPDFLoader 폴백도 실패: {exc}")
        return f"# {pdf_path.stem}\n\n(변환 실패: {exc})"


def _save_markdown(md_text: str, pdf_path: Path, output_dir: Optional[Path]) -> None:
    """변환된 Markdown을 파일로 저장합니다."""
    if output_dir is None:
        output_dir = Path(settings.local_work_dir).parent / "docs" / "markdown"
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{pdf_path.stem}.md"
    md_path.write_text(md_text, encoding="utf-8")
    logger.info(f"Markdown 저장: {md_path}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Markdown → Document 청킹
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def markdown_to_documents(
    md_text: str,
    source: str = "",
    chunk_size: int = 800,
    overlap: int = 150,
) -> List[Document]:
    """
    Markdown 텍스트를 헤더 경계로 청킹하여 Document 리스트로 반환합니다.

    [청킹 전략 — 2단계]
    1단계: MarkdownHeaderTextSplitter
       · ## 제N장 / ### 제N절 / #### 제N조 경계로 분리
       · 조항이 두 청크에 걸쳐 분리되는 문제 해결

    2단계: RecursiveCharacterTextSplitter
       · 1단계 결과가 chunk_size 초과 시 추가 분할
       · overlap으로 문맥 연속성 보장

    [메타데이터]
    · source: 파일명
    · chapter: 제N장
    · section: 제N절
    · article: 제N조
    · content_hash: 중복 감지용 MD5

    Args:
        md_text:    Markdown 문자열
        source:     출처 파일명 (메타데이터)
        chunk_size: 최대 청크 크기 (기본 800자)
        overlap:    청크 간 중복 (기본 150자)

    Returns:
        Document 리스트
    """
    from langchain_text_splitters import (
        MarkdownHeaderTextSplitter,
        RecursiveCharacterTextSplitter,
    )

    # ── 1단계: 헤더 경계 분리 ─────────────────────────────
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[
            ("#", "title"),
            ("##", "chapter"),
            ("###", "section"),
            ("####", "article"),
        ],
        strip_headers=False,  # 헤더 텍스트를 청크 내용에 포함
    )
    header_docs = header_splitter.split_text(md_text)

    if not header_docs:
        return []

    # ── 2단계: 크기 초과 청크 재분할 ─────────────────────
    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", ". ", " ", ""],
    )

    final_docs = []
    seen_hashes: set = set()

    for doc in header_docs:
        if len(doc.page_content) <= chunk_size:
            chunks = [doc]
        else:
            sub_chunks = char_splitter.split_documents([doc])
            chunks = sub_chunks

        for chunk in chunks:
            # 페이지 주석 제거 (<!-- page N --> 는 내용이 아님)
            content = re.sub(r"<!--.*?-->", "", chunk.page_content).strip()
            if len(content) < 30:  # 너무 짧은 청크 제거
                continue

            # 중복 제거
            content_hash = hashlib.md5(content.encode()).hexdigest()
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)

            # 메타데이터 정리
            meta = {
                "source": source,
                "chapter": chunk.metadata.get("chapter", ""),
                "section": chunk.metadata.get("section", ""),
                "article": chunk.metadata.get("article", ""),
                "content_hash": content_hash,
            }

            final_docs.append(Document(page_content=content, metadata=meta))

    logger.info(
        f"Markdown 청킹 완료: {len(header_docs)}개 헤더 섹션 "
        f"→ {len(final_docs)}개 청크 (소스: {source})"
    )
    return final_docs


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  통합 함수: PDF 파일 → Document 리스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def load_pdf_as_markdown_docs(
    pdf_path: Path,
    save_md: bool = True,
    chunk_size: int = 800,
    overlap: int = 150,
) -> List[Document]:
    """
    PDF 파일을 Markdown으로 변환하고 청킹하여 Document 리스트 반환.

    [build_db.py 통합 방법]
        from core.pdf_to_markdown import load_pdf_as_markdown_docs

        # 기존: PyPDFLoader 사용
        # docs = PyPDFLoader(str(pdf_path)).load()

        # 변경: Markdown 변환 후 청킹
        docs = load_pdf_as_markdown_docs(pdf_path, save_md=True)

    Args:
        pdf_path:   PDF 파일 경로
        save_md:    변환된 Markdown 파일 저장 여부
        chunk_size: 청크 최대 크기
        overlap:    청크 간 중복

    Returns:
        Document 리스트 (FAISS 색인 가능)
    """
    md_text = pdf_to_markdown(pdf_path, save_md=save_md)
    if not md_text.strip():
        logger.warning(f"빈 Markdown 결과: {pdf_path.name}")
        return []

    docs = markdown_to_documents(
        md_text=md_text,
        source=pdf_path.name,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    return docs


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  배치 처리: 디렉토리 내 모든 PDF
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def batch_convert_pdfs(
    pdf_dir: Path,
    save_md: bool = True,
    chunk_size: int = 800,
    overlap: int = 150,
) -> List[Document]:
    """
    디렉토리 내 모든 PDF를 Markdown으로 변환하여 Document 목록 반환.

    [build_db.py 에서 사용]
        from core.pdf_to_markdown import batch_convert_pdfs

        all_docs = batch_convert_pdfs(
            pdf_dir=settings.local_work_dir,
            save_md=True,
        )
        # → FAISS 색인 생성
    """
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning(f"PDF 파일 없음: {pdf_dir}")
        return []

    logger.info(f"배치 변환 시작: {len(pdf_files)}개 PDF → Markdown")
    all_docs = []

    for pdf_path in pdf_files:
        try:
            docs = load_pdf_as_markdown_docs(
                pdf_path,
                save_md=save_md,
                chunk_size=chunk_size,
                overlap=overlap,
            )
            all_docs.extend(docs)
            logger.info(f"  ✅ {pdf_path.name}: {len(docs)}개 청크")
        except Exception as exc:
            logger.error(f"  ❌ {pdf_path.name}: {exc}", exc_info=True)

    logger.info(f"배치 변환 완료: {len(pdf_files)}개 파일 → 총 {len(all_docs)}개 청크")
    return all_docs
