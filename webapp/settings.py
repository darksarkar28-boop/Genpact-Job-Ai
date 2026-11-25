"""Settings for a single-host deployment with a persistent data directory."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

DATA_DIR = Path(os.environ.get("JOBWEB_DATA_DIR", Path.home() / ".genpact-job-ai"))
DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
DEBUG = os.environ.get("JOBWEB_PRODUCTION") != "1"
SECRET_KEY = os.environ.get("JOBWEB_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set JOBWEB_SECRET_KEY to a persistent random secret.")
    SECRET_KEY = get_random_secret_key()
ALLOWED_HOSTS = [
    host.strip() for host in os.environ.get("JOBWEB_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "webapp",
    "campus",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "webapp.middleware.limit_import_size",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "webapp.urls"
WSGI_APPLICATION = "webapp.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": DATA_DIR / "profiles.sqlite3",
    "OPTIONS": {"timeout": 10},
}}
if os.environ.get("JOBWEB_TEST_DATABASE"):
    DATABASES["default"]["TEST"] = {"NAME": os.environ["JOBWEB_TEST_DATABASE"]}

CAMPUS_MAIL_ENABLED = bool(os.environ.get("CAMPUS_SMTP_HOST"))
if CAMPUS_MAIL_ENABLED:
    EMAIL_HOST = os.environ["CAMPUS_SMTP_HOST"]
    EMAIL_PORT = int(os.environ.get("CAMPUS_SMTP_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("CAMPUS_SMTP_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("CAMPUS_SMTP_PASSWORD", "")
    EMAIL_USE_TLS = os.environ.get("CAMPUS_SMTP_TLS", "1") == "1"
    EMAIL_TIMEOUT = 10
    DEFAULT_FROM_EMAIL = os.environ.get("CAMPUS_FROM_EMAIL", "campus@localhost")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "workspace"
LOGOUT_REDIRECT_URL = "login"
STATIC_URL = "/static/"
STATIC_ROOT = DATA_DIR / "static"
USE_TZ = True
TIME_ZONE = "UTC"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT = not DEBUG
if not DEBUG and os.environ.get("JOBWEB_TRUST_PROXY_TLS") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_REFERRER_POLICY = "same-origin"
# Final review accepts a CV and a cover letter in the same request.
DATA_UPLOAD_MAX_NUMBER_FILES = 2
FILE_UPLOAD_MAX_MEMORY_SIZE = 512 * 1024

# Document exports are deliberately separate from tracked workspace files.
DOCUMENT_EXPORT_ROOT = DATA_DIR / "exports"
DOCUMENT_EXPORT_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
DOCUMENT_WORKER_RUNTIME = os.environ.get("JOBWEB_DOCUMENT_WORKER_RUNTIME", "docker")
DOCUMENT_WORKER_IMAGE = os.environ.get(
    "JOBWEB_DOCUMENT_WORKER_IMAGE", "genpact-job-ai-document-worker:local"
)
DOCUMENT_WORKER_TIMEOUT = int(os.environ.get("JOBWEB_DOCUMENT_WORKER_TIMEOUT", "90"))
DOCUMENT_WORKER_MEMORY = os.environ.get("JOBWEB_DOCUMENT_WORKER_MEMORY", "768m")
DOCUMENT_WORKER_CPUS = os.environ.get("JOBWEB_DOCUMENT_WORKER_CPUS", "1.0")

# Model telemetry contains only configured route labels, usage counts and estimated cost.
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "handlers": {"model_usage": {"class": "logging.StreamHandler"}},
    "loggers": {"jobai.model_usage": {"handlers": ["model_usage"], "level": "INFO", "propagate": False}},
}
