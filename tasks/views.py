from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import ListForm, TaskForm
from .models import List, Task


def _user_lists(user):
    return List.objects.filter(users=user)


@login_required
def index(request):
    form = ListForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        task_list = form.save()
        task_list.users.add(request.user)
        return redirect('list-detail', pk=task_list.pk)

    open_tasks = Q(tasks__completed=False) & (
        Q(tasks__expires_at__isnull=True) | Q(tasks__expires_at__gt=timezone.now())
    )
    lists = _user_lists(request.user).annotate(open_count=Count('tasks', filter=open_tasks))
    return render(request, 'tasks/index.html', {'lists': lists, 'form': form})


@login_required
def list_detail(request, pk):
    task_list = get_object_or_404(_user_lists(request.user), pk=pk)
    form = TaskForm(request.POST or None, task_list=task_list)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('list-detail', pk=pk)

    tasks = task_list.tasks.filter(parent__isnull=True).prefetch_related(
        Prefetch('subtasks', queryset=Task.objects.order_by('created_at', 'id'))
    )
    return render(request, 'tasks/list_detail.html', {'list': task_list, 'tasks': tasks, 'form': form})


@login_required
@require_POST
def list_delete(request, pk):
    get_object_or_404(_user_lists(request.user), pk=pk).delete()
    return redirect('index')


@login_required
@require_POST
def task_toggle(request, pk):
    task = get_object_or_404(Task, pk=pk, list__users=request.user)
    task.completed = not task.completed
    task.save(update_fields=['completed'])
    return redirect('list-detail', pk=task.list_id)


@login_required
@require_POST
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk, list__users=request.user)
    list_id = task.list_id
    task.delete()
    return redirect('list-detail', pk=list_id)
