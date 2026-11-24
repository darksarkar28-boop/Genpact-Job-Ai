from django.contrib.auth import views as auth
from django.urls import path

from . import board, mailbox, routed_views, views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/signup/", views.signup, name="signup"),
    path("accounts/login/", auth.LoginView.as_view(template_name="webapp/login.html"), name="login"),
    path("accounts/logout/", auth.LogoutView.as_view(), name="logout"),
    path("workbench/", routed_views.workbench, name="workbench"),
    path("profile/", views.workspace, name="workspace"),
    path("applications/", board.application_board, name="application_board"),
    path("applications/mail/<str:provider>/connect/", mailbox.connect, name="mailbox_connect"),
    path("applications/mail/<str:provider>/callback/", mailbox.callback, name="mailbox_callback"),
    path("applications/mail/<str:provider>/disconnect/", mailbox.disconnect, name="mailbox_disconnect"),
    path("applications/mail/<str:provider>/sync/", mailbox.sync, name="mailbox_sync"),
    path("applications/mail/signals/<int:signal_id>/decision/", mailbox.decide, name="mailbox_decide"),
    path("applications/revision/", board.application_board_revision, name="application_board_revision"),
    path("applications/new/", board.application_create, name="application_create"),
    path("applications/import/", board.application_import, name="application_import"),
    path("applications/<int:application_id>/move/", board.application_move, name="application_move"),
    path("profile/import/", views.import_profile, name="import"),
    path("research/company/", views.company_research, name="company-research"),
    path("profile/edit/", views.edit_profile, name="edit"),
    path("applications/review/", views.application_review, name="application_review"),
]
