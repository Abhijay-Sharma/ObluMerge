import time
from urllib import response
from django.urls import resolve
from django.db import connection
from requests import request
from .models import RequestLog


class RequestLoggingMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):

        # Skip static files
        if request.path.startswith("/static"):
            return self.get_response(request)

        start_time = time.time()
        request_data = {}

        if request.method == "POST":
            request_data = request.POST.dict()
        elif request.method == "GET":
            request_data = request.GET.dict()

        response = self.get_response(request)

        execution_time = time.time() - start_time
        response_data = None

        try:
            response_data = response.content[:1000].decode("utf-8")
        except:
            response_data = None

        # Resolve view
        try:
            resolver_match = resolve(request.path)
            view_name = resolver_match.view_name
            app_name = resolver_match.app_name
        except:
            view_name = None
            app_name = None

        # Query stats
        query_count = len(connection.queries)
        query_time = sum(float(q["time"]) for q in connection.queries)

        # Redirect detection
        redirect_to = None
        if response.status_code in (301, 302):
            redirect_to = response.get("Location")

        # User
        user = request.user if request.user.is_authenticated else None

        # Session
        session_id = request.session.session_key

        # IP and User Agent
        ip = request.META.get("REMOTE_ADDR")
        user_agent = request.META.get("HTTP_USER_AGENT")

        RequestLog.objects.create(
            user=user,
            session_id=session_id,
            app_name=app_name,
            view_name=view_name,
            user_agent=user_agent,  
            path=request.path,
            method=request.method,
            status_code=response.status_code,
            redirect_to=redirect_to,
            execution_time=execution_time,
            query_count=query_count,
            query_time=query_time,
            ip_address=ip,
            request_data=request_data,
            response_data=response_data
        )

        return response