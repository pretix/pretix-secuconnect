from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt


@xframe_options_exempt
def redirect_view(request, *args, **kwargs):
    return HttpResponse("hi there")


class ReturnView(View):
    def get(self, request, *args, **kwargs):
        return HttpResponse("hi there 2")

