from firebase_admin import credentials

from .env_utils import load_b64_json_env


def get_firebase_credentials():
    """Base64エンコードされた環境変数からFirebaseサービスアカウント認証情報を読み込む"""
    firebase_dict = load_b64_json_env('FIREBASE_SERVICE_ACCOUNT_B64')
    if not firebase_dict:
        return None

    return credentials.Certificate(firebase_dict)
