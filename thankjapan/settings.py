from pathlib import Path
import os
import dj_database_url
import cloudinary
import cloudinary_storage
import paypalrestsdk

BASE_DIR = Path(__file__).resolve().parent.parent

# settings.py


LANGUAGE_CODE = 'en'


LANGUAGES = [
    
    ('en', 'English'),
    ('ja', '日本語'),
    ('es', 'Español'),
    ('fr', 'Français'),
    ('it', 'Italiano'),
    ('pt', 'Português'),
    ('zh-hant', '中文（繁体字）'),
    ('ko', '한국어'),
    ('es-es', 'Español (España)'),
    ('de', 'Deutsch'),
    ('th', 'ไทย'),
    ('pt-br', 'Português (Brasil)'),
    ('es-mx', 'Español (México)'),
    ('en-in', 'English (India)'),
]


LOCALE_PATHS = [
    os.path.join(BASE_DIR, 'locale'),
]

USE_I18N = True    
USE_L10N = True    
USE_TZ = True
TIME_ZONE = 'Asia/Tokyo'


GA_TRACKING_ID = os.environ.get('GA_TRACKING_ID', '')

# Load environment variables
SECRET_KEY = os.environ.get('SECRET_KEY')
DEBUG = True
ALLOWED_HOSTS = [
    'www.thankjapan.com',
    'thankjapan.com',
    'thankjapan-4c187061757b.herokuapp.com'
]

# Database
DATABASES = {
    'default': dj_database_url.config(conn_max_age=600, ssl_require=True)
}

# PayPal
PAYPAL_MODE = os.getenv('PAYPAL_MODE', 'live')
PAYPAL_CLIENT_ID = os.getenv('PAYPAL_CLIENT_ID')
PAYPAL_CLIENT_SECRET = os.getenv('PAYPAL_CLIENT_SECRET')
paypalrestsdk.configure({
    "mode": PAYPAL_MODE,
    "client_id": PAYPAL_CLIENT_ID,
    "client_secret": PAYPAL_CLIENT_SECRET
})

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'

# もし static_jp フォルダが存在する場合のみ追加
STATICFILES_DIRS = []
if (BASE_DIR / 'static_jp').exists():
    STATICFILES_DIRS.append(BASE_DIR / 'static_jp')

# Herokuで collectstatic が通るように必ず STATIC_ROOT を設定
STATIC_ROOT = BASE_DIR / 'staticfiles'

# WhiteNoiseで静的ファイルを配信
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Media files (Cloudinary)
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME'),
    'API_KEY': os.environ.get('CLOUDINARY_API_KEY'),
    'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET'),
}

# Installed apps
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'thank_japan_app.apps.ThankJapanAppConfig',
    'cloudinary',
    'cloudinary_storage',
    'payment',
    'corsheaders',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'thank_japan_app.middleware.RedirectToWwwMiddleware',
    'corsheaders.middleware.CorsMiddleware',
]

# Templates
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'thank_japan_app.context_processors.google_analytics',
            ],
        },
    },
]

WSGI_APPLICATION = 'thankjapan.wsgi.application'
# URL configuration
ROOT_URLCONF = 'thankjapan.urls'


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Email settings
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = os.environ.get('EMAIL_PORT', 587)
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_PASSWORD')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL')

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS
CORS_ALLOWED_ORIGINS = [
    "https://thankjapan-4c187061757b.herokuapp.com",
    "https://www.thankjapan.com",
]

# Security
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
