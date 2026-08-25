import base64
import json
import os

from firebase_admin import credentials


def get_firebase_credentials():
    """Base64エンコードされた環境変数からFirebaseサービスアカウント認証情報を読み込む"""
    encoded = os.environ.get('FIREBASE_SERVICE_ACCOUNT_B64')
    if not encoded:
        return None

    decoded_json = base64.b64decode(encoded).decode('utf-8')
    firebase_dict = json.loads(decoded_json)
    return credentials.Certificate(firebase_dict)
