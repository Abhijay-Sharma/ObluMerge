from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class RequestLog(models.Model):

    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    session_id = models.CharField(max_length=100, null=True, blank=True)

    app_name = models.CharField(max_length=100, null=True, blank=True)
    view_name = models.CharField(max_length=200, null=True, blank=True)

    path = models.CharField(max_length=300)
    method = models.CharField(max_length=10)
    user_agent = models.TextField(null=True, blank=True)
    status_code = models.IntegerField()

    redirect_to = models.CharField(max_length=300, null=True, blank=True)
    request_data = models.JSONField(null=True, blank=True)
    response_data = models.JSONField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)

    execution_time = models.FloatField(null=True, blank=True)

    query_count = models.IntegerField(null=True, blank=True)
    query_time = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.method} {self.path} {self.status_code}"