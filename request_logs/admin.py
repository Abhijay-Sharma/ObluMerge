from django.contrib import admin
from .models import RequestLog


@admin.register(RequestLog)
class RequestLogAdmin(admin.ModelAdmin):

    list_display = (
        "created_at",
        "user",
        "method",
        "path",
        "status_code",
        "execution_time",
    )

    search_fields = ("path", "user__username")

    list_filter = ("method", "status_code")