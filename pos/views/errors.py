"""Friendly error pages -- users never see a raw traceback (section 22)."""

from django.shortcuts import render


def permission_denied(request, exception=None):
    return render(
        request,
        'pos/error.html',
        {
            'code': 403,
            'title': 'Access denied',
            'message': str(exception) or 'Your role does not permit this action.',
        },
        status=403,
    )


def page_not_found(request, exception=None):
    return render(
        request,
        'pos/error.html',
        {
            'code': 404,
            'title': 'Page not found',
            'message': 'The page you asked for does not exist or has been moved.',
        },
        status=404,
    )


def server_error(request):
    return render(
        request,
        'pos/error.html',
        {
            'code': 500,
            'title': 'Something went wrong',
            'message': 'The action could not be completed. No changes were saved.',
        },
        status=500,
    )
