from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('lists/<int:pk>/', views.list_detail, name='list-detail'),
    path('lists/<int:pk>/delete/', views.list_delete, name='list-delete'),
    path('lists/<int:pk>/rename/', views.list_rename, name='list-rename'),
    path('lists/<int:pk>/merge/', views.list_merge, name='list-merge'),
    path('lists/<int:pk>/members/', views.member_add, name='member-add'),
    path('lists/<int:pk>/members/<int:user_id>/remove/', views.member_remove, name='member-remove'),
    path('tasks/<int:pk>/toggle/', views.task_toggle, name='task-toggle'),
    path('tasks/<int:pk>/claim/', views.task_claim, name='task-claim'),
    path('tasks/<int:pk>/delete/', views.task_delete, name='task-delete'),
]
