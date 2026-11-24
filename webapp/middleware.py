from django.http import HttpResponse

from .importers import MAX_UPLOAD_BYTES


def limit_import_size(get_response):
    # Run before CSRF middleware reads multipart bodies. Allow space for the form envelope.
    def middleware(request):
        if request.path_info == "/profile/import/" and request.method == "POST":
            length = request.META.get("CONTENT_LENGTH") or "0"
            if not length.isdecimal() or int(length) > MAX_UPLOAD_BYTES + 65536:
                return HttpResponse("This upload is too large. Choose a file up to 8 MiB.",
                                    status=413, content_type="text/plain")
        return get_response(request)
    return middleware
