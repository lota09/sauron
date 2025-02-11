'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : Errors.py
  - Description      : Describe Runtime Errors
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

# -*- coding: utf-8 -*-
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