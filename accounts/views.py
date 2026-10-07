from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from rest_framework.authtoken.models import Token


@login_required
def account(request):
    token = Token.objects.filter(user=request.user).first()
    return render(request, 'accounts/account.html', {'token': token, 'api_keys': request.user.api_keys.all()})


@login_required
@require_POST
def regenerate_token(request):
    Token.objects.filter(user=request.user).delete()
    Token.objects.create(user=request.user)
    return redirect('account')


@login_required
@require_POST
def revoke_api_key(request, pk):
    get_object_or_404(request.user.api_keys, pk=pk).delete()
    return redirect('account')
