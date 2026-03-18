
from django.shortcuts import render
from .utils import get_models_data, get_urls


def docs_home(request):

    models_data = get_models_data()
    urls = get_urls()

    return render(request, "docs/home.html", {
        "models": models_data,
        "urls": urls
    })

from django.apps import apps

def apps_overview(request):

    app_map = {}

    for model in apps.get_models():

        app = model._meta.app_label

        if app not in app_map:
            app_map[app] = set()

        for field in model._meta.get_fields():

            if field.is_relation and field.related_model:

                related_app = field.related_model._meta.app_label

                if related_app != app:
                    app_map[app].add(related_app)

    return render(request, "docs/apps.html", {"apps": app_map})

def app_detail(request, app_name):

    models = []

    for model in apps.get_models():
        if model._meta.app_label == app_name:
            models.append({
                 "name": model.__name__,
                 "app": model._meta.app_label
                })

    return render(request, "docs/app_detail.html", {
        "app": app_name,
        "models": models
    })

from django.apps import apps

def model_detail(request, app, model):

    model_class = apps.get_model(app, model)

    fields = []

    for field in model_class._meta.get_fields():

          # ❌ Skip reverse relations
        if field.auto_created and not field.concrete:
            continue

        field_data = {
            "name": field.name,
            "type": field.__class__.__name__,
            "is_relation": field.is_relation,
            "related_model": None
        }

        if field.is_relation and field.related_model:
            field_data["related_model"] = {
                "name": field.related_model.__name__,
                "app": field.related_model._meta.app_label
            }

        fields.append(field_data)

    context = {
        "model": {
            "name": model_class.__name__,
            "app": model_class._meta.app_label
        },
        "fields": fields
    }

    return render(request, "docs/model_detail.html", context)


def field_detail(request, app, model, field):

    model_class = apps.get_model(app, model)

    target_field = None

    for f in model_class._meta.get_fields():
        if f.name == field:
            target_field = f
            break

    used_in = []

    if target_field and target_field.is_relation:

        related_model = target_field.related_model

        for m in apps.get_models():

            for f in m._meta.get_fields():

                if f.is_relation and f.related_model == related_model:

                    used_in.append({
                        "model": {
                            "name": m.__name__,
                            "app": m._meta.app_label
                        },
                        "field": f.name
                    })

    context = {
        "field": {
            "name": target_field.name,
            "type": target_field.__class__.__name__,
        },
        "model": {
            "name": model_class.__name__,
            "app": model_class._meta.app_label
        },
        "used_in": used_in
    }

    return render(request, "docs/field_detail.html", context)


from collections import defaultdict
from django.urls import get_resolver
import re
import inspect


def urls_view(request):
    resolver = get_resolver()

    grouped = defaultdict(list)

    def is_valid(path):
        # remove regex/internal patterns
        return not re.search(r"\^|\$|\(\?P<", path)

    def extract(urlpatterns, prefix=""):
        for p in urlpatterns:
            if hasattr(p, "url_patterns"):
                extract(p.url_patterns, prefix + str(p.pattern))
            else:
                full_path = prefix + str(p.pattern)

                if not is_valid(full_path):
                    continue

                parts = full_path.strip("/").split("/")

                if not parts or parts[0] == "":
                    continue

                # smart grouping
                if parts[0] == "admin":
                    group = "admin"
                    subgroup = parts[1] if len(parts) > 1 else "root"
                else:
                    group = parts[0]
                    subgroup = None

                grouped[group].append({
                    "path": full_path,
                    "name": p.name,
                    "subgroup": subgroup
                })

    extract(resolver.url_patterns)

    return render(request, "docs/urls.html", {
        "groups": dict(grouped)
    })