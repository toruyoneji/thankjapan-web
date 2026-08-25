import base64
import json
import os


def load_b64_json_env(var_name):
    """Base64エンコードされたJSON文字列を環境変数から読み込んでdictを返す"""
    encoded = os.environ.get(var_name)
    if not encoded:
        return None

    decoded_json = base64.b64decode(encoded).decode('utf-8')
    return json.loads(decoded_json)
