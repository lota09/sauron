'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : Errors.py
  - Description      : Describe Runtime Errors
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

# -*- coding: utf-8 -*-

def InfoCollect(e):
    import traceback
    import os

    tb = traceback.extract_tb(e.__traceback__)  # 스택 트레이스 추출
    last_trace = tb[-1]  # 가장 마지막 예외 발생 위치

    # 파일 경로에서 모듈(파일) 이름만 추출
    module_name = os.path.basename(last_trace.filename)  
    line_number = last_trace.lineno  
    function_name = last_trace.name

    debug_message = (
        f"📜 Module: {module_name}, line {line_number}\n"
        f"🔧 Function: {function_name}\n"
        f"❌ Exception: {type(e).__name__} - {e}"
    )
    return debug_message


class FetchError(Exception):
    def __init__(self, message="Fetch Failed. Reason Unknown."):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f"{self.__class__.__name__}: {self.message}"
      
class SummaryError(Exception):
    def __init__(self, message="Summary Failed. Reason Unknown."):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f"{self.__class__.__name__}: {self.message}"
    
class KakaoTalkError(Exception):
    def __init__(self, message="카카오톡 전송 실패. 이유 : 알 수 없음."):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f"{self.__class__.__name__}: {self.message}"
    
class DiscordError(Exception):
    def __init__(self, message="디스코드 메시지 전송 실패. 이유 : 알 수 없음."):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f"{self.__class__.__name__}: {self.message}"