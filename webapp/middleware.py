from django.http import HttpResponse

from .importers import MAX_UPLOAD_BYTES


def limit_import_size(get_response):
    # Run before CSRF middleware reads multipart bodies. Allow space for the form envelope.
    def middleware(request):
        if request.method == "POST" and request.path_info in ("/profile/import/", "/applications/review/"):
            length = request.META.get("CONTENT_LENGTH") or "0"
            limit = 2 * MAX_UPLOAD_BYTES + 262144 if request.path_info == "/applications/review/" else MAX_UPLOAD_BYTES + 65536
            if not length.isdecimal() or int(length) > limit:
                return HttpResponse("This upload is too large. Each PDF must be 8 MiB or smaller.",
                                    status=413, content_type="text/plain")
        return get_response(request)
    return middleware
