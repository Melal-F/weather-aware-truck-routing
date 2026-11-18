import os
from pathlib import Path

from django.http import FileResponse, Http404, HttpResponse
from django.urls import include, path

from config.settings import FRONTEND_DIST


def _dist():
    if not FRONTEND_DIST:
        return None
    return Path(FRONTEND_DIST)


def index(_request):
    dist = _dist()
    page = dist / "index.html" if dist else None
    if page is None or not page.is_file():
        return HttpResponse(
            "API is running. Build the frontend to serve the map.",
            content_type="text/plain",
        )
    return FileResponse(page.open("rb"))


def asset(_request, name):
    dist = _dist()
    if dist is None:
        raise Http404
    root = (dist / "assets").resolve()
    target = (root / name).resolve()
    if os.path.commonpath([str(root), str(target)]) != str(root) or not target.is_file():
        raise Http404
    return FileResponse(target.open("rb"))


urlpatterns = [
    path("api/", include("routing.urls")),
    path("assets/<path:name>", asset),
    path("", index),
]
