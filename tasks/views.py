from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import ListForm, MemberForm, TaskForm
from .models import List, Task
from .permissions import check_task_update, require_admin


def _user_lists(user):
    return List.objects.filter(users=user)


@login_required
def index(request):
    form = ListForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        task_list = form.save()
        task_list.add_member(request.user, admin=True)
        return redirect('list-detail', pk=task_list.pk)

    open_tasks = Q(tasks__completed=False) & (
        Q(tasks__expires_at__isnull=True) | Q(tasks__expires_at__gt=timezone.now())
    )
    lists = _user_lists(request.user).annotate(open_count=Count('tasks', filter=open_tasks))
    return render(request, 'tasks/index.html', {'lists': lists, 'form': form})


def _render_detail(request, task_list, form=None, member_form=None):
    is_admin = task_list.is_admin(request.user)
    tasks = task_list.tasks.filter(parent__isnull=True).select_related('assignee').prefetch_related(
        Prefetch('subtasks', queryset=Task.objects.select_related('assignee').order_by('created_at', 'id'))
    )
    admin_ids = set(task_list.admins.values_list('pk', flat=True))
    return render(request, 'tasks/list_detail.html', {
        'list': task_list,
        'tasks': tasks,
        'is_admin': is_admin,
        'members': [(user, user.pk in admin_ids) for user in task_list.users.order_by('username')],
        'form': (form or TaskForm(task_list=task_list)) if is_admin else None,
        'member_form': (member_form or MemberForm()) if is_admin else None,
        # Lists that could be merged into this one: others you're an admin of.
        'mergeable': request.user.administered_lists.exclude(pk=task_list.pk) if is_admin else [],
    })


@login_required
def list_detail(request, pk):
    task_list = get_object_or_404(_user_lists(request.user), pk=pk)
    form = None
    if request.method == 'POST':
        require_admin(request.user, task_list, 'add tasks')
        form = TaskForm(request.POST, task_list=task_list)
        if form.is_valid():
            form.save()
            return redirect('list-detail', pk=pk)
    return _render_detail(request, task_list, form=form)


@login_required
@require_POST
def list_delete(request, pk):
    task_list = get_object_or_404(_user_lists(request.user), pk=pk)
    require_admin(request.user, task_list, 'delete the list')
    task_list.delete()
    return redirect('index')


@login_required
@require_POST
def list_rename(request, pk):
    task_list = get_object_or_404(_user_lists(request.user), pk=pk)
    require_admin(request.user, task_list, 'rename the list')
    name = request.POST.get('name', '').strip()[:200]
    if name:
        task_list.name = name
        task_list.save(update_fields=['name'])
    return redirect('list-detail', pk=pk)


@login_required
@require_POST
def list_merge(request, pk):
    """Merge another list (``other``) into this one."""
    task_list = get_object_or_404(_user_lists(request.user), pk=pk)
    other = get_object_or_404(_user_lists(request.user).exclude(pk=pk), pk=request.POST.get('other') or 0)
    require_admin(request.user, task_list, 'merge lists')
    require_admin(request.user, other, 'merge lists')
    task_list.merge(other)
    return redirect('list-detail', pk=pk)


@login_required
@require_POST
def member_add(request, pk):
    task_list = get_object_or_404(_user_lists(request.user), pk=pk)
    require_admin(request.user, task_list, 'manage members')
    form = MemberForm(request.POST)
    if not form.is_valid():
        return _render_detail(request, task_list, member_form=form)
    task_list.add_member(form.cleaned_data['who'], admin=form.cleaned_data['admin'])
    return redirect('list-detail', pk=pk)


@login_required
@require_POST
def member_remove(request, pk, user_id):
    task_list = get_object_or_404(_user_lists(request.user), pk=pk)
    require_admin(request.user, task_list, 'manage members')
    task_list.remove_member(get_object_or_404(task_list.users, pk=user_id))
    return redirect('list-detail', pk=pk)


@login_required
@require_POST
def task_toggle(request, pk):
    task = get_object_or_404(Task, pk=pk, list__users=request.user)
    task.completed = not task.completed
    task.save(update_fields=['completed'])
    return redirect('list-detail', pk=task.list_id)


@login_required
@require_POST
def task_claim(request, pk):
    """Take a task on, or let go of it if you already hold it."""
    task = get_object_or_404(Task, pk=pk, list__users=request.user)
    assignee = None if task.assignee == request.user else request.user
    check_task_update(request.user, task, {'assignee': assignee})
    task.assignee = assignee
    task.save(update_fields=['assignee'])
    return redirect('list-detail', pk=task.list_id)


@login_required
@require_POST
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk, list__users=request.user)
    require_admin(request.user, task.list, 'delete tasks')
    list_id = task.list_id
    task.delete()
    return redirect('list-detail', pk=list_id)
