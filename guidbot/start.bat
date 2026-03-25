@echo off
chcp 65001 > nul
title 좋은문화병원 AI 가이드봇

echo.
echo  ╔═══════════════════════════════════════════╗
echo  ║   🏥  좋은문화병원 AI 가이드봇 시작      ║
echo  ╚═══════════════════════════════════════════╝
echo.

REM ── 가상환경 활성화 ────────────────────────────────────────
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo  ✅ 가상환경 활성화 완료
) else (
    echo  ⚠️  가상환경 없음 - 시스템 Python 사용
)

echo.

REM ── Step 1: 모델 사전 워밍업 (최초 실행 or 모델 업데이트 후) ──
echo  [1/2] AI 모델 캐시 확인 중...
python warmup.py
if %ERRORLEVEL% NEQ 0 (
    echo  ⚠️  워밍업 실패 (무시하고 계속 진행)
)

echo.

REM ── Step 2: Streamlit 앱 실행 ──────────────────────────────
echo  [2/2] 가이드봇 시작 중...
echo  ─────────────────────────────────────────────
echo  접속 주소: http://localhost:8502
echo  종료: Ctrl+C
echo  ─────────────────────────────────────────────
echo.

streamlit run main.py ^
    --server.port 8502 ^
    --server.address 0.0.0.0 ^
    --server.headless true ^
    --browser.gatherUsageStats false ^
    --server.runOnSave false

pause