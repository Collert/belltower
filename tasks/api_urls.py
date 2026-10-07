from rest_framework.routers import DefaultRouter

from . import api

router = DefaultRouter()
router.register('lists', api.ListViewSet, basename='list')
router.register('tasks', api.TaskViewSet, basename='task')
router.register('users', api.UserViewSet, basename='user')

urlpatterns = router.urls
