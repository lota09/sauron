'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : ClovaSummary.py
  - Description      : Summary announcement contents
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.23 : Initial release
*******************************************************************'''

# -*- coding: utf-8 -*-
import http.client
import json
import os
from Errors import SummaryError

API_INFO_FILE = 'secrets/clovastudio-api-info.json'

class CompletionExecutor:
    def __init__(self, host, api_key, api_key_primary_val, request_id, url_key):
        self._host = host
        self._api_key = api_key
        self._api_key_primary_val = api_key_primary_val
        self._request_id = request_id
        self._url_key = url_key

    def _send_request(self, completion_request):
        headers = {
            'Content-Type': 'application/json; charset=utf-8',
            'X-NCP-CLOVASTUDIO-API-KEY': self._api_key,
            'X-NCP-APIGW-API-KEY': self._api_key_primary_val,
            'X-NCP-CLOVASTUDIO-REQUEST-ID': self._request_id
        }

        conn = http.client.HTTPSConnection(self._host)
        conn.request('POST', f'/testapp/v1/api-tools/summarization/v2/{self._url_key}', 
                     json.dumps(completion_request), headers)
        response = conn.getresponse()
        result = json.loads(response.read().decode(encoding='utf-8'))
        conn.close()
        return result

    def execute(self, completion_request):
        res = self._send_request(completion_request)
        if res['status']['code'] == '20000':
            #print("Input Tokens:", res['result']['inputTokens'])
            return res['result']['text']
        else:
            raise SummaryError(f"Error Code : {res['status']['code']}")
            #return 'Error'


def LoadSecrets(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Configuration file not found: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as file:
        try:
            api_info = json.load(file)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format in {file_path}: {e}")

    required_keys = {'api_key', 'api_key_primary_val', 'request_id'}
    if not required_keys.issubset(api_info.keys()):
        raise ValueError(f"Missing required keys in JSON file: {required_keys - api_info.keys()}")

    return api_info


def Summarize(content):
    """Summarize the given content using Clova Studio API."""
    # Load API keys and configuration
    api_info = LoadSecrets(API_INFO_FILE)

    # Create an instance of CompletionExecutor
    completion_executor = CompletionExecutor(
        host='clovastudio.apigw.ntruss.com',
        api_key=api_info['api_key'],
        api_key_primary_val=api_info['api_key_primary_val'],
        request_id=api_info['request_id'],
        url_key=api_info['url_key']
    )

    # Prepare the request data
    request_data = {
        "texts": [content],
        "segMinSize": 300,
        "includeAiFilters": True,
        "autoSentenceSplitter": True,
        "segCount": -1,
        "segMaxSize": 1000
    }

    # Execute the API call and return the response
    return completion_executor.execute(request_data)


if __name__ == '__main__':
    # Example content to summarize
    example_content = "Here is a lengthy text that needs to be summarized."

    # Get summary from ClovaSummary function
    summary = Summarize(example_content)
    print("Summary:", summary)
