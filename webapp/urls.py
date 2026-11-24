from django.contrib.auth import views as auth
from django.urls import path

from . import views, routed_views, career_views, interview_views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/signup/", views.signup, name="signup"),
    path("accounts/login/", auth.LoginView.as_view(template_name="webapp/login.html"), name="login"),
    path("accounts/logout/", auth.LogoutView.as_view(), name="logout"),
    path("workbench/", routed_views.workbench, name="workbench"),
    path("career/", career_views.career_planning, name="career-planning"),
    path("career/clear/", interview_views.clear_context, name="career-clear"),
    path("career/interviews/", interview_views.home, name="interview-home"),
    path("career/interviews/<int:pk>/", interview_views.detail, name="interview-detail"),
    path("career/interviews/<int:pk>/delete/", interview_views.delete, name="interview-delete"),
    path("career/interviews/<int:pk>/answers/<int:index>/download/", interview_views.download, name="interview-download"),
    path("career/interviews/<int:pk>/answers/<int:index>/erase/", interview_views.erase, name="interview-erase"),
    path("profile/", views.workspace, name="workspace"),
    path("profile/import/", views.import_profile, name="import"),
    path("research/company/", views.company_research, name="company-research"),
    path("profile/edit/", views.edit_profile, name="edit"),
    path("applications/review/", views.application_review, name="application_review"),
]
