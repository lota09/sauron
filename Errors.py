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
        return f"FetchError: {self.message}"
      
      
class SummaryError(Exception):
    def __init__(self, message="Summary Failed. Reason Unknown."):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f"FetchError: {self.message}"