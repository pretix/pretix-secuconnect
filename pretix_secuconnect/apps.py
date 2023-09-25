from django.utils.translation import gettext_lazy

from . import __version__

try:
    from pretix.base.plugins import PluginConfig
except ImportError:
    raise RuntimeError("Please use pretix 2.7 or above to run this plugin!")


class PluginApp(PluginConfig):
    default = True
    name = "pretix_secuconnect"
    verbose_name = "secuconnect"

    class PretixPluginMeta:
        name = gettext_lazy("secuconnect")
        author = "pretix team"
        description = gettext_lazy("Accept payments through secuconnect.")
        visible = True
        version = __version__
        category = "PAYMENT"
        compatibility = "pretix>=4.20.0"
        picture = "pretix_secuconnect/logo.png"

    def ready(self):
        from . import signals  # NOQA
