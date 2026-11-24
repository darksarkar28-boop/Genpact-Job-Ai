from django.urls import path

from . import views

app_name = "campus"
urlpatterns = [
    path("", views.home, name="home"),
    path("invite/<str:token>/", views.open_invitation, name="invite"),
    path("join/", views.join, name="join"),
    path("o/<slug:slug>/", views.dashboard, name="dashboard"),
    path("o/<slug:slug>/cohorts/", views.create_cohort, name="create_cohort"),
    path("o/<slug:slug>/invitations/", views.invite_members, name="invite_members"),
    path("o/<slug:slug>/invitations/<uuid:invite_id>/revoke/", views.revoke, name="revoke"),
    path("o/<slug:slug>/members/<int:member_id>/", views.manage_member, name="manage_member"),
    path("o/<slug:slug>/progress/", views.progress, name="progress"),
    path("o/<slug:slug>/resumes/", views.share, name="share"),
    path("o/<slug:slug>/reviews/<int:review_id>/", views.review_detail, name="review"),
    path("o/<slug:slug>/reviews/<int:review_id>/withdraw/", views.withdraw, name="withdraw"),
    path("o/<slug:slug>/reviews/<int:review_id>/v/<int:revision_id>/", views.download, name="download"),
    path("o/<slug:slug>/reviews/<int:review_id>/v/<int:revision_id>/comments/", views.comment, name="comment"),
]
