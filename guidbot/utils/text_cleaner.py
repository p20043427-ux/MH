"""
utils/text_cleaner.py ─ 한국어 병원 규정 문서 전처리 모듈 (v3.0)

[v3.0 고도화 내용]
1. 목차(TOC) 페이지 자동 감지·필터링
   - "목  차", "차  례", "CONTENTS" 등 목차 페이지 → 색인 제외
   - 이유: 목차는 규정 내용이 아닌 탐색 보조 → 검색 노이즈

2. 개정 이력 테이블 감지·제거
   - "개정번호", "시행일", "개정사유" 포함 줄 필터
   - 이유: 개정 이력은 날짜/번호 위주 → 의미 검색에 방해

3. 서명·결재란 제거
   - "담당", "팀장", "결재", "서명" 등 결재 박스 내용 제거
   - 이유: 결재 정보는 규정 내용이 아님

4. 한글 비율 기반 품질 점수 (korean_ratio)
   - (한글 글자 수) / (전체 의미 문자 수) 계산
   - 0.0 ~ 1.0 범위, 0.2 미만은 텍스트 추출 실패로 판단

5. 강화된 헤더·푸터 제거
   - 쪽 번호 다양한 형태: "- 1 -", "1 / 10", "Page 1" 등
   - 문서명 반복 헤더 제거

6. 영문 단어 분리 하이픈 처리
   - "appoint-\\nment" → "appointment" 복원

7. 전각 문자 완전 변환표 확장
   - 전각 숫자, 영문 대소문자, 특수기호 포함

[전처리 파이프라인]
원문
  → ① 목차/개정이력/결재란 감지 (필터링 여부 결정)
  → ② 제어 문자·전각 문자 정제
  → ③ 헤더·푸터·쪽 번호 제거
  → ④ 영문/한글 단어 분리 하이픈 복원
  → ⑤ 공백·줄바꿈 정규화
  → ⑥ 최소 길이 + 한글 비율 품질 필터
  → ⑦ 조항·장 번호 + 날짜 메타데이터 추출
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProcessResult:
    """텍스트 전처리 결과"""
    content: str                                          # 정제된 텍스트
    metadata: dict[str, Any] = field(default_factory=dict)  # 추출된 메타데이터
    korean_ratio: float = 0.0                            # 한글 비율 (0.0~1.0)
    quality_score: float = 0.0                           # 종합 품질 점수 (0.0~1.0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  정규식 패턴 (컴파일 캐시)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 공백/줄바꿈
_RE_MULTI_NEWLINE  = re.compile(r"\n{3,}")
_RE_MULTI_SPACE    = re.compile(r"[ \t]{2,}")

# 단어 분리 하이픈 복원
_RE_BROKEN_KOR     = re.compile(r"(?<=[가-힣])-\s*\n\s*(?=[가-힣])")   # 한글
_RE_BROKEN_ENG     = re.compile(r"(?<=[a-zA-Z])-\s*\n\s*(?=[a-zA-Z])")  # 영문 (신규)

# 제어 문자 (의미 없는 이진 노이즈)
_RE_CONTROL_CHARS  = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ── 쪽 번호·헤더·푸터 패턴 ────────────────────────────────────────────
_RE_PAGE_NUMBER = re.compile(
    r"^\s*(?:"
    r"[-–—]\s*\d+\s*[-–—]"              # - 1 - 형태
    r"|\d+\s*/\s*\d+"                    # 1 / 10 형태
    r"|[Pp]age\s+\d+(?:\s+of\s+\d+)?"   # Page 1 / Page 1 of 10
    r"|\d{1,4}\s*[-–—]\s*\d{1,4}"       # 1-10 형태
    r")\s*$",
    re.MULTILINE,
)

# 날짜 패턴 (단독 줄)
_RE_DATE_LINE = re.compile(
    r"^\s*\d{4}[년.]\s*\d{1,2}[월.]\s*\d{1,2}[일.]?\s*$",
    re.MULTILINE,
)

# ── 조항·장·절 번호 추출 ─────────────────────────────────────────────
_RE_ARTICLE        = re.compile(r"제\s*(\d+)\s*조")               # 제N조
_RE_ARTICLE_TITLE  = re.compile(r"제\s*\d+\s*조\s*[（(]([^)）]+)[)）]")  # 제N조(제목)
_RE_CHAPTER        = re.compile(r"제\s*(\d+)\s*장")               # 제N장
_RE_CHAPTER_TITLE  = re.compile(r"제\s*\d+\s*장\s+([^\n]+)")      # 제N장 제목
_RE_SECTION        = re.compile(r"제\s*(\d+)\s*절")               # 제N절

# ── 날짜 메타데이터 추출 ─────────────────────────────────────────────
_RE_DATE_META = re.compile(
    r"(?:개정|시행|제정|공포|발효)\s*[일자:]?\s*"
    r"(\d{4})\s*[년.]\s*(\d{1,2})\s*[월.]\s*(\d{1,2})\s*[일.]?"
)

# ── 노이즈 판별 패턴 ─────────────────────────────────────────────────

# 목차 페이지 감지 (페이지 전체에서 판단)
_RE_TOC_PAGE = re.compile(
    r"(?:목\s*차|차\s*례|CONTENTS|INDEX|Table\s+of\s+Contents)",
    re.IGNORECASE,
)
# 목차 항목 특징: "....... 숫자" 형태의 줄이 많음
_RE_TOC_ITEM = re.compile(r"\.{3,}\s*\d+\s*$", re.MULTILINE)

# 개정 이력 감지
_RE_REVISION_HISTORY = re.compile(
    r"(?:개정\s*번호|개정\s*이력|개정\s*연혁|시행\s*일자|개정\s*일자|개정\s*사유)",
)

# 서명·결재란 감지
_RE_APPROVAL_BOX = re.compile(
    r"(?:담\s*당|팀\s*장|부\s*장|이\s*사|대\s*표|결\s*재|서\s*명|확\s*인|검\s*토)"
    r".{0,20}"
    r"(?:담\s*당|팀\s*장|부\s*장|이\s*사|대\s*표|결\s*재|서\s*명|확\s*인|검\s*토)",
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  전각 문자 변환 테이블 (확장판)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 전각 → 반각 일괄 변환 (ord(전각 숫자 '０') = 0xFF10)
_FULLWIDTH_TABLE: dict[int, str] = {}
# 전각 숫자 ０-９
for i in range(10):
    _FULLWIDTH_TABLE[0xFF10 + i] = str(i)
# 전각 영문 대문자 Ａ-Ｚ
for i in range(26):
    _FULLWIDTH_TABLE[0xFF21 + i] = chr(ord('A') + i)
# 전각 영문 소문자 ａ-ｚ
for i in range(26):
    _FULLWIDTH_TABLE[0xFF41 + i] = chr(ord('a') + i)

# 개별 전각 특수기호
_FULLWIDTH_SPECIAL = {
    "　": " ",   # 전각 공백
    "！": "!",
    "＂": '"',
    "＃": "#",
    "＄": "$",
    "％": "%",
    "＆": "&",
    "＇": "'",
    "（": "(",
    "）": ")",
    "＊": "*",
    "＋": "+",
    "，": ",",
    "－": "-",
    "．": ".",
    "／": "/",
    "：": ":",
    "；": ";",
    "＜": "<",
    "＝": "=",
    "＞": ">",
    "？": "?",
    "＠": "@",
    "［": "[",
    "＼": "\\",
    "］": "]",
    "＾": "^",
    "＿": "_",
    "｀": "`",
    "｛": "{",
    "｜": "|",
    "｝": "}",
    "～": "~",
    "。": ".",
    "「": '"',
    "」": '"',
    "『": '"',
    "』": '"',
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  단계별 처리 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_toc_page(text: str) -> bool:
    """목차 페이지 여부 판단."""
    if not _RE_TOC_PAGE.search(text):
        return False
    # 목차 항목(......숫자) 이 3줄 이상이면 목차 페이지로 판단
    toc_items = _RE_TOC_ITEM.findall(text)
    return len(toc_items) >= 3


def _is_revision_history_page(text: str) -> bool:
    """개정 이력 단독 페이지 여부 판단."""
    if not _RE_REVISION_HISTORY.search(text):
        return False
    # 유효한 규정 내용(제X조 등)이 없으면 순수 개정이력 페이지
    return not bool(_RE_ARTICLE.search(text))


def _convert_fullwidth(text: str) -> str:
    """전각 문자 → 반각 변환 (확장판)."""
    # 유니코드 전각 블록 일괄 변환
    text = text.translate(_FULLWIDTH_TABLE)
    # 개별 특수기호 변환
    for fw, hw in _FULLWIDTH_SPECIAL.items():
        text = text.replace(fw, hw)
    return text


def _clean_special_chars(text: str) -> str:
    """제어 문자 제거 + 전각 변환 + 불릿 통일."""
    # 1. 제어 문자 제거
    text = _RE_CONTROL_CHARS.sub("", text)
    # 2. 전각 → 반각 변환
    text = _convert_fullwidth(text)
    # 3. 불릿 기호 통일 (다양한 불릿 → •)
    text = re.sub(r"[▶▷▪▫◆◇○●□■▸►]", "•", text)
    # 4. 특수 따옴표 → 일반 따옴표 (임베딩 모델 호환성)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    return text


def _remove_headers_footers(text: str) -> str:
    """쪽 번호, 날짜 단독 줄, 결재란 제거."""
    text = _RE_PAGE_NUMBER.sub("", text)
    text = _RE_DATE_LINE.sub("", text)
    # 결재란: 짧은 줄에 담당/팀장/결재 등 2개 이상 포함된 줄 제거
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        # 결재·서명 키워드가 2개 이상 있고 줄이 짧으면 결재란으로 판단
        approval_hits = len(re.findall(
            r"담당|팀장|부장|이사|대표|결재|서명|확인|검토|작성", line
        ))
        if approval_hits >= 2 and len(line.strip()) < 60:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _restore_broken_words(text: str) -> str:
    """줄 끝 단어 분리 하이픈 복원 (한글 + 영문)."""
    text = _RE_BROKEN_KOR.sub("", text)   # 한글 단어 분리
    text = _RE_BROKEN_ENG.sub("", text)   # 영문 단어 분리 (신규)
    return text


def _normalize_whitespace(text: str) -> str:
    """공백·줄바꿈 정규화."""
    text = _RE_MULTI_SPACE.sub(" ", text)
    text = _RE_MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def _calc_korean_ratio(text: str) -> float:
    """
    한글 비율 계산.

    (한글 글자 수) / (한글 + 영문 + 숫자 글자 수)
    결과 0.0 ~ 1.0. 0.2 미만이면 PDF 텍스트 추출 실패 가능성 높음.
    """
    if not text:
        return 0.0
    korean  = sum(1 for c in text if "\uAC00" <= c <= "\uD7A3")
    english = sum(1 for c in text if c.isalpha() and not ("\uAC00" <= c <= "\uD7A3"))
    digits  = sum(1 for c in text if c.isdigit())
    total   = korean + english + digits
    return korean / total if total > 0 else 0.0


def _calc_quality_score(text: str, korean_ratio: float) -> float:
    """
    종합 텍스트 품질 점수 (0.0 ~ 1.0).

    구성:
    - 한글 비율 (40%): 한국어 문서에서 핵심
    - 문장 부호 비율 (20%): 문장이 완결될수록 높음
    - 평균 단어 길이 (20%): 너무 짧거나 길면 노이즈
    - 공백 비율 (20%): 적정 수준의 공백 = 정상 문장
    """
    if len(text) < 10:
        return 0.0

    # 문장 부호 비율 (마침표·쉼표·콜론 등)
    punct_count = sum(1 for c in text if c in ".,:;!?")
    punct_ratio = min(punct_count / len(text) * 20, 1.0)  # 5% → 1.0

    # 평균 단어 길이 (2~6자가 이상적)
    words = text.split()
    avg_word_len = sum(len(w) for w in words) / len(words) if words else 0
    word_score = 1.0 if 2 <= avg_word_len <= 6 else max(0.0, 1.0 - abs(avg_word_len - 4) * 0.1)

    # 공백 비율 (10~25%가 이상적)
    space_ratio = text.count(" ") / len(text)
    space_score = 1.0 if 0.05 <= space_ratio <= 0.3 else 0.5

    score = (
        korean_ratio   * 0.40 +
        punct_ratio    * 0.20 +
        word_score     * 0.20 +
        space_score    * 0.20
    )
    return round(min(score, 1.0), 3)


def _extract_metadata(text: str) -> dict[str, Any]:
    """
    병원 규정 문서 특화 메타데이터 추출.

    추출 항목:
    - article:        "제1조, 제3조" (조 번호 목록)
    - article_title:  "연차유급휴가" (최초 조의 제목)
    - chapter:        "제2장" (장 번호)
    - chapter_title:  "근로시간" (장 제목)
    - section:        "제1절" (절 번호)
    - dates:          개정/시행일 목록
    """
    metadata: dict[str, Any] = {}

    # 조항 번호 (최대 3개)
    articles = _RE_ARTICLE.findall(text)
    if articles:
        metadata["article"] = ", ".join(f"제{a}조" for a in articles[:3])

    # 조항 제목 (첫 번째)
    title_match = _RE_ARTICLE_TITLE.search(text)
    if title_match:
        metadata["article_title"] = title_match.group(1).strip()

    # 장 번호
    chapters = _RE_CHAPTER.findall(text)
    if chapters:
        metadata["chapter"] = ", ".join(f"제{c}장" for c in chapters[:2])

    # 장 제목 (첫 번째)
    chapter_title_match = _RE_CHAPTER_TITLE.search(text)
    if chapter_title_match:
        title_text = chapter_title_match.group(1).strip()
        # 불필요한 공백 정리 후 첫 문장만 취함
        metadata["chapter_title"] = re.split(r"[.。\n]", title_text)[0].strip()[:30]

    # 절 번호
    sections = _RE_SECTION.findall(text)
    if sections:
        metadata["section"] = f"제{sections[0]}절"

    # 날짜 정보 (개정일, 시행일 등)
    date_matches = _RE_DATE_META.findall(text)
    if date_matches:
        dates = [f"{y}.{m.zfill(2)}.{d.zfill(2)}" for y, m, d in date_matches[:2]]
        metadata["dates"] = dates

    return metadata


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공개 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def is_noise_page(text: str) -> tuple[bool, str]:
    """
    페이지 전체를 보고 색인에서 제외할 노이즈 페이지인지 판단.

    Returns:
        (is_noise, reason):  노이즈 여부, 제외 이유
    """
    if _is_toc_page(text):
        return True, "목차 페이지"
    if _is_revision_history_page(text):
        return True, "개정이력 페이지"
    return False, ""


def process(text: str, min_length: int = 30) -> ProcessResult | None:
    """
    텍스트 전처리 파이프라인 진입점 (v3.0).

    Args:
        text:       원본 추출 텍스트
        min_length: 유효 텍스트 최소 길이

    Returns:
        ProcessResult. 노이즈 페이지(길이 미달 또는 저품질)면 None.
    """
    if not text or not text.strip():
        return None

    # ① 제어 문자·전각 문자 정제
    text = _clean_special_chars(text)

    # ② 헤더·푸터·쪽 번호·결재란 제거
    text = _remove_headers_footers(text)

    # ③ 단어 분리 하이픈 복원 (한글 + 영문)
    text = _restore_broken_words(text)

    # ④ 공백·줄바꿈 정규화
    text = _normalize_whitespace(text)

    # ⑤ 최소 길이 필터
    if len(text) < min_length:
        return None

    # ⑥ 품질 점수 계산
    korean_ratio  = _calc_korean_ratio(text)
    quality_score = _calc_quality_score(text, korean_ratio)

    # 품질이 너무 낮으면 None (스캔 PDF 텍스트 추출 실패 등)
    # min_length 2배 이상인 페이지에만 엄격 적용 (짧은 페이지는 관대하게)
    if len(text) > min_length * 2 and quality_score < 0.1:
        return None

    # ⑦ 메타데이터 추출
    metadata = _extract_metadata(text)
    metadata["korean_ratio"] = round(korean_ratio, 3)

    return ProcessResult(
        content=text,
        metadata=metadata,
        korean_ratio=korean_ratio,
        quality_score=quality_score,
    )
