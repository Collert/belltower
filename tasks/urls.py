from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('lists/<int:pk>/', views.list_detail, name='list-detail'),
    path('lists/<int:pk>/delete/', views.list_delete, name='list-delete'),
    path('tasks/<int:pk>/toggle/', views.task_toggle, name='task-toggle'),
    path('tasks/<int:pk>/delete/', views.task_delete, name='task-delete'),
]
