#!/bin/bash

# JSON 파일 경로
JSON_FILE="kakao-api-info.json"

# REST_API_KEY 가져오기
REST_API_KEY=$(jq -r '.REST_API_KEY' "$JSON_FILE")
if [[ -z "$REST_API_KEY" || "$REST_API_KEY" == "null" ]]; then
    echo "Error: REST_API_KEY is not set in $JSON_FILE."
    exit 1
fi

# 설정
REDIRECT_URI="http://localhost:8080"
AUTHORIZE_URL="https://kauth.kakao.com/oauth/authorize?response_type=code&client_id=${REST_API_KEY}&redirect_uri=${REDIRECT_URI}"
TOKEN_URL="https://kauth.kakao.com/oauth/token"  # 카카오 토큰 요청 URL

echo "Opening browser for user authentication..."
xdg-open "$AUTHORIZE_URL"  # Linux

# 타임아웃 설정
TIMEOUT=3

echo "Starting temporary server to capture authentication code (timeout in $TIMEOUT seconds)..."
AUTHORIZE_CODE=""

# netcat으로 임시 서버 시작

# netcat 요청 처리
REQ=$(timeout "$TIMEOUT" nc -l -p 8080)
AUTHORIZE_CODE=$(echo "$REQ" | sed -n 's/.*code=\([^ ]*\).*/\1/p')

# 대기
wait

if [[ -z "$AUTHORIZE_CODE" ]]; then
    echo "Error: No authentication code received within the timeout period."
    exit 1
fi

#HTML 페이지에 표시할 내용
echo -e "HTTP/1.1 200 OK\nContent-Type: text/html\n\n<h1>Authentication Successful!</h1><p>You can close this window.</p>" &

#refresh_token 업데이트

# POST 요청 보내기
RESPONSE=$(curl -s -X POST "${TOKEN_URL}" \
    -H "Content-Type: application/x-www-form-urlencoded;charset=utf-8" \
    -d "grant_type=authorization_code" \
    -d "client_id=${REST_API_KEY}" \
    --data-urlencode "redirect_uri=${REDIRECT_URI}" \
    -d "code=${AUTHORIZE_CODE}")

REFRESH_TOKEN=$(echo "$RESPONSE" | jq -r '.refresh_token')

# AUTH_CODE 업데이트
jq --arg refresh_token "$REFRESH_TOKEN" '.REFRESH_TOKEN = $refresh_token' "$JSON_FILE" > tmp.json && mv tmp.json "$JSON_FILE"
echo -e "\nRefresh token saved to $JSON_FILE."