from django.urls import path
from . import views

app_name = "jobfeeds"
urlpatterns = [
    path("", views.home, name="home"),
    path("verify/send/", views.verify_send, name="verify_send"),
    path("verify/<str:token>/", views.verify, name="verify"),
]
