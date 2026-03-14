#!/bin/bash
# GitHub 업로드 스크립트
# 사용법: ./upload_to_github.sh YOUR_GITHUB_USERNAME

USERNAME=$1
REPO_NAME="polymarket-ai-bot"

if [ -z "$USERNAME" ]; then
    echo "❌ GitHub 사용자명을 입력하세요"
    echo "예: ./upload_to_github.sh hang4ai"
    exit 1
fi

cd /root/.openclaw/workspace/polymarket_bot

# Git 초기화
git init

# 모든 파일 추가
git add .

# 커밋
git commit -m "Initial commit: Polymarket AI Trading Bot

- Multi-agent real-time trading system
- Technical, sentiment, whale analysis agents
- Consensus-based decision making
- Risk management with daily loss limits"

# main 브랜치로 변경
git branch -M main

# 원격 저장소 추가
git remote add origin "https://github.com/$USERNAME/$REPO_NAME.git"

echo ""
echo "✅ 로컬 준비 완료!"
echo ""
echo "이제 다음 명령어를 실행하세요:"
echo "  git push -u origin main"
echo ""
echo "GitHub 인증이 필요합니다:"
echo "  - 사용자명: $USERNAME"
echo "  - 비밀번호 대신 Personal Access Token 사용"
echo "  - 토큰은 https://github.com/settings/tokens 에서 생성"
