from django import forms
from django.contrib.auth import get_user_model

from .models import List, Task


class ListForm(forms.ModelForm):
    class Meta:
        model = List
        fields = ['name']
        widgets = {'name': forms.TextInput(attrs={'placeholder': 'New list name'})}


class MemberChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        return user.get_full_name() or user.get_username()


class TaskForm(forms.ModelForm):
    assignee = MemberChoiceField(queryset=get_user_model().objects.none(), required=False, empty_label='Unassigned')

    class Meta:
        model = Task
        fields = ['title', 'description', 'expires_at', 'assignee', 'parent']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'What needs doing?'}),
            'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Details (optional)'}),
            'expires_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }
        labels = {'expires_at': 'Expires', 'parent': 'Subtask of', 'assignee': 'Assigned to'}

    def __init__(self, *args, task_list, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.list = task_list
        self.fields['parent'].queryset = task_list.tasks.filter(parent__isnull=True, completed=False)
        self.fields['assignee'].queryset = task_list.users.order_by('username')


class MemberForm(forms.Form):
    who = forms.CharField(label='Email or username', widget=forms.TextInput(attrs={'placeholder': 'Email or username'}))
    admin = forms.BooleanField(required=False, label='Admin (can add and assign tasks)')

    def clean_who(self):
        who = self.cleaned_data['who'].strip()
        users = get_user_model().objects.filter(is_active=True)
        matches = list(users.filter(email__iexact=who)[:2]) if '@' in who else list(users.filter(username=who))
        if not matches:
            raise forms.ValidationError('No Belltower account matches.')
        if len(matches) > 1:
            raise forms.ValidationError('More than one account uses that email; use the username.')
        return matches[0]
