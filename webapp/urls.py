from django.contrib.auth import views as auth
from django.urls import include, path

from . import views, routed_views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/signup/", views.signup, name="signup"),
    path("accounts/login/", auth.LoginView.as_view(template_name="webapp/login.html"), name="login"),
    path("accounts/logout/", auth.LogoutView.as_view(), name="logout"),
    path("workbench/", routed_views.workbench, name="workbench"),
    path("jobs/", include("jobfeeds.urls")),
    path("campus/", include("campus.urls")),
    path("profile/", views.workspace, name="workspace"),
    path("profile/import/", views.import_profile, name="import"),
    path("research/company/", views.company_research, name="company-research"),
    path("profile/edit/", views.edit_profile, name="edit"),
    path("applications/review/", views.application_review, name="application_review"),
]
