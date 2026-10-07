from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', views.account, name='account'),
    path('token/', views.regenerate_token, name='regenerate-token'),
    path('keys/<int:pk>/revoke/', views.revoke_api_key, name='revoke-api-key'),
]
