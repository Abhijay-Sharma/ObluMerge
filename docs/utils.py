from django.apps import apps
from django.urls import get_resolver


def get_models_data():

    all_models = apps.get_models()

    data = []

    for model in all_models:

        fields = []

        for field in model._meta.get_fields():

            field_info = {
                "name": field.name,
                "type": field.__class__.__name__,
                "relation": field.is_relation,
                "many_to_many": field.many_to_many,
                "many_to_one": field.many_to_one,
            }

            if field.is_relation:
                try:
                    field_info["related_model"] = str(field.related_model)
                except:
                    field_info["related_model"] = None

            fields.append(field_info)

        data.append({
            "app": model._meta.app_label,
            "model": model.__name__,
            "fields": fields
        })

    return data




def get_urls():

    resolver = get_resolver()

    url_patterns = []

    def extract(patterns, prefix=""):

        for p in patterns:

            if hasattr(p, "url_patterns"):
                extract(p.url_patterns, prefix + str(p.pattern))
            else:
                url_patterns.append({
                    "path": prefix + str(p.pattern),
                    "view": str(p.callback),
                    "name": p.name
                })

    extract(resolver.url_patterns)

    return url_patterns