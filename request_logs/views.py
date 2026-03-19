from django.shortcuts import render
from .models import RequestLog
from django.shortcuts import get_object_or_404
from django.views.generic import ListView


def logs_dashboard(request):

    logs = RequestLog.objects.all().order_by("-created_at")

    user = request.GET.get("user")
    method = request.GET.get("method")
    status = request.GET.get("status")

    if user:
        logs = logs.filter(user__username=user)

    if method:
        logs = logs.filter(method=method)

    if status:
        logs = logs.filter(status_code=status)

    logs = logs[:500]

    return render(request,"request_logs/dashboard.html",{"logs":logs})

def log_detail(request, log_id):

    log = get_object_or_404(RequestLog, id=log_id)

    return render(
        request,
        "request_logs/log_detail.html",
        {"log": log}
    )

def session_timeline(request, session_id):

    logs = RequestLog.objects.filter(
        session_id=session_id
    ).order_by("created_at")

    return render(
        request,
        "request_logs/session_timeline.html",
        {"logs": logs}
    )


class LogsDashboardView(ListView):
    model = RequestLog
    template_name = "request_logs/dashboard.html"
    context_object_name = "logs"

    def get_queryset(self):
        queryset = RequestLog.objects.all().order_by("-created_at")

        user = self.request.GET.get("user")
        method = self.request.GET.get("method")
        status = self.request.GET.get("status")

        if user:
            queryset = queryset.filter(user__username=user)

        if method:
            queryset = queryset.filter(method=method)

        if status:
            queryset = queryset.filter(status_code=status)

        return queryset[:500]