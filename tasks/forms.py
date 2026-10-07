from django import forms

from .models import List, Task


class ListForm(forms.ModelForm):
    class Meta:
        model = List
        fields = ['name']
        widgets = {'name': forms.TextInput(attrs={'placeholder': 'New list name'})}


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['title', 'description', 'expires_at', 'parent']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'What needs doing?'}),
            'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Details (optional)'}),
            'expires_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }
        labels = {'expires_at': 'Expires', 'parent': 'Subtask of'}

    def __init__(self, *args, task_list, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.list = task_list
        self.fields['parent'].queryset = task_list.tasks.filter(parent__isnull=True, completed=False)
