from django.contrib.auth import views as auth
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/signup/", views.signup, name="signup"),
    path("accounts/login/", auth.LoginView.as_view(template_name="webapp/login.html"), name="login"),
    path("accounts/logout/", auth.LogoutView.as_view(), name="logout"),
    path("profile/", views.workspace, name="workspace"),
    path("profile/import/", views.import_profile, name="import"),
    path("profile/edit/", views.edit_profile, name="edit"),
    path("documents/", views.document_center, name="documents"),
    path("documents/queue/", views.queue_document, name="document_queue"),
    path("documents/<int:job_id>/status/", views.document_status, name="document_status"),
    path("documents/<int:job_id>/retry/", views.retry_document, name="document_retry"),
    path("documents/<int:job_id>/download/", views.document_download, name="document_download"),
]
